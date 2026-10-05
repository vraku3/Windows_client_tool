"""Flight Recorder: record the vital signs, save them, scrub and replay them."""
import logging
import os
from datetime import datetime
from typing import List, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QComboBox, QDialog, QFileDialog, QGridLayout,
                             QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QSlider, QTableWidget, QVBoxLayout, QWidget)

from core.confirm import confirm_destructive
from core.table_ui import set_role
from core.semantic_colors import semantic

from . import flight_recorder as fr
from .tab_base import DashModule, DashTab, fmt_size, numeric_item

logger = logging.getLogger(__name__)

SPEEDS = (("1x", 1.0), ("4x", 4.0), ("16x", 16.0), ("64x", 64.0))


class TraceChart(QWidget):
    """One panel's history with a cursor. Click or drag to move the cursor."""

    cursor_requested = pyqtSignal(float)          # 0..1 across the recording

    def __init__(self, key: str, title: str, unit: str, ceiling: float = 0.0, parent=None) -> None:
        super().__init__(parent)
        self._key, self._title, self._unit, self._ceiling = key, title, unit, ceiling
        self._values: List[float] = []
        self._cursor = 0
        self.setMinimumHeight(86)
        self.setMouseTracking(False)

    def set_data(self, values: List[float], cursor: int) -> None:
        self._values, self._cursor = values, cursor
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
        top = max(self._ceiling, max(vals), 1e-9)
        step = w / (len(vals) - 1)
        line = QPainterPath(QPointF(0, h - 4 - (vals[0] / top) * (h - 22)))
        for i, v in enumerate(vals[1:], 1):
            line.lineTo(QPointF(i * step, h - 4 - (v / top) * (h - 22)))
        colour = QColor(semantic("info"))
        p.setPen(QPen(colour, 1.5))
        p.drawPath(line)
        x = self._cursor * step
        p.setPen(QPen(QColor(semantic("warning")), 1.5))
        p.drawLine(int(x), 0, int(x), h)


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
        traces = fr.list_saved_traces(self._dir)
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


class FlightTab(DashTab):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._trace = fr.Trace()
        self._recorder: Optional[fr.Recorder] = None
        self._sampler: Optional[fr.Sampler] = None
        self._eff = None
        self._nominal: List[int] = []
        self._top_source = None
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
        for w in (self._rec_btn, self._interval, self._open_btn, self._library_btn, self._save_btn):
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
        layout.addLayout(bar)

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
        self.status = QLabel("", self)
        set_role(self.status, "muted")
        layout.addWidget(self.status)

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

    # ---- recording --------------------------------------------------------------------

    def _toggle_record(self, on: bool) -> None:
        if on:
            self._begin()
        else:
            self._end()

    def _begin(self) -> None:
        import platform
        from core.procengine.snapshot import SnapshotSource
        from . import power
        self._play_btn.setChecked(False)
        interval = float(self._interval.currentData())
        self._recorder = fr.Recorder(interval, platform.node())
        self._trace, self._path, self._cursor = self._recorder.trace, None, 0
        self._top_source = SnapshotSource()
        freqs = power.read_frequencies()
        self._nominal = [f.max_mhz for f in freqs] if freqs else []
        try:
            self._eff = power.EffectiveFreq(len(self._nominal)) if self._nominal else None
        except OSError as e:
            logger.warning("clock counter unavailable for the recording: %s", e)
            self._eff = None
        self._sampler = fr.Sampler(top_process=self._top_process, clock=self._clock)
        self._interval.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._timer.start(int(interval * 1000))
        self.status.setText("Recording… you can switch tabs; it keeps going.")
        self.refresh()

    def _top_process(self):
        snap = self._top_source.read()
        rows = [(i.name, i.rates.cpu_percent or 0.0) for i in snap.by_pid.values() if i.pid != 0]
        return max(rows, key=lambda r: r[1]) if rows else ("", 0.0)

    def _clock(self):
        if not self._eff:
            return None
        values = self._eff.read(self._nominal)
        return sum(values) / len(values) if values else None

    def _take_sample(self):
        return self._sampler.sample() if self._sampler else None

    def _sample_taken(self, sample) -> None:
        self._busy = False
        if self._recorder is None:
            return
        if not self._recorder.add(sample):
            self._rec_btn.setChecked(False)
            self.status.setText("The recording is full (20,000 samples) and was stopped and saved.")
            return
        self._cursor = max(0, len(self._trace.samples) - 1)
        self._redraw()

    def _sample_failed(self, message) -> None:
        self._busy = False
        logger.warning("sample failed: %s", message)
        self.status.setText(f"A sample could not be read: {message}")

    def _end(self) -> None:
        self._timer.stop()
        if self._eff is not None:
            self._eff.close()
            self._eff = None
        recorder, self._recorder, self._sampler = self._recorder, None, None
        self._interval.setEnabled(True)
        self._open_btn.setEnabled(True)
        if recorder is None or not recorder.trace.samples:
            self.status.setText("Stopped; nothing was captured.")
            return
        try:
            self._path = fr.default_path(self._dir())
            fr.save(recorder.trace, self._path)
            self.status.setText(f"Saved {len(recorder.trace.samples):,} samples to {self._path}")
        except OSError as e:
            logger.warning("could not auto-save the recording: %s", e)
            self.status.setText(f"NOT saved ({e}). Use 'Save as…' to keep it.")
        self._redraw()

    def _dir(self) -> str:
        base = getattr(self._app, "app_data_dir", None) or os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), "WindowsTweaker")
        return os.path.join(base, "flight")

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
        for key, chart in self._charts.items():
            chart.set_data(self._trace.series(key), self._cursor)
        if n:
            peak = self._trace.peak("cpu")
            self._readout.setText(self._trace.describe(min(self._cursor, n - 1))
                                  + (f"\nCPU peaked at {peak[1]:.0f}% at +{int(self._trace.samples[peak[0]]['t'])}s"
                                     f" (top: {self._trace.samples[peak[0]].get('top', '?')})" if peak else ""))

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
    description = "Record the vital signs, save them, scrub and replay"
    tab_class = FlightTab

    def on_stop(self) -> None:
        # App shutdown is the one place a running recording MUST be finalised.
        if self._widget is not None and self._widget._recorder is not None:
            self._widget._rec_btn.setChecked(False)
        super().on_stop()
