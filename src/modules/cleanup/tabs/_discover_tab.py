"""Read-only review of caches not covered by the cleanup catalog."""
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QAbstractItemView, QHeaderView, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.long_op_pool import get_long_op_pool
from core.table_ui import NumericSortItem, set_role
from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.cleanup.cleanup_scanner import format_size
from modules.cleanup.cleanup_scanner.discover import find_uncovered_caches


class _DiscoverTab(QWidget):
    freed_bytes = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._pool = get_long_op_pool()
        layout = QVBoxLayout(self)
        description = QLabel(
            "Folders that look like caches but are not in the cleanup list yet. "
            "Nothing here is deleted - review each one; most are safe to clear "
            "from the app that owns them.")
        description.setWordWrap(True)
        set_role(description, "muted")
        layout.addWidget(description)
        row = QHBoxLayout()
        row.addWidget(QLabel("Minimum size"))
        self._minimum = QSpinBox()
        self._minimum.setRange(1, 1_000_000)
        self._minimum.setSuffix(" MB")
        self._minimum.setValue(20)
        row.addWidget(self._minimum)
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self._scan)
        row.addWidget(self._scan_btn)
        row.addStretch()
        layout.addLayout(row)
        self._status = QLabel("Click Scan to discover uncovered caches.")
        layout.addWidget(self._status)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Size", "Folder", "Name"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, 1)
        actions = QHBoxLayout()
        for text, callback in [("Open folder", self._open_folder), ("Copy path", self._copy_path)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)

    def _scan(self):
        if not widget_is_valid(self) or self._worker is not None:
            return
        minimum = self._minimum.value()
        self._scan_btn.setEnabled(False)
        self._status.setText("Scanning for uncovered caches...")

        def run(worker):
            return find_uncovered_caches(min_bytes=minimum * 2**20,
                                         cancelled=lambda: worker.is_cancelled)

        worker = Worker(run)

        def done(rows):
            if not widget_is_valid(self) or self._worker is not worker:
                return
            self._show_rows(rows, minimum)
            self._worker = None
            self._scan_btn.setEnabled(True)

        def error(reason):
            if not widget_is_valid(self) or self._worker is not worker:
                return
            self._status.setText(f"Scan failed: {reason}")
            self._worker = None
            self._scan_btn.setEnabled(True)

        worker.signals.result.connect(done)
        worker.signals.error.connect(error)
        self._worker = worker
        self._pool.start(worker)

    def _show_rows(self, result, minimum):
        if not widget_is_valid(self):
            return
        rows = result.candidates
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for index, candidate in enumerate(rows):
            self._table.setItem(index, 0, NumericSortItem(format_size(candidate.size), candidate.size))
            self._table.setItem(index, 1, QTableWidgetItem(candidate.path))
            self._table.setItem(index, 2, QTableWidgetItem(candidate.name))
        self._table.setSortingEnabled(True)
        self._table.sortItems(0, Qt.SortOrder.DescendingOrder)
        if rows:
            total = format_size(sum(row.size for row in rows))
            status = f"Found {len(rows)} folders, {total}, not covered by the cleanup list."
        else:
            status = f"Nothing uncovered above {minimum} MB."
        if result.unreadable:
            status += (f" {result.unreadable} protected folders were skipped"
                       " (normal: mostly Windows' own).")
        self._status.setText(status)

    def _selected_path(self):
        if not widget_is_valid(self):
            return None
        item = self._table.item(self._table.currentRow(), 1)
        return item.text() if item is not None else None

    def _open_folder(self):
        if not widget_is_valid(self):
            return
        path = self._selected_path()
        if path:
            try:
                os.startfile(path, "explore")
            except OSError as exc:
                self._status.setText(f"Open folder failed: {exc}")

    def _copy_path(self):
        if not widget_is_valid(self):
            return
        path = self._selected_path()
        if path:
            QApplication.clipboard().setText(path)

    def _cancel_all(self):
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
            if widget_is_valid(self):
                self._scan_btn.setEnabled(True)
                self._status.setText("Scan cancelled.")
