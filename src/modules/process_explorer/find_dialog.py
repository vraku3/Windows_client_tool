"""Find which process has a handle or DLL open -- Process Explorer's Ctrl+F.

The search itself is `procengine/findref.py`; this is the box, the results
table and -- most importantly -- the line underneath that says what could
not be searched.

That line is the point. The question people bring here is "why can't I
delete this file", and an empty result reads as "nothing has it open". If
twenty processes refused inspection, or the sweep ran out of its budget,
then the honest answer is "nothing I could look at has it open", and
acting on the other one wastes an afternoon.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QProgressBar, QPushButton,
                             QTableWidget, QVBoxLayout)

from core.table_ui import centered_item, center_header
from ui.help.dashboard_ui import apply_help

logger = logging.getLogger(__name__)

_HEADERS = ["Process", "PID", "Kind", "Type", "Name or path"]


class FindHandleDialog(QDialog):
    """Modeless search over every process's handles and modules."""

    _finished = pyqtSignal(object)
    _progressed = pyqtSignal(int, int)

    #: Emitted with a pid when a row is double-clicked, so the pane can
    #: select that process -- finding the holder is only useful if you can
    #: then go and look at it.
    pid_chosen = pyqtSignal(int)

    #: The dialog's own budget, far longer than the library default.
    #: This search is an explicit action with a progress bar and a Stop
    #: button, so the person watching it IS the budget -- the automatic
    #: one exists only to bound a runaway, and cutting a deliberate
    #: search off at 8s just makes it quietly answer "nothing found".
    BUDGET_SECONDS = 60.0

    def __init__(self, thread_pool=None, parent=None,
                 budget_seconds: float = BUDGET_SECONDS):
        super().__init__(parent)
        self._budget_seconds = budget_seconds
        self.setWindowTitle("Find handle or DLL")
        self.resize(900, 520)
        self._thread_pool = thread_pool
        self._workers: list = []
        self._cancelled = False
        self._running = False

        self._finished.connect(self._show_report)
        self._progressed.connect(self._show_progress)

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self._query = QLineEdit(self)
        self._query.setPlaceholderText(
            "Part of a file name, registry key or DLL path…")
        self._query.returnPressed.connect(self.start)
        row.addWidget(self._query, 1)
        self._handles = QCheckBox("Handles", self)
        self._handles.setChecked(True)
        self._modules = QCheckBox("DLLs", self)
        self._modules.setChecked(True)
        row.addWidget(self._handles)
        row.addWidget(self._modules)
        self._button = QPushButton("Search", self)
        self._button.clicked.connect(self._toggle)
        row.addWidget(self._button)
        layout.addLayout(row)

        self._progress = QProgressBar(self)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._table = QTableWidget(0, len(_HEADERS), self)
        self._table.setHorizontalHeaderLabels(_HEADERS)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.doubleClicked.connect(self._chose)
        layout.addWidget(self._table, 1)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        apply_help(self, 'Find Handle or DLL')

    # ---- running --------------------------------------------------------

    def _toggle(self) -> None:
        if self._running:
            self.cancel()
        else:
            self.start()

    def start(self) -> None:
        if self._running:
            return
        text = self._query.text().strip()
        if not text:
            self._status.setText("Enter something to search for.")
            return
        self._cancelled = False
        self._running = True
        self._button.setText("Stop")
        self._table.setRowCount(0)
        self._progress.setRange(0, 0)
        self._progress.show()
        self._status.setText("Searching every process…")

        want_handles = self._handles.isChecked()
        want_modules = self._modules.isChecked()

        def work(worker=None):
            from core.procengine.findref import find

            return find(
                text, handles=want_handles, modules=want_modules,
                should_stop=lambda: self._cancelled,
                budget_seconds=self._budget_seconds,
                progress=lambda done, total: self._progressed.emit(done,
                                                                   total))

        pool = self._thread_pool
        if pool is None:
            # No pool: run inline rather than not at all. The pane always
            # has one; a standalone dialog in a test may not.
            self._finished.emit(work())
            return

        from core.worker import Worker

        job = Worker(work)
        job.signals.result.connect(self._finished.emit)
        job.signals.error.connect(
            lambda message: self._failed(message))
        self._workers.append(job)
        pool.start(job)

    def cancel(self) -> None:
        self._cancelled = True
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()
        self._running = False
        self._button.setText("Search")
        self._progress.hide()

    @pyqtSlot(int, int)
    def _show_progress(self, done: int, total: int) -> None:
        if total:
            self._progress.setRange(0, total)
            self._progress.setValue(done)

    def _failed(self, message: str) -> None:
        self._running = False
        self._button.setText("Search")
        self._progress.hide()
        self._status.setText(f"The search failed: {message}")

    @pyqtSlot(object)
    def _show_report(self, report) -> None:
        self._running = False
        self._button.setText("Search")
        self._progress.hide()

        self._table.setRowCount(len(report.matches))
        for index, match in enumerate(report.matches):
            cells = [match.process, str(match.pid), match.kind,
                     match.type_name, match.detail]
            for column, text in enumerate(cells):
                item = centered_item(text)
                item.setData(Qt.ItemDataRole.UserRole, match.pid)
                self._table.setItem(index, column, item)

        # Never just "0 results". The summary carries what refused and
        # whether the sweep finished, because for this question those are
        # the difference between an answer and the appearance of one.
        text = report.summary()
        if report.note:
            text = f"{text}\n{report.note}"
        if not report.matches:
            text = f"Nothing matched. {text}"
        self._status.setText(text)

    def _chose(self, index) -> None:
        item = self._table.item(index.row(), 0)
        if item is not None:
            self.pid_chosen.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def done(self, result: int) -> None:
        self.cancel()
        super().done(result)
