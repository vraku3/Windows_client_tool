"""Table-cell helpers shared by the inventory panes (Qt side).

``QTableWidgetItem(int)`` is the *type* overload, which yields a blank cell,
and text sorting puts "9 B" above "10 GB".  ``numeric_item`` shows text but
sorts on a number.
"""
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

from core.semantic_colors import semantic


class _NumericItem(QTableWidgetItem):
    def __init__(self, text: str, value: float):
        super().__init__(text)
        self._value = value
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def __lt__(self, other) -> bool:
        if isinstance(other, _NumericItem):
            return self._value < other._value
        return super().__lt__(other)


def numeric_item(text: str, value: Optional[float]) -> QTableWidgetItem:
    """Displays ``text``, sorts on ``value``; None (unknown) sorts lowest."""
    return _NumericItem(text, float("-inf") if value is None else float(value))


_SEVERITY_COLOUR = {"error": "error", "warning": "warning", "info": "info", "ok": "success"}


def paint_severity(item: QTableWidgetItem, severity: str) -> None:
    """Colour a cell's text for a severity, from the theme's semantic palette."""
    meaning = _SEVERITY_COLOUR.get(severity)
    if meaning:
        item.setForeground(QBrush(QColor(semantic(meaning))))


def fit_table_height(table: QTableWidget, max_rows: int = 12) -> None:
    """Give a table inside a scroll area the height of its rows (capped), so
    it neither hoards the pane nor scrolls a three-row list."""
    rows = min(table.rowCount(), max_rows)
    height = table.horizontalHeader().height() + 2 * table.frameWidth()
    height += sum(table.rowHeight(r) for r in range(rows))
    table.setFixedHeight(max(height, table.horizontalHeader().height() + 30))
