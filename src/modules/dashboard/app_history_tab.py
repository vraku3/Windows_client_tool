"""Task Manager's App history tab.

The question: what has cost the machine the most since each program
started -- cumulative CPU time, not the live rate the other tabs show.
Processes of one program sum into one row, so Chrome's twenty-six
processes read as "Google Chrome".

What is deliberately absent: the network, metered-network and tile columns
Task Manager shows for Store apps. Per-process network counters need a
kernel driver this tool does not ship, and tile updates exist only inside
Windows' private usage store -- a column of zeros would claim "used no
network", which is a lie. A status line says so instead.
"""
import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from core.semantic_colors import semantic
from core.worker import Worker, retire
from core.table_ui import set_role
from ui.table_style import apply_table_style
from .glance_support import summary, unreadable
from ui.chip_bar import ChipBar

from core.procengine.columns import fmt_cpu_time, fmt_count
from core.procengine.snapshot import SnapshotSource
from core.procengine.usage import app_usage

logger = logging.getLogger(__name__)

REFRESH_MS = 2000

_HEADERS = ("Name", "CPU time", "Processes", "Running since")


class AppHistoryTab(QWidget):
    """Cumulative per-program CPU usage. Owns its workers, per CLAUDE.md."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workers: list = []
        self._source = SnapshotSource()
        self._app = None
        self._busy = False
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._usage = []
        self._glance = summary(self, layout, [
            ("top", "Top CPU consumer", "Program with the highest cumulative CPU time since it started."),
            ("total", "Total CPU time", "Sum of CPU time for the running programs listed below."),
            ("apps", "Apps", "Running programs grouped by image path where readable, otherwise by name.")])
        note = QLabel(
            "Cumulative CPU time since each program started. Network "
            "per-program usage is not shown: Windows does not expose a "
            "per-process network byte counter without a kernel driver.")
        note.setWordWrap(True)
        set_role(note, "muted")
        layout.addWidget(note)

        top = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        top.addStretch(1)
        layout.addLayout(top)

        self._chips = ChipBar([("all", "All", 0), ("used", "Used CPU", 0),
                               ("multi", "Multiple processes", 0)], parent=self)
        self._chips.selection_changed.connect(lambda _keys: self._filter_usage())
        layout.addWidget(self._chips)
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSortingEnabled(False)
        apply_table_style(self._table, numeric_columns=(1, 2, 3))
        layout.addWidget(self._table, 1)

        self.status = QLabel("", self)
        layout.addWidget(self.status)

    def set_app(self, app) -> None:
        self._app = app

    def start(self) -> None:
        self.refresh()
        self._timer.start(REFRESH_MS)

    def stop(self) -> None:
        self._timer.stop()
        self.cancel_all()

    def cancel_all(self) -> None:
        retire(self._workers)
        self._busy = False

    # ---- reading --------------------------------------------------------

    def refresh(self) -> None:
        if self._busy:
            return
        pool = getattr(self._app, "thread_pool", None) if self._app else None
        if pool is None:
            self._apply(self._read())
            return
        self._busy = True
        worker = Worker(lambda _worker: self._read())
        worker.signals.result.connect(self._apply)
        worker.signals.error.connect(self._failed)
        worker.signals.cancelled.connect(self._on_cancelled)
        worker.signals.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        pool.start(worker)

    def _read(self):
        snapshot = self._source.read()
        return snapshot, app_usage(snapshot)

    def _apply(self, result) -> None:
        self._busy = False
        snapshot, usage = result
        self._usage = usage
        self._glance["top"].set(usage[0].name if usage else "None",
                                fmt_cpu_time(usage[0].cpu_ticks) if usage else "No running apps")
        self._glance["total"].set(fmt_cpu_time(sum(e.cpu_ticks for e in usage)), "running programs")
        self._glance["apps"].set(len(usage), "programs")
        self._chips.set_counts({"all": len(usage), "used": sum(e.cpu_ticks > 0 for e in usage),
                                "multi": sum(e.process_count > 1 for e in usage)})
        self._filter_usage()
        total = len(snapshot.by_pid)
        self.status.setText(f"{total:,} processes · "
                            f"{snapshot.refused:,} could not be read")

    def _filter_usage(self):
        key = next(iter(self._chips.selected()), "all")
        self._populate([e for e in self._usage if key == "all" or
                        (key == "used" and e.cpu_ticks > 0) or
                        (key == "multi" and e.process_count > 1)])

    def _on_cancelled(self):
        self._busy = False

    def _failed(self, message) -> None:
        self._busy = False
        unreadable(self._glance, str(message))
        logger.error("App history refresh failed: %s", message)
        self.status.setText(f"Could not read the process list: {message}")

    def _populate(self, usage) -> None:
        self._table.setRowCount(len(usage))
        for index, entry in enumerate(usage):
            values = (entry.name,
                      fmt_cpu_time(entry.cpu_ticks),
                      fmt_count(entry.process_count),
                      _running_since(entry.started_earliest))
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column >= 1:
                    item.setTextAlignment(
                        int(Qt.AlignmentFlag.AlignRight
                            | Qt.AlignmentFlag.AlignVCenter))
                self._table.setItem(index, column, item)
            # The busiest row gets the theme's warning tint so the eye
            # lands on the answer to the tab's question.
            if index == 0 and entry.cpu_ticks:
                from PyQt6.QtGui import QBrush, QColor

                brush = QBrush(QColor(semantic("warning")))
                for column in range(len(_HEADERS)):
                    self._table.item(index, column).setBackground(brush)


def _running_since(create_time: int) -> str:
    """When the program's first process started, or a dash for the
    unmeasurable (pid 0/4 report a bogus epoch)."""
    if not create_time:
        return "—"
    try:
        import datetime

        epoch = create_time / 10_000_000 - 11644473600
        return datetime.datetime.fromtimestamp(epoch).strftime(
            "%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "—"


def _fmt_name(info) -> str:
    return getattr(info.details, "description", None) or info.name
