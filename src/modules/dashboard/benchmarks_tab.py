"""Benchmarks: short CPU, memory and disk tests, compared with the last run."""
import logging
import os
import platform
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout,
                             QHeaderView, QLabel, QProgressBar, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from core.table_ui import set_role
from core.semantic_colors import semantic
from core.worker import Worker

from . import benchmarks as bm
from .tab_base import DashModule, DashTab

logger = logging.getLogger(__name__)

STEP_LABELS = ("CPU single-thread", "CPU multi-thread", "Memory", "Disk")
HEADERS = ("Test", "Result", "vs last run", "Detail")


class BenchmarksTab(DashTab):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker = None
        self._results: List[bm.BenchResult] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        top = QHBoxLayout()
        top.addWidget(QLabel("Disk test on:"))
        self._drive = QComboBox(self)
        self._drive.setMinimumWidth(180)
        top.addWidget(self._drive)
        self._quick = QCheckBox("Quick run", self)
        self._quick.setToolTip("Shorter tests (about 5 s total); less steady numbers")
        top.addWidget(self._quick)
        self._run_btn = QPushButton("Run benchmarks", self)
        self._run_btn.clicked.connect(self._run)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel)
        self._copy_btn = QPushButton("Copy results", self)
        self._copy_btn.clicked.connect(self._copy)
        for w in (self._run_btn, self._cancel_btn, self._copy_btn):
            top.addWidget(w)
        top.addStretch(1)
        layout.addLayout(top)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, len(STEP_LABELS))
        self._progress.setFixedHeight(8)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        self._table = QTableWidget(0, len(HEADERS), self)
        self._table.setHorizontalHeaderLabels(list(HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((230, 150, 110)):
            header.resizeSection(column, width)
        layout.addWidget(self._table, 1)

        self.status = QLabel(
            "Compare this machine with itself: run before and after a driver, BIOS "
            "setting or power-plan change. These are quick sanity checks, not a "
            "substitute for a published benchmark suite.", self)
        self.status.setWordWrap(True)
        set_role(self.status, "muted")
        layout.addWidget(self.status)
        self._trend_label = QLabel("", self)
        self._trend_label.setWordWrap(True)
        self._trend_label.hide()
        set_role(self._trend_label, "statusWarning")
        layout.addWidget(self._trend_label)
        self._fill_drives()
        self._show_last_run()

    # ---- setup --------------------------------------------------------------------

    def _fill_drives(self) -> None:
        from .folder_sizes import volumes
        for v in volumes():
            self._drive.addItem(f"{v['mount']}  ({v['fs']}, {v['free'] / 1024 ** 3:.0f} GB free)", v["mount"])
        if self._drive.count() == 0:
            self._drive.addItem("(no drive found)", None)

    def _history_dir(self) -> str:
        base = getattr(self._app, "app_data_dir", None) or os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), "WindowsTweaker")
        return os.path.join(base, "benchmarks")

    def set_app(self, app) -> None:
        super().set_app(app)
        self._show_last_run()

    def refresh(self) -> None:     # nothing to poll: benchmarks run on demand
        pass

    def _show_last_run(self) -> None:
        history = bm.load_history(self._history_dir())
        if history and not self._results:
            results = [bm.BenchResult(**r) for r in history[-1]["results"]]
            self._render(results, {}, f"Last run: {history[-1]['when']}")
        self._show_trend(history)

    def _show_trend(self, history: list) -> None:
        declines = bm.trend(history)
        if not declines:
            self._trend_label.hide()
            return
        lines = [f"{name}: " + " → ".join(f"{v:,.0f}" for v in d["values"]) +
                 f"  ({d['pct_change']:+.0f}% over the last {len(d['values']) - 1} runs)"
                 for name, d in declines.items()]
        self._trend_label.setText(
            "Declined in every one of the last few runs (not just one slow run):\n" +
            "\n".join(lines))
        self._trend_label.show()

    # ---- running ------------------------------------------------------------------

    def _run(self) -> None:
        mount = self._drive.currentData()
        if not mount:
            self.status.setText("No drive to test.")
            return
        # A per-user temp folder on the chosen drive, so the test needs no admin
        # rights and never writes into a system folder.
        folder = os.path.join(mount, "Users", os.environ.get("USERNAME", ""), "AppData", "Local", "Temp") \
            if mount.upper().startswith(os.environ.get("SystemDrive", "C:").upper()) else mount
        if not os.path.isdir(folder):
            folder = mount
        quick = self._quick.isChecked()
        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress.setValue(0)
        self.status.setText("Running… do not use the machine heavily until it finishes.")
        pool = self._pool()

        def work(worker):
            return bm.run_all(
                folder, cancelled=lambda: bool(worker and worker.is_cancelled),
                on_step=lambda label: worker and worker.signals.progress.emit(
                    STEP_LABELS.index(label) if label in STEP_LABELS else 0),
                quick=quick)

        if pool is None:
            self._done(work(None))
            return
        worker = Worker(work)
        worker.signals.progress.connect(self._step)
        worker.signals.result.connect(self._done)
        worker.signals.error.connect(self._failed)
        self._worker = worker
        self._workers.append(worker)
        pool.start(worker)

    def _step(self, index: int) -> None:
        self._progress.setValue(index)
        if 0 <= index < len(STEP_LABELS):
            self.status.setText(f"Running: {STEP_LABELS[index]}…")

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status.setText("Cancelling…")

    def _finish_ui(self) -> None:
        self._worker = None
        self._run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress.setValue(len(STEP_LABELS))

    def _failed(self, message) -> None:
        self._finish_ui()
        logger.error("benchmarks failed: %s", message)
        self.status.setText(f"The benchmarks failed: {message}")

    def _done(self, results) -> None:
        self._finish_ui()
        self._results = list(results)
        if not self._results:
            self.status.setText("Cancelled before any test finished.")
            return
        previous = bm.load_history(self._history_dir())
        change = bm.compare(previous, self._results)
        try:
            bm.save_run(self._history_dir(), self._results, platform.node())
            saved = "saved to the run history"
        except OSError as e:
            logger.warning("could not save benchmark history: %s", e)
            saved = f"NOT saved ({e})"
        self._render(self._results, change, f"Finished, {saved}.")
        self._show_trend(bm.load_history(self._history_dir()))

    # ---- results ------------------------------------------------------------------

    def _render(self, results, change, status: str) -> None:
        table = self._table
        table.setRowCount(len(results))
        for r, res in enumerate(results):
            failed = res.value == 0 and "could not run" in res.detail
            cells = [res.name, "failed" if failed else res.text(),
                     "" if res.name not in change else f"{change[res.name]:+.1f}%",
                     res.detail + ("   " + res.note if res.note else "")]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c in (1, 2):
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                table.setItem(r, c, item)
            delta = change.get(res.name)
            if delta is not None and abs(delta) >= 5:
                table.item(r, 2).setForeground(QColor(semantic("success" if delta > 0 else "error")))
            if failed:
                table.item(r, 1).setForeground(QColor(semantic("error")))
        self.status.setText(status)

    def _copy(self) -> None:
        lines = [f"{r.name}: {r.text()}  ({r.detail})" for r in self._results]
        if lines:
            QApplication.clipboard().setText("\n".join([f"{platform.node()} benchmarks", *lines]))
            self.status.setText("Copied.")

    def cancel_all(self) -> None:
        super().cancel_all()
        self._worker = None


class BenchmarksModule(DashModule):
    name = "Benchmarks"
    icon = "🏁"
    description = "CPU, memory and disk microbenchmarks, compared with your last run"
    tab_class = BenchmarksTab
