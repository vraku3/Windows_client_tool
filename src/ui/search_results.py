from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QHeaderView, QTableView, QVBoxLayout, QWidget

from core.search_provider import SearchResult
from core.table_ui import center_header


class SearchResultsTable(QWidget):
    """Table displaying search results with sortable columns."""

    result_activated = pyqtSignal(object)  # emits SearchResult on double-click

    COLUMNS = ["Time", "Source", "Type", "Summary", "Module"]

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(self.COLUMNS)

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._table.setColumnWidth(0, 150)
        self._table.setColumnWidth(1, 140)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 320)
        self._table.setColumnWidth(4, 140)
        center_header(self._table)
        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table)

        self._results: list[SearchResult] = []

    def set_results(self, results: list[SearchResult]) -> None:
        self._results = results
        self._model.removeRows(0, self._model.rowCount())
        for r in results:
            row = [
                QStandardItem(r.timestamp.strftime("%Y-%m-%d %H:%M:%S")),
                QStandardItem(r.source),
                QStandardItem(r.type),
                QStandardItem(r.summary),
                QStandardItem(r.source),  # Module = source for now
            ]
            for item in row:
                item.setEditable(False)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._model.appendRow(row)

    def clear(self) -> None:
        self._model.removeRows(0, self._model.rowCount())
        self._results.clear()

    def _on_double_click(self, index):
        row = index.row()
        if 0 <= row < len(self._results):
            self.result_activated.emit(self._results[row])
