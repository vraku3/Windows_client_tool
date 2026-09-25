import csv
import logging
from typing import List

from core.widget_life import widget_is_valid as _widget_is_valid

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.types import LogEntry
from core.table_ui import center_header

logger = logging.getLogger(__name__)

# Row colors by level
LEVEL_COLORS = {
    "Error": QColor("#6e1e1e"),
    "Warning": QColor("#805500"),
    "Critical": QColor("#8b0000"),
}


class LogTableWidget(QWidget):
    """Reusable table for displaying LogEntry items with sorting, coloring, and export."""

    row_selected = pyqtSignal(object)  # emits LogEntry
    row_double_clicked = pyqtSignal(object)  # emits LogEntry

    COLUMNS = ["Time", "Source", "Level", "Message"]

    def __init__(self, parent: QWidget = None, extra_columns: list = None):
        super().__init__(parent)
        self._entries: List[LogEntry] = []
        self._columns = list(self.COLUMNS)
        if extra_columns:
            self._columns.extend(extra_columns)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Table
        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(self._columns)

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        center_header(self._table)
        # The default 100px cut the Time column to "2026-09-25 ...".
        for col, name in enumerate(self._columns):
            width = {"Time": 150, "Source": 170, "Level": 70}.get(name)
            if width:
                self._table.setColumnWidth(col, width)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.clicked.connect(self._on_clicked)
        self._table.doubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table)

        # Status bar
        self._status = QLabel("0 entries")
        layout.addWidget(self._status)

    def set_entries(self, entries: List[LogEntry]) -> None:
        """Replace all entries in the table."""
        if not _widget_is_valid(self._status):
            return
        self._entries = list(entries)
        self._model.removeRows(0, self._model.rowCount())
        for entry in entries:
            row = self._make_row(entry)
            self._model.appendRow(row)
        self._status.setText(f"{len(entries)} entries")

    def append_entries(self, entries: List[LogEntry]) -> None:
        """Add entries to existing table data."""
        if not _widget_is_valid(self._status):
            return
        self._entries.extend(entries)
        for entry in entries:
            row = self._make_row(entry)
            self._model.appendRow(row)
        self._status.setText(f"{len(self._entries)} entries")

    def clear(self) -> None:
        if not _widget_is_valid(self._status):
            return
        self._model.removeRows(0, self._model.rowCount())
        self._entries.clear()
        self._status.setText("0 entries")

    def get_entries(self) -> List[LogEntry]:
        return list(self._entries)

    def _make_row(self, entry: LogEntry) -> list:
        time_item = QStandardItem(entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"))
        source_item = QStandardItem(entry.source)
        level_item = QStandardItem(entry.level)
        msg_item = QStandardItem(entry.message[:500])  # Truncate long messages

        row = [time_item, source_item, level_item, msg_item]

        # Color coding
        bg = LEVEL_COLORS.get(entry.level)
        if bg:
            brush = QBrush(bg)
            for item in row:
                item.setBackground(brush)
                item.setForeground(QBrush(QColor("white")))

        for item in row:
            item.setEditable(False)

        return row

    def _on_clicked(self, index):
        row = index.row()
        if 0 <= row < len(self._entries):
            self.row_selected.emit(self._entries[row])

    def _on_double_clicked(self, index):
        row = index.row()
        if 0 <= row < len(self._entries):
            self.row_double_clicked.emit(self._entries[row])

    def export_csv(self, file_path: str = None) -> None:
        if not file_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self.window() or self, "Export CSV", "", "CSV Files (*.csv)"
            )
        if not file_path:
            return
        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self._columns)
                for entry in self._entries:
                    writer.writerow([
                        entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        entry.source,
                        entry.level,
                        entry.message,
                    ])
            logger.info("Exported %d entries to %s", len(self._entries), file_path)
        except OSError as e:
            logger.error("Export failed: %s", e)

    def copy_selected_to_clipboard(self) -> None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return
        row = indexes[0].row()
        if 0 <= row < len(self._entries):
            entry = self._entries[row]
            text = f"{entry.timestamp} [{entry.level}] {entry.source}: {entry.message}"
            QApplication.clipboard().setText(text)
