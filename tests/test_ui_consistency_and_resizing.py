"""Tests for uniform GitHub-Dark theme palette, QPalette sync, and universal table/tree resizing."""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QHeaderView,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from core.semantic_colors import PANE_BACKGROUND, CHROME_PALETTES, set_theme
from core.theme_manager import ThemeManager
from ui.table_resizer import enhance_table_or_tree, install_table_resizer, uninstall_table_resizer

STYLES_DIR = str(Path(__file__).resolve().parent.parent / "src" / "ui" / "styles")


def test_theme_manager_syncs_qapplication_palette(qapp):
    tm = ThemeManager(STYLES_DIR)
    tm.apply_theme("dark")
    pal = qapp.palette()
    assert pal.color(QPalette.ColorRole.Window).name().lower() == PANE_BACKGROUND["dark"].lower()
    assert pal.color(QPalette.ColorRole.WindowText).name().lower() == CHROME_PALETTES["dark"]["text"].lower()
    assert pal.color(QPalette.ColorRole.Text).name().lower() == CHROME_PALETTES["dark"]["text"].lower()
    assert pal.color(QPalette.ColorRole.Text).name().lower() != "#000000"

    tm.apply_theme("light")
    pal_light = qapp.palette()
    assert pal_light.color(QPalette.ColorRole.Window).name().lower() == PANE_BACKGROUND["light"].lower()
    assert pal_light.color(QPalette.ColorRole.Text).name().lower() == CHROME_PALETTES["light"]["text"].lower()

    # Restore dark theme
    tm.apply_theme("dark")
    set_theme("dark")


def test_enhance_table_unlocks_horizontal_and_vertical_resize(qapp):
    table = QTableWidget(2, 3)
    table.setHorizontalHeaderLabels(["Name", "Status", "Details"])
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    table.horizontalHeader().resizeSection(1, 100)
    table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)

    table.setItem(0, 0, QTableWidgetItem("Service Alpha"))
    table.setItem(0, 1, QTableWidgetItem("Running"))
    table.setItem(0, 2, QTableWidgetItem("Longer details column"))

    enhance_table_or_tree(table)
    qapp.processEvents()

    h = table.horizontalHeader()
    v = table.verticalHeader()
    for col in range(3):
        assert h.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
    assert h.stretchLastSection() is True
    assert v.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive


def test_enhance_tree_unlocks_interactive_columns(qapp):
    tree = QTreeWidget()
    tree.setHeaderLabels(["Item", "Value"])
    tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    QTreeWidgetItem(tree, ["Root", "123"])

    enhance_table_or_tree(tree)
    qapp.processEvents()

    assert tree.header().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert tree.header().sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive
    assert tree.header().stretchLastSection() is True


def test_global_table_resizer_handles_scroll_area_and_table_on_show(qapp):
    install_table_resizer(qapp)
    try:
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        inner = QWidget()
        scroll.setWidget(inner)
        scroll.show()

        table = QTableWidget(1, 2)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().resizeSection(0, 80)
        table.horizontalHeader().resizeSection(1, 120)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.show()
        qapp.processEvents()

        assert scroll.widgetResizable() is True
        assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
        assert table.horizontalHeader().sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive
        assert table.verticalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
        scroll.close()
        table.close()
    finally:
        uninstall_table_resizer(qapp)


def test_sidebar_filter_and_collapsible_groups(qapp):
    from ui.sidebar_nav import SidebarNav
    nav = SidebarNav()
    nav.add_module("SYSTEM", "Hardware & Tuning", "🖥️", "Hardware & Tuning", False)
    nav.add_module("TOOLS", "System Utilities", "🧰", "System Utilities", False)
    nav.show()
    qapp.processEvents()

    nav._filter_edit.setText("hardware")
    qapp.processEvents()
    assert nav._btn_map["Hardware & Tuning"].isVisible()
    assert not nav._btn_map["System Utilities"].isVisible()

    nav._filter_edit.setText("")
    nav._toggle_group("TOOLS")
    qapp.processEvents()
    assert not nav._btn_map["System Utilities"].isVisible()
    nav._toggle_group("TOOLS")
    qapp.processEvents()
    assert nav._btn_map["System Utilities"].isVisible()
    nav.close()
