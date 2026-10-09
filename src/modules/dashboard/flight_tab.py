"""Flight Recorder: record the vital signs, save them, scrub and replay them."""
import logging
import os
from datetime import datetime
from typing import List, Optional

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout,
                             QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.confirm import confirm_destructive
from core.table_ui import set_role
from core.semantic_colors import semantic

from . import flight_recorder as fr
from .tab_base import DashModule, DashTab, fmt_size, numeric_item

logger = logging.getLogger(__name__)

SPEEDS = (("1x", 1.0), ("4x", 4.0), ("16x", 16.0), ("64x", 64.0), ("256x", 256.0))
HISTORY_SUBDIR = "history"
HISTORY_CONFIG_KEY = "modules.dashboard.flight.history"


class TraceChart(QWidget):
    """One panel's history with a cursor. Click or drag to move the cursor."""

    cursor_requested = pyqtSignal(float)          # 0..1 across the recording

    def __init__(self, key: str, title: str, unit: str, ceiling: float = 0.0, parent=None) -> None:
        super().__init__(parent)
        self._key, self._title, self._unit, self._ceiling = key, title, unit, ceiling
        self._values: List[Optional[float]] = []
        self._events: List[int] = []
        self._cursor = 0
        self.setMinimumHeight(76)
        self.setMouseTracking(False)

    def set_data(self, values: List[Optional[float]], cursor: int, events: Optional[List[int]] = None) -> None:
        """`values` may hold None: no reading, drawn as a gap. `events` are
        sample indexes to mark (programs that appeared mid-recording)."""
        self._values, self._cursor, self._events = values, cursor, events or []
        self.update()

    def _fraction(self, x: float) -> float:
        return min(1.0, max(0.0, x / max(1, self.width())))

    def mousePressEvent(self, event) -> None:
        self.cursor_requested.emit(self._fraction(event.position().x()))
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.cursor_requested.emit(self._fraction(event.position().x()))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        text = self.palette().text().color()
        faint = QColor(text)
        faint.setAlpha(50)
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, 25))
        p.setPen(QPen(faint, 1))
        for i in range(1, 4):
            p.drawLine(0, int(h * i / 4), w, int(h * i / 4))
        vals = self._values
        current = vals[self._cursor] if 0 <= self._cursor < len(vals) else None
        head = f"{self._title}   " + (f"{current:,.1f} {self._unit}" if current is not None else "no data")
        p.setPen(text)
        p.drawText(6, 14, head)
        if len(vals) < 2:
            return
        step = w / (len(vals) - 1)
        self._paint_line(p, vals, step, w, h)
        self._paint_events(p, step)
        x = self._cursor * step
        p.setPen(QPen(QColor(semantic("warning")), 1.5))
        p.drawLine(int(x), 0, int(x), h)

    def _paint_line(self, p: QPainter, vals, step: float, w: int, h: int) -> None:
        """The series, broken wherever a reading is missing (TMOG's rule: a
        gap, never a line drawn through samples that were not taken)."""
        known = [v for v in vals if v is not None]
        if not known:
            return
        top = max(self._ceiling, max(known), 1e-9)
        line = QPainterPath()
        pen_down = False
        for i, v in enumerate(vals):
            if v is None:
                pen_down = False
                continue
            point = QPointF(i * step, h - 4 - (v / top) * (h - 22))
            if pen_down:
                line.lineTo(point)
            else:
                line.moveTo(point)
            pen_down = True
        p.setPen(QPen(QColor(semantic("info")), 1.5))
        p.drawPath(line)

    def _paint_events(self, p: QPainter, step: float) -> None:
        if not self._events:
            return
        p.setPen(QPen(QColor(semantic("success")), 1))
        for i in self._events:
            x = int(i * step)
            p.drawLine(x, 18, x, 26)


_LIB_HEADERS = ("Name", "Started", "Machine", "Duration", "Samples", "Size", "Modified")
_LIB_NAME, _LIB_STARTED, _LIB_MACHINE, _LIB_DURATION, _LIB_SAMPLES, _LIB_SIZE, _LIB_MODIFIED = range(7)


def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s" if hours else f"{minutes}m{secs:02d}s"


class SavedTracesDialog(QDialog):
    """Browse every recording saved to disk -- the library `Open…`'s plain
    file picker does not give you: size and duration at a glance, newest
    first, so an old recording from 03:12 does not have to be guessed by
    filename alone."""

    def __init__(self, directory: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Saved recordings")
        self.resize(620, 360)
        self._dir = directory
        self.chosen_path: Optional[str] = None
        layout = QVBoxLayout(self)
        self._table = QTableWidget(0, len(_LIB_HEADERS), self)
        self._table.setHorizontalHeaderLabels(list(_LIB_HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.doubleClicked.connect(self._open)
        layout.addWidget(self._table, 1)
        self.status = QLabel("", self)
        set_role(self.status, "muted")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self._open_btn = QPushButton("Open", self)
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._open)
        self._delete_btn = QPushButton("Delete…", self)
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete)
        self._table.itemSelectionChanged.connect(self._selection_changed)
        row.addWidget(self._open_btn)
        row.addWidget(self._delete_btn)
        row.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        self.reload()

    def reload(self) -> None:
        # Manual recordings and the rolling history, newest first, in one list.
        traces = sorted(fr.list_saved_traces(self._dir)
                        + fr.list_saved_traces(os.path.join(self._dir, HISTORY_SUBDIR)),
                        key=lambda t: t.modified, reverse=True)
        table = self._table
        table.setSortingEnabled(False)
        table.setRowCount(len(traces))
        for row, t in enumerate(traces):
            table.setItem(row, _LIB_NAME, numeric_item(t.name, t.name))
            table.setItem(row, _LIB_STARTED, numeric_item(t.started, t.started or "—"))
            table.setItem(row, _LIB_MACHINE, numeric_item(t.machine, t.machine or "—"))
            dur_text = _fmt_duration(t.duration) if t.readable else "unreadable"
            table.setItem(row, _LIB_DURATION, numeric_item(t.duration or -1, dur_text))
            table.setItem(row, _LIB_SAMPLES, numeric_item(t.sample_count or 0,
                          str(t.sample_count) if t.sample_count is not None else "—"))
            table.setItem(row, _LIB_SIZE, numeric_item(t.size_bytes, fmt_size(t.size_bytes)))
            when = datetime.fromtimestamp(t.modified).strftime("%Y-%m-%d %H:%M:%S")
            table.setItem(row, _LIB_MODIFIED, numeric_item(t.modified, when))
            table.item(row, _LIB_NAME).setData(Qt.ItemDataRole.UserRole, t.path)
        table.setSortingEnabled(True)
        self.status.setText(f"{len(traces):,} recording(s) in {self._dir}")
        self._selection_changed()

    def _selected_path(self) -> Optional[str]:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        item = self._table.item(rows[0].row(), _LIB_NAME)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selection_changed(self) -> None:
        has = self._selected_path() is not None
        self._open_btn.setEnabled(has)
        self._delete_btn.setEnabled(has)

    def _open(self) -> None:
        path = self._selected_path()
        if path:
            self.chosen_path = path
            self.accept()

    def _delete(self) -> None:
        path = self._selected_path()
        if not path:
            return
        if not confirm_destructive(self, "Delete recording",
                                    f"Delete {os.path.basename(path)}?",
                                    irreversible=True):
            return
        try:
            os.remove(path)
        except OSError as e:
            logger.warning("could not delete %s: %s", path, e)
            self.status.setText(f"Could not delete: {e}")
            return
        self.reload()


#: The moment table: one column per ranking the sample keeps.
_MOMENT_COLS = (("cpu", "By CPU", "{:.1f}%"), ("mem", "By memory", "{:,.0f} MB"),
                ("gpu", "By GPU", "{:.1f}%"), ("io", "By disk", "{:.2f} MB/s"))
_MOMENT_ROWS = 5
#: Samples either side of the cursor whose "new program" events are listed.
NEW_WINDOW = 3


def flight_dir(app) -> str:
    base = getattr(app, "app_data_dir", None) or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "WindowsTweaker")
    return os.path.join(base, "flight")


def _config_get(app, key: str, default):
    config = getattr(app, "config", None)
    return config.get(key, default) if config is not None else default


def moment_rows(trace: fr.Trace, index: int) -> List[List[str]]:
    """The table under the cursor: who was using CPU, memory, GPU, disk then.
    A ranking the sample does not carry (an old recording, no GPU counters)
    says so in its first cell rather than looking like an idle machine."""
    top = trace.top_at(index)
    rows = []
    for r in range(_MOMENT_ROWS):
        row = []
        for key, _title, fmt in _MOMENT_COLS:
            items = top.get(key)
            if items is None:
                row.append("not recorded" if r == 0 else "")
            elif r < len(items):
                name, _pid, value = items[r]
                row.append(name if value is None else f"{name}   {fmt.format(value)}")
            else:
                row.append("")
        rows.append(row)
    return rows


def new_near(trace: fr.Trace, index: int, window: int = NEW_WINDOW) -> List[str]:
    lo, hi = max(0, index - window), min(len(trace.samples), index + window + 1)
    return sorted({name for s in trace.samples[lo:hi] for name in s.get("new", ())})


class HistoryService(QObject):
    """The always-on rolling history: one sample every HISTORY_INTERVAL on a
    worker, from app start, whether or not the tab is ever opened. Owned by
    FlightModule, never by the tab, for exactly that reason."""

    def __init__(self, app, directory: str) -> None:
        super().__init__()
        self._app, self.directory = app, directory
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._sources = None
        self._sampler = None
        self._history: Optional[fr.RollingHistory] = None
        self._busy = False
        self._workers: list = []
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._history is not None

    @property
    def path(self) -> Optional[str]:
        return self._history.path if self._history else None

    def start(self) -> None:
        import platform
        from .flight_sources import LiveSources
        if self.running:
            return
        self._sources = LiveSources()
        self._sampler = self._sources.sampler()
        self._history = fr.RollingHistory(self.directory, platform.node())
        self._timer.start(int(fr.HISTORY_INTERVAL * 1000))
        self._tick()

    def stop(self) -> None:
        self._timer.stop()
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()
        if self._history is not None:
            self._history.close()
        if self._sources is not None:
            self._sources.close()
        self._history = self._sources = self._sampler = None
        self._busy = False

    def _tick(self) -> None:
        from core.worker import Worker
        if self._busy or self._sampler is None:
            return
        sampler = self._sampler
        pool = getattr(self._app, "thread_pool", None)
        if pool is None:
            self._took(sampler.sample())
            return
        self._busy = True
        worker = Worker(lambda _w: sampler.sample())
        worker.signals.result.connect(self._took)
        worker.signals.error.connect(self._failed)
        worker.signals.cancelled.connect(self._released)
        self._workers = self._workers[-3:] + [worker]
        pool.start(worker)

    def _released(self) -> None:
        self._busy = False

    def _took(self, sample) -> None:
        self._busy = False
        if self._history is None:
            return
        try:
            self._history.add(sample)
            self.last_error = ""
        except OSError as e:
            self.last_error = str(e)
            logger.warning("rolling history could not be written: %s", e)

    def _failed(self, message) -> None:
        self._busy = False
        self.last_error = str(message)
        logger.warning("rolling history sample failed: %s", message)


class FlightTab(DashTab):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._trace = fr.Trace()
        self._recorder: Optional[fr.Recorder] = None
        self._sampler: Optional[fr.Sampler] = None
        self._sources = None
        self._history: Optional[HistoryService] = None
        self._cursor = 0
        self._path: Optional[str] = None
        self._timer.setInterval(1000)                       # sampling clock (recording only)
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(100)
        self._play_timer.timeout.connect(self._play_tick)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(self._build_toolbar())
        grid = QGridLayout()
        self._charts = {}
        for i, (key, title, unit) in enumerate(fr.PANELS):
            chart = TraceChart(key, title, unit, ceiling=100.0 if unit == "%" else 0.0)
            chart.cursor_requested.connect(self._cursor_requested)
            self._charts[key] = chart
            grid.addWidget(chart, i // 2, i % 2)
        layout.addLayout(grid, 1)
        self._slider = QSlider(Qt.Orientation.Horizontal, self)
        self._slider.setRange(0, 0)
        self._slider.valueChanged.connect(self._slider_moved)
        layout.addWidget(self._slider)
        self._readout = QLabel("Press Record to start capturing, or Open a saved .trace to look back.", self)
        set_role(self._readout, "mono")
        self._readout.setWordWrap(True)
        layout.addWidget(self._readout)
        layout.addWidget(self._build_moment_table())
        self._new_label = QLabel("", self)
        self._new_label.setWordWrap(True)
        layout.addWidget(self._new_label)
        self.status = QLabel("", self)
        set_role(self.status, "muted")
        layout.addWidget(self.status)

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._rec_btn = QPushButton("Record", self)
        self._rec_btn.setCheckable(True)
        self._rec_btn.toggled.connect(self._toggle_record)
        self._interval = QComboBox(self)
        for label, seconds in (("every 1 s", 1.0), ("every 2 s", 2.0), ("every 5 s", 5.0)):
            self._interval.addItem(label, seconds)
        self._open_btn = QPushButton("Open…", self)
        self._open_btn.clicked.connect(self._open)
        self._library_btn = QPushButton("Saved recordings…", self)
        self._library_btn.clicked.connect(self._open_library)
        self._save_btn = QPushButton("Save as…", self)
        self._save_btn.clicked.connect(self._save_as)
        self._history_box = QCheckBox("Keep a rolling history (7 days)", self)
        self._history_box.setToolTip(
            "Records every 5 s whenever the app is running, even with this tab closed, "
            "one file per day, kept 7 days (about 11 MB a day). Open them from Saved recordings.")
        self._history_box.toggled.connect(self._toggle_history)
        for w in (self._rec_btn, self._interval, self._open_btn, self._library_btn,
                  self._save_btn, self._history_box):
            bar.addWidget(w)
        bar.addStretch(1)
        self._play_btn = QPushButton("Play", self)
        self._play_btn.setCheckable(True)
        self._play_btn.toggled.connect(self._toggle_play)
        self._speed = QComboBox(self)
        for label, factor in SPEEDS:
            self._speed.addItem(label, factor)
        bar.addWidget(self._play_btn)
        bar.addWidget(self._speed)
        return bar

    def _build_moment_table(self) -> QTableWidget:
        table = QTableWidget(_MOMENT_ROWS, len(_MOMENT_COLS), self)
        table.setHorizontalHeaderLabels([title for _k, title, _f in _MOMENT_COLS])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setToolTip("Who was using the machine at the cursor")
        # Exactly its rows tall; the charts take the rest of the height.
        table.setFixedHeight(table.horizontalHeader().sizeHint().height()
                             + _MOMENT_ROWS * table.verticalHeader().defaultSectionSize()
                             + 2 * table.frameWidth() + 2)
        self._moment = table
        return table

    # ---- DashTab hooks ---------------------------------------------------------------

    def refresh(self) -> None:          # the sampling tick while recording
        if self._recorder is None or self._busy:
            return
        self._busy = True
        self.run(lambda _w: self._take_sample(), self._sample_taken, self._sample_failed)

    def start(self) -> None:            # activating the tab must not start recording
        pass

    def stop(self) -> None:             # nor does leaving it stop one: a recording is
        self._play_btn.setChecked(False)   # meant to keep running while you look elsewhere

    def cancel_all(self) -> None:
        if self._recorder is None:
            super().cancel_all()

    # ---- rolling history -------------------------------------------------------------

    def set_history_service(self, service: Optional[HistoryService]) -> None:
        self._history = service
        self._history_box.blockSignals(True)
        self._history_box.setChecked(bool(service and service.running))
        self._history_box.blockSignals(False)
        self._history_box.setEnabled(service is not None)

    def _toggle_history(self, on: bool) -> None:
        if self._history is None:
            return
        config = getattr(self._app, "config", None)
        if config is not None:
            config.set(HISTORY_CONFIG_KEY, bool(on))
        if on:
            self._history.start()
            self.status.setText(f"Rolling history on: writing to {self._history.path}")
        else:
            self._history.stop()
            self.status.setText("Rolling history off. Files already written are kept until they age out.")

    # ---- recording --------------------------------------------------------------------

    def _toggle_record(self, on: bool) -> None:
        if on:
            self._begin()
        else:
            self._end()

    def _begin(self) -> None:
        import platform
        from .flight_sources import LiveSources
        self._play_btn.setChecked(False)
        interval = float(self._interval.currentData())
        self._sources = LiveSources()
        self._sampler = self._sources.sampler()
        try:
            self._recorder = fr.Recorder(interval, platform.node(), fr.default_path(self._dir()))
        except OSError as e:          # cannot create the file: record in memory, say so
            logger.warning("could not create the recording file: %s", e)
            self._recorder = fr.Recorder(interval, platform.node())
        self._trace, self._path, self._cursor = self._recorder.trace, self._recorder.path, 0
        self._interval.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._timer.start(int(interval * 1000))
        self.status.setText("Recording, written to disk as it goes. You can switch tabs; it keeps going.")
        self.refresh()

    def _take_sample(self):
        return self._sampler.sample() if self._sampler else None

    def _sample_taken(self, sample) -> None:
        self._busy = False
        if self._recorder is None:
            return
        if not self._recorder.add(sample):
            self._rec_btn.setChecked(False)
            self.status.setText("The recording is full (20,000 samples) and was stopped.")
            return
        self._cursor = max(0, len(self._trace.samples) - 1)
        self._redraw()

    def _sample_failed(self, message) -> None:
        self._busy = False
        logger.warning("sample failed: %s", message)
        self.status.setText(f"A sample could not be read: {message}")

    def _end(self) -> None:
        self._timer.stop()
        recorder, self._recorder, self._sampler = self._recorder, None, None
        if self._sources is not None:
            self._sources.close()
            self._sources = None
        self._interval.setEnabled(True)
        self._open_btn.setEnabled(True)
        if recorder is None:
            return
        recorder.close()
        if not recorder.trace.samples:
            self.status.setText("Stopped; nothing was captured.")
        elif recorder.path:
            self.status.setText(f"Saved {len(recorder.trace.samples):,} samples to {recorder.path}")
        else:
            self.status.setText("NOT saved: the file could not be created. Use 'Save as…' to keep it.")
        self._redraw()

    def _dir(self) -> str:
        return flight_dir(self._app)

    # ---- files ---------------------------------------------------------------------

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open recording", self._dir(), "Trace files (*.trace)")
        if path:
            self.open_path(path)

    def _open_library(self) -> None:
        dialog = SavedTracesDialog(self._dir(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen_path:
            self.open_path(dialog.chosen_path)

    def open_path(self, path: str) -> bool:
        try:
            trace = fr.load(path)
        except (OSError, ValueError) as e:
            self.status.setText(f"Could not open {os.path.basename(path)}: {e}")
            return False
        self._trace, self._path, self._cursor = trace, path, 0
        self.status.setText(f"Opened {path}   ({len(trace.samples):,} samples, {trace.machine or 'unknown machine'}, "
                            f"started {trace.started})")
        self._redraw()
        return True

    def _save_as(self) -> None:
        if not self._trace.samples:
            self.status.setText("Nothing to save yet.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save recording", fr.default_path(self._dir()),
                                              "Trace files (*.trace)")
        if path:
            try:
                fr.save(self._trace, path)
                self._path = path
                self.status.setText(f"Saved to {path}")
            except OSError as e:
                self.status.setText(f"Could not save: {e}")

    # ---- scrubbing and replay ---------------------------------------------------------

    def _redraw(self) -> None:
        n = len(self._trace.samples)
        self._slider.blockSignals(True)
        self._slider.setRange(0, max(0, n - 1))
        self._slider.setValue(min(self._cursor, max(0, n - 1)))
        self._slider.blockSignals(False)
        events = [i for i, _name in self._trace.events()]
        for key, chart in self._charts.items():
            chart.set_data(self._trace.series(key), self._cursor, events if key == "cpu" else None)
        if not n:
            return
        index = min(self._cursor, n - 1)
        peak = self._trace.peak("cpu")
        self._readout.setText(self._trace.describe(index)
                              + (f"\nCPU peaked at {peak[1]:.0f}% at +{int(self._trace.samples[peak[0]]['t'])}s"
                                 f" (top: {self._trace.samples[peak[0]].get('top', '?')})" if peak else ""))
        self._fill_moment(index)

    def _fill_moment(self, index: int) -> None:
        for r, row in enumerate(moment_rows(self._trace, index)):
            for c, text in enumerate(row):
                self._moment.setItem(r, c, QTableWidgetItem(text))
        fresh = new_near(self._trace, index)
        self._new_label.setText(("Started around here (first time in this recording): " + ", ".join(fresh))
                                if fresh else "")

    def _cursor_requested(self, fraction: float) -> None:
        n = len(self._trace.samples)
        if n:
            self._cursor = int(round(fraction * (n - 1)))
            self._redraw()

    def _slider_moved(self, value: int) -> None:
        self._cursor = value
        self._redraw()

    def _toggle_play(self, on: bool) -> None:
        if on and self._recorder is None and self._trace.samples:
            if self._cursor >= len(self._trace.samples) - 1:
                self._cursor = 0
            self._play_btn.setText("Pause")
            self._play_timer.start()
        else:
            self._play_timer.stop()
            self._play_btn.setText("Play")
            if on:
                self._play_btn.setChecked(False)

    def _play_tick(self) -> None:
        step = float(self._speed.currentData()) * 0.1 / max(self._trace.interval, 0.1)
        self._play_pos = getattr(self, "_play_pos", 0.0) + step
        advance = int(self._play_pos)
        if advance:
            self._play_pos -= advance
            self._cursor = min(len(self._trace.samples) - 1, self._cursor + advance)
            self._redraw()
        if self._cursor >= len(self._trace.samples) - 1:
            self._play_btn.setChecked(False)


class FlightModule(DashModule):
    name = "Flight Recorder"
    icon = "🎞"
    description = "Record the vital signs and who used them; save, scrub, replay, keep days of history"
    tab_class = FlightTab

    def __init__(self) -> None:
        super().__init__()
        self.history: Optional[HistoryService] = None

    def on_start(self, app) -> None:
        super().on_start(app)
        self.history = HistoryService(app, os.path.join(flight_dir(app), HISTORY_SUBDIR))
        if _config_get(app, HISTORY_CONFIG_KEY, False):
            try:
                self.history.start()
            except OSError as e:   # e.g. the folder cannot be created; the tab says it is off
                logger.warning("rolling history could not start: %s", e)

    def create_widget(self):
        widget = super().create_widget()
        widget.set_history_service(self.history)
        return widget

    def on_stop(self) -> None:
        # App shutdown is the one place a running recording MUST be finalised.
        if self._widget is not None and self._widget._recorder is not None:
            self._widget._rec_btn.setChecked(False)
        super().on_stop()
        if self.history is not None:
            self.history.stop()
