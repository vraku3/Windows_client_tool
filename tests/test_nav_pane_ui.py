"""Offscreen construction and preview checks with fake engine state."""
import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit, QSpinBox

from modules.nav_pane.engine.model import NavEntry
from modules.nav_pane.engine.operations import Operations
from modules.nav_pane.engine.registry import MemoryRegistry
from modules.nav_pane.nav_pane_module import NavPane, NavPaneModule
from modules.nav_pane.widgets import preview_rows


def test_widget_builds_with_tooltips(qapp, tmp_path):
    engine = Operations(MemoryRegistry(), tmp_path / "journal", account_check=lambda: "",
                        profile=str(tmp_path), root_paths=[], notify=lambda: None)
    pane = NavPane(engine, QThreadPool.globalInstance())
    assert pane.findChild(QLabel, "moduleIntro") is not None
    for widget in pane.findChildren((QAbstractButton, QComboBox, QLineEdit, QSpinBox)):
        assert widget.toolTip() or widget.parentWidget().toolTip(), type(widget).__name__
    pane.resize(1100, 1000)
    pane.show()
    qapp.processEvents()
    pane.cancel()
    pane.close()


def test_preview_places_entry_under_desktop_or_beside_home():
    for show_all in (False, True):
        rows = preview_rows("Aplicații", 48, show_all)
        selected = next(row for row in rows if row[2])
        assert selected == ("Aplicații", int(show_all), True)
        assert rows.index(selected) < next(i for i, row in enumerate(rows) if row[0] == "Home")
        if show_all:
            assert rows[0] == ("Desktop", 0, False)
        below = preview_rows("Last", 80, show_all)
        assert below[-1][0] == "Last"


def test_lifecycle_before_widget_is_safe():
    module = NavPaneModule()
    module.on_deactivate()
    module.on_activate()
    module.on_stop()


def make_pane(tmp_path):
    engine = Operations(MemoryRegistry(), tmp_path / "journal", account_check=lambda: "",
                        profile=str(tmp_path), root_paths=[], notify=lambda: None)
    return NavPane(engine, QThreadPool.globalInstance())


def test_unsigned_position_survives_name_icon_edits(qapp, tmp_path):
    pane = make_pane(tmp_path)
    entry = NavEntry("{11111111-1111-1111-1111-111111111111}", "Old", str(tmp_path),
                     str(tmp_path), sort_order=0xF1234567)
    pane._entries = [entry]
    pane.select(0)
    pane.display_name.setText("New")
    pane.icons.setCurrentIndex(1)
    assert pane.order() == entry.sort_order
    pane.custom.setValue(81)
    assert pane.order() == 81
    pane.cancel()
    pane.close()


def test_selected_target_and_resolved_folder_are_separate(qapp, tmp_path):
    pane = make_pane(tmp_path)
    pane._entries = [NavEntry("{11111111-1111-1111-1111-111111111111}", "Apps",
                              r"C:\\User\\Apps", r"C:\\Cloud\\Apps", junction=r"C:\\User\\Apps")]
    pane.select(0)
    assert pane.path.text() == r"C:\\Cloud\\Apps"
    assert r"C:\\User\\Apps" in pane.target_info.text()
    assert r"C:\\Cloud\\Apps" in pane.target_info.text()
    pane.cancel()
    pane.close()


def test_mutation_clears_old_error_and_preserves_success_on_refresh(qapp, tmp_path, monkeypatch):
    pane = make_pane(tmp_path)
    pane._refusal = ""
    pane._action_message = "Could not complete: previous failure"
    monkeypatch.setattr(pane, "refresh", lambda: None)
    pane.run(lambda: None, mutation=True)
    assert pane._action_message == ""
    pane.pool.waitForDone(5000)
    qapp.processEvents()
    assert "success" in pane.status.text().lower()
    context = ([], False, [], "", {"entries": {}, "transactions": {}})
    pane.set_context(context)
    assert "success" in pane.status.text().lower()
    pane.cancel()
    pane.close()


def test_recovery_confirmation_identifies_entry_and_operation(qapp, tmp_path, monkeypatch):
    pane = make_pane(tmp_path)
    entry = NavEntry("{11111111-1111-1111-1111-111111111111}", "Apps", r"C:\\Link", r"C:\\Cloud")
    pane._selected = entry
    data = {"entries": {}, "transactions": {entry.guid: {
        "operation": "edit", "entry": {"name": "Apps", "resolved_target": r"C:\\Cloud", "junction": r"C:\\Link"}}}}
    monkeypatch.setattr(pane.engine.journal, "read", lambda: data)
    seen = []
    monkeypatch.setattr(pane, "confirm", lambda title, text: seen.append(text) or False)
    pane.repair(False)
    assert all(value in seen[0] for value in ("Apps", "edit", r"C:\\Cloud", r"C:\\Link"))
    pane.cancel()
    pane.close()


@pytest.mark.parametrize("action", ["remove", "repair", "toggle_show_all", "revert_setting",
                                   "repair_setting", "verify", "open_folder"])
def test_each_action_replaces_stale_error(qapp, tmp_path, monkeypatch, action):
    from modules.nav_pane import nav_pane_module
    from modules.nav_pane.engine.model import Kind, Verification, VerifyResult
    pane = make_pane(tmp_path)
    pane._refusal = ""
    pane._selected = NavEntry("{11111111-1111-1111-1111-111111111111}", "Apps", str(tmp_path),
                              str(tmp_path), kind=Kind.HANDMADE)
    pane._action_message = "Could not complete: previous failure"
    monkeypatch.setattr(pane, "refresh", lambda: None)
    monkeypatch.setattr(pane, "confirm", lambda *args: True)
    for method in ("remove_entry", "repair", "set_show_all_folders", "revert_show_all_folders"):
        monkeypatch.setattr(pane.engine, method, lambda *args: None)
    monkeypatch.setattr(pane.engine, "verify", lambda *args: VerifyResult(Verification.VERIFIED, "Confirmed"))
    monkeypatch.setattr(nav_pane_module, "shell_launch_folder", lambda *args: "Opened folder")
    arguments = {"repair": (False,), "toggle_show_all": (True,)}
    getattr(pane, action)(*arguments.get(action, ()))
    assert pane._action_message == ""
    pane.pool.waitForDone(5000)
    qapp.processEvents()
    assert "previous failure" not in pane.status.text()
    assert pane._action_message
    pane.cancel()
    pane.close()


def test_saving_name_icon_preserves_unsigned_registry_position(qapp, tmp_path, monkeypatch):
    from modules.nav_pane.engine.model import CLSID
    pane = make_pane(tmp_path)
    guid = pane.engine.add_entry(str(tmp_path), "Old", 0xFFFFFFFF)
    pane._entries = pane.engine.list_entries()
    pane.select(0)
    pane.display_name.setText("New")
    pane.icons.setCurrentIndex(1)
    pane._refusal = ""
    pane._valid = True
    monkeypatch.setattr(pane, "run", lambda function, *args: function())
    pane.save()
    assert pane.engine.registry.read(CLSID + "\\" + guid, "SortOrderIndex") == (0xFFFFFFFF, 4)
    assert pane.engine.list_entries()[0].name == "New"
    pane.cancel()
    pane.close()
