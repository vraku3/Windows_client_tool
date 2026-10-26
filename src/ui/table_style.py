"""Opt-in readable alignment and density for item and model-backed tables."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QStyledItemDelegate, QHeaderView


class _ScanDelegate(QStyledItemDelegate):
    def __init__(self, numeric_columns, parent):
        super().__init__(parent)
        self.numeric_columns = frozenset(numeric_columns)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        horizontal = Qt.AlignmentFlag.AlignRight if index.column() in self.numeric_columns else Qt.AlignmentFlag.AlignLeft
        option.displayAlignment = horizontal | Qt.AlignmentFlag.AlignVCenter


def apply_table_style(view, numeric_columns=(), *, size_columns=(), date_columns=(), row_height=30):
    """Align rendered cells, including future rows, without changing model data."""
    view.setProperty("scanTable", True)
    view.setAlternatingRowColors(True)
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
    view.setWordWrap(False)
    columns = set(numeric_columns) | set(size_columns) | set(date_columns)
    delegate = view.itemDelegate()
    if isinstance(delegate, _ScanDelegate):
        delegate.numeric_columns = frozenset(columns)
    else:
        view.setItemDelegate(_ScanDelegate(columns, view))
    header = view.horizontalHeader()
    header.setTextElideMode(Qt.TextElideMode.ElideRight)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setToolTip("Column headings. Drag a divider to resize; select a heading to sort when sorting is enabled.")
    vertical = view.verticalHeader()
    vertical.setDefaultSectionSize(row_height)
    vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    if not view.toolTip():
        view.setToolTip("Results table. Select a row to inspect it; numeric values are aligned for comparison.")
    view.style().unpolish(view)
    view.style().polish(view)
