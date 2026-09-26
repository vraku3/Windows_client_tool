"""The Everything tab renders an inventory, filters it, and keeps rows honest."""
from datetime import datetime

from modules.startup_manager import persistence as P
from modules.startup_manager import trust
from modules.startup_manager.unified_tab import UnifiedStartupTab, detail_text

NOW = datetime(2026, 9, 26, 12, 0, 0)


def _inventory():
    items = [
        P.Item("Alpha", r"C:\a\alpha.exe", "Run", "User", "HKCU\\Run", True, r"C:\a\alpha.exe", True,
               trust.SIGNED, None, "Acme"),
        P.Item("Bravo", r"C:\b\bravo.exe", "Run", "Machine", "HKLM\\Run", False, r"C:\b\bravo.exe", False,
               trust.UNKNOWN, None, None),
        P.Item("Charlie", r"C:\c\charlie.exe", "Service", "Machine", "charlie", True, r"C:\c\charlie.exe", True,
               trust.UNSIGNED, None, None, impact_ms=7000, impact_count=2),
    ]
    for i in items:
        P.assess(i, NOW)
    return P.Inventory(items, [])


def _tab(qapp):
    tab = UnifiedStartupTab(None)
    tab._inventory = _inventory()
    tab._render()
    return tab


def _names(tab):
    return [tab._table.item(r, 0).text() for r in range(tab._table.rowCount())]


def test_flagged_and_slow_rows_come_first(qapp):
    tab = _tab(qapp)
    try:
        assert _names(tab)[0] in ("Bravo", "Charlie")
        assert set(_names(tab)) == {"Alpha", "Bravo", "Charlie"}
    finally:
        tab.deleteLater()


def test_every_cell_of_every_row_is_filled_and_boot_impact_sorts_numerically(qapp):
    tab = _tab(qapp)
    try:
        for row in range(tab._table.rowCount()):
            for column in (0, 1, 2, 3, 5):
                assert tab._table.item(row, column).text(), (row, column)
        tab._table.sortByColumn(6, tab._table.horizontalHeader().sortIndicatorOrder())
        tab._table.sortByColumn(6, __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.SortOrder.DescendingOrder)
        assert tab._table.item(0, 0).text() == "Charlie"
    finally:
        tab.deleteLater()


def test_chip_and_search_filter(qapp):
    tab = _tab(qapp)
    try:
        tab._set_chip("Missing file")
        assert _names(tab) == ["Bravo"]
        tab._set_chip("Not signed")
        assert _names(tab) == ["Charlie"]
        tab._set_chip("All")
        tab._search.setText("acme")
        assert _names(tab) == ["Alpha"]
        assert "(3)" in tab._chips["All"].text() and "(1)" in tab._chips["Missing file"].text()
    finally:
        tab.deleteLater()


def test_row_selection_shows_detail_and_gates_buttons(qapp):
    tab = _tab(qapp)
    try:
        tab._table.selectRow(_names(tab).index("Alpha"))
        assert "Acme" in tab._detail.toPlainText()
        assert tab._disable_btn.isEnabled() and not tab._enable_btn.isEnabled()
        tab._table.selectRow(_names(tab).index("Bravo"))
        assert not tab._disable_btn.isEnabled() and not tab._enable_btn.isEnabled()   # machine scope
        assert "administrator" in tab._detail.toPlainText().lower()
    finally:
        tab.deleteLater()


def test_detail_explains_findings():
    item = _inventory().items[1]
    text = detail_text(item)
    assert "[MISSING]" in text and "orphaned" in text


def test_cancel_before_load_is_harmless(qapp):
    tab = UnifiedStartupTab(None)
    try:
        tab._cancel_all()
        assert tab._refresh_btn.isEnabled()
    finally:
        tab.deleteLater()
