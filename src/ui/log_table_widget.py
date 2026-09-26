import csv
import logging
from typing import Callable, List, Optional

from core.widget_life import widget_is_valid as _widget_is_valid

from PyQt6.QtCore import Qt, pyqtSignal
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

#: Widths the first four columns start with, so a timestamp is never cut to
#: "2026-09-26 ..." the way the default section size cut it.
_COLUMN_WIDTHS = (150, 230, 70)
_EXTRA_WIDTH = 64


class LogTableWidget(QWidget):
    """Reusable table for displaying LogEntry items with sorting, coloring, and export."""

    row_selected = pyqtSignal(object)  # emits LogEntry
    row_double_clicked = pyqtSignal(object)  # emits LogEntry

    COLUMNS = ["Time", "Source", "Level", "Message"]

    def __init__(self, parent: QWidget = None, extra_columns: list = None,
                 extra_values: Optional[Callable[[LogEntry], list]] = None):
        """`extra_columns` are extra headers placed AFTER Message; `extra_values`
        maps an entry to one value per extra column (an int sorts numerically)."""
        super().__init__(parent)
        self._entries: List[LogEntry] = []
        self._extra_values = extra_values
        self._n_extra = len(extra_columns or [])
        # Extras sit between Level and Message so Message stays last and stretches.
        self._columns = self.COLUMNS[:3] + list(extra_columns or []) + self.COLUMNS[3:]
        self._sized = False

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
        self._table.selectionModel().currentRowChanged.connect(self._on_current_changed)
        self._table.doubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table)
        self._apply_widths()

        # Status bar
        self._status = QLabel("0 entries")
        layout.addWidget(self._status)

    def _apply_widths(self) -> None:
        for col, width in enumerate(_COLUMN_WIDTHS):
            self._table.setColumnWidth(col, width)
        for col in range(3, 3 + self._n_extra):
            self._table.setColumnWidth(col, _EXTRA_WIDTH)

    def set_entries(self, entries: List[LogEntry]) -> None:
        """Replace all entries in the table."""
        if not _widget_is_valid(self._status):
            return
        self._entries = list(entries)
        self._fill(self._entries)
        self._status.setText(f"{len(entries)} entries")

    def append_entries(self, entries: List[LogEntry]) -> None:
        """Add entries to existing table data."""
        if not _widget_is_valid(self._status):
            return
        self._entries.extend(entries)
        self._fill(entries, replace=False)
        self._status.setText(f"{len(self._entries)} entries")

    def _fill(self, entries: List[LogEntry], replace: bool = True) -> None:
        # Sorting stays OFF while rows go in: with it on, every appended row is
        # re-sorted (quadratic for thousands of rows) and a row can move before
        # its remaining cells are set.
        header = self._table.horizontalHeader()
        section = header.sortIndicatorSection()
        order = header.sortIndicatorOrder()
        was_sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        try:
            if replace:
                self._model.removeRows(0, self._model.rowCount())
            for entry in entries:
                self._model.appendRow(self._make_row(entry))
        finally:
            self._table.setSortingEnabled(was_sorting)
            if was_sorting:
                self._model.sort(section, order)

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
        one_line = " ".join((entry.message or "").split())
        msg_item = QStandardItem(one_line[:500])  # Truncate long messages

        row = [time_item, source_item, level_item, *self._extra_items(entry), msg_item]
        # The entry rides on the row, so a sort can never separate the two.
        time_item.setData(entry, Qt.ItemDataRole.UserRole)

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

    def _extra_items(self, entry: LogEntry) -> list:
        if not self._n_extra:
            return []
        values = list(self._extra_values(entry)) if self._extra_values else []
        values += [""] * (self._n_extra - len(values))
        items = []
        for value in values[:self._n_extra]:
            item = QStandardItem()
            if isinstance(value, int):
                item.setData(value, Qt.ItemDataRole.EditRole)
            else:
                item.setText(str(value))
            items.append(item)
        return items

    def _entry_at(self, index) -> Optional[LogEntry]:
        if not index.isValid():
            return None
        first = self._model.item(index.row(), 0)
        entry = first.data(Qt.ItemDataRole.UserRole) if first is not None else None
        return entry if isinstance(entry, LogEntry) else None

    def selected_entry(self) -> Optional[LogEntry]:
        rows = self._table.selectionModel().selectedRows()
        return self._entry_at(rows[0]) if rows else None

    def _on_current_changed(self, current, _previous):
        entry = self._entry_at(current)
        if entry is not None:
            self.row_selected.emit(entry)

    def _on_double_clicked(self, index):
        entry = self._entry_at(index)
        if entry is not None:
            self.row_double_clicked.emit(entry)

    def _extra_text(self, entry: LogEntry) -> list:
        return [str(v) for v in (self._extra_values(entry) if self._extra_values else [])]

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
                        *self._extra_text(entry),
                        entry.message,
                    ])
            logger.info("Exported %d entries to %s", len(self._entries), file_path)
        except OSError as e:
            logger.error("Export failed: %s", e)

    def copy_selected_to_clipboard(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        extra = " ".join(self._extra_text(entry))
        text = f"{entry.timestamp} [{entry.level}] {entry.source}: {entry.message}"
        if extra:
            text += f"  ({extra})"
        QApplication.clipboard().setText(text)
