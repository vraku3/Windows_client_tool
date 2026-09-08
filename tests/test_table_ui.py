from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem

from core import table_ui
from core.table_ui import center_header, centered_item, fit_last, fit_table, NumericSortItem


def test_centered_item_is_centered_and_compatible():
    item = centered_item("Hello")
    assert item.text() == "Hello"
    assert item.textAlignment() == Qt.AlignmentFlag.AlignCenter
    assert isinstance(item, QTableWidgetItem)


def test_centered_item_sortable_compares_case_insensitively():
    # 'apple' sorts before 'Zebra' only when comparison ignores case.
    a = centered_item("apple", sortable=True)
    b = centered_item("Zebra", sortable=True)
    assert a < b


def test_center_header_does_not_touch_resize_modes(qapp):
    table = QTableWidget(0, 2)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    center_header(table)
    assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert table.horizontalHeader().defaultAlignment() == Qt.AlignmentFlag.AlignCenter


def test_fit_table_sets_stretch_content_and_centred_header(qapp):
    table = QTableWidget(0, 4)
    fit_table(table, stretch=[0, 1], content=[2, 3])
    header = table.horizontalHeader()
    assert header.defaultAlignment() == Qt.AlignmentFlag.AlignCenter
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.ResizeToContents
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.ResizeToContents
    assert header.stretchLastSection() is False


def test_fit_last_stretches_only_the_last_column(qapp):
    table = QTableWidget(0, 3)
    fit_last(table)
    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.ResizeToContents
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch
    assert header.defaultAlignment() == Qt.AlignmentFlag.AlignCenter


def test_numeric_sort_orders_by_value_not_text(qapp):
    table = QTableWidget(3, 1)
    # Text order would put "10 GB" before "9 GB". Value order must not.
    table.setItem(0, 0, NumericSortItem("500 MB", 500_000_000))
    table.setItem(1, 0, NumericSortItem("9 GB", 9_000_000_000))
    table.setItem(2, 0, NumericSortItem("10 GB", 10_000_000_000))
    table.setSortingEnabled(True)
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    assert [table.item(r, 0).text() for r in range(3)] == \
        ["500 MB", "9 GB", "10 GB"]


def test_numeric_sort_degrades_to_text_against_a_plain_item(qapp):
    """A column mixing NumericSortItem with a bare QTableWidgetItem (e.g. a
    row whose size could not be measured) must not raise."""
    table = QTableWidget(2, 1)
    table.setItem(0, 0, NumericSortItem("12 MB", 12_000_000))
    table.setItem(1, 0, QTableWidgetItem("n/a"))
    table.setSortingEnabled(True)
    table.sortItems(0, Qt.SortOrder.AscendingOrder)  # must not raise


def test_save_and_restore_column_widths(qapp):
    table = QTableWidget(1, 3)
    table.setColumnWidth(0, 111)
    table.setColumnWidth(1, 222)
    saved = {}
    table_ui.save_column_widths(table, lambda k, v: saved.__setitem__(k, v), "test.widths")
    table2 = QTableWidget(1, 3)
    table_ui.restore_column_widths(
        table2, lambda k, default=None: saved.get(k, default), "test.widths")
    assert table2.columnWidth(0) == 111
    assert table2.columnWidth(1) == 222
