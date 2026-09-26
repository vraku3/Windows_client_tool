"""Software Inventory pane, driven through the real widgets."""
import pytest

from modules.software_inventory import software_module as sm
from modules.software_inventory.software_reader import SoftwareEntry


def _e(name, version="1.0", **kw):
    kw.setdefault("publisher", "Pub")
    return SoftwareEntry(name=name, version=version, install_date=kw.pop("install_date", ""),
                         size_mb="", type_=kw.pop("type_", "64-bit"), source="registry", **kw)


ENTRIES = [
    _e("Zeta", "3.0"), _e("Alpha", "1.0"),
    _e("Microsoft Visual C++ 2010 x64 Redistributable - 10.0.40219", "10.0.40219"),
    _e("Hidden", "1", system_component=True),
    _e("Dup", "1"), _e("Dup", "2"),
]


@pytest.fixture
def pane(qapp):
    p = sm._SoftwarePane()
    p.set_entries(ENTRIES)
    return p


def _names(p):
    return [p.table.item(r, 0).text() for r in range(p.table.rowCount())]


def test_default_chip_hides_system_components(pane):
    assert "Hidden" not in _names(pane)
    assert len(_names(pane)) == 5


def test_chip_buttons_show_live_counts(pane):
    assert pane._chip_buttons["All"].text() == "All (6)"
    assert pane._chip_buttons["System components"].text() == "System components (1)"
    assert pane._chip_buttons["End of life"].text() == "End of life (1)"


def test_clicking_a_chip_filters(pane):
    pane._chip_buttons["Duplicates"].click()
    assert _names(pane) == ["Dup"]
    pane._chip_buttons["All"].click()
    assert len(_names(pane)) == 6


def test_search_filters_and_combines_with_chip(pane):
    pane.filter_edit.setText("visual")
    assert len(_names(pane)) == 1
    pane.filter_edit.setText("")
    pane._chip_buttons["Runtimes"].click()
    pane.filter_edit.setText("zeta")
    assert _names(pane) == []


def test_every_row_keeps_its_own_cells_after_sorting(pane):
    pane.table.sortByColumn(1, pane.table.horizontalHeader().sortIndicatorOrder())
    for r in range(pane.table.rowCount()):
        row = pane.table.item(r, 0).data(sm.Qt.ItemDataRole.UserRole)
        assert pane.table.item(r, 0).text() == row.entry.name
        assert pane.table.item(r, 1).text() == row.entry.version


def test_default_order_is_ascending_by_name(pane):
    names = [n.lower() for n in _names(pane)]
    assert names == sorted(names)


def test_selection_survives_a_refresh(pane):
    for r in range(pane.table.rowCount()):
        if pane.table.item(r, 0).text() == "Zeta":
            pane.table.selectRow(r)
    assert pane.selected_row().entry.name == "Zeta"
    pane.set_entries(list(ENTRIES))               # what the 2-minute timer does
    assert pane.selected_row() is not None and pane.selected_row().entry.name == "Zeta"


def test_detail_and_button_states_follow_selection(pane):
    assert not pane.uninstall_btn.isEnabled() and not pane.copy_code_btn.isEnabled()
    pane.set_entries([_e("Tool", "1", uninstall_string="x.exe", windows_installer=True,
                         product_code="{33333333-3333-3333-3333-333333333333}")])
    pane.table.selectRow(0)
    assert pane.uninstall_btn.isEnabled() and pane.copy_code_btn.isEnabled()
    assert "msiexec /x {33333333" in pane.detail.toPlainText()


def test_uninstall_asks_first_and_declining_launches_nothing(pane, monkeypatch):
    pane.set_entries([_e("Tool", "1", uninstall_string="x.exe")])
    pane.table.selectRow(0)
    monkeypatch.setattr(sm, "confirm_destructive", lambda *a, **k: False)
    monkeypatch.setattr(sm.subprocess, "Popen", lambda *a, **k: pytest.fail("launched without consent"))
    pane._uninstall()


def test_uninstall_with_consent_runs_the_recorded_command(pane, monkeypatch):
    pane.set_entries([_e("Tool", "1", uninstall_string="x.exe /S")])
    pane.table.selectRow(0)
    monkeypatch.setattr(sm, "confirm_destructive", lambda *a, **k: True)
    ran = []
    monkeypatch.setattr(sm.subprocess, "Popen", lambda cmd, **k: ran.append(cmd))
    pane._uninstall()
    assert ran == ["x.exe /S"]
    assert "Refresh" in pane.status.text()


def test_copy_markdown_uses_the_visible_rows(pane, qapp):
    pane._chip_buttons["Duplicates"].click()
    pane._copy_markdown()
    text = qapp.clipboard().text()
    assert "Dup" in text and "Zeta" not in text


def test_winget_result_marks_rows_and_a_failure_is_disclosed(pane):
    pane._on_winget(({"zeta": "4.0"}, ""))
    assert pane._chip_buttons["Updates available"].text() == "Updates available (1)"
    pane._on_winget((None, "winget not found."))
    assert "did not return" in pane.status.text()
    assert pane._winget is None and pane._winget_error
