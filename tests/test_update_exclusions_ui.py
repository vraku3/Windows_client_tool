from types import SimpleNamespace

from PyQt6.QtWidgets import QDialog, QMessageBox

from core.blocklist import add_exclusion, load_exclusions, save_exclusions
from modules.updates.exclusions_ui import ExclusionDialog, ExclusionsEditor
from modules.updates.updates_module import _AppUpdatesTab, _WinUpdatesTab
from modules.updates.winget_updater import AppUpdate, UpdateOutcome
from modules.updates.windows_updater import WindowsUpdate


class Config:
    def __init__(self, patterns):
        self.data = {"updates.blocklist_patterns": patterns}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        return True


def app(patterns=()):
    return SimpleNamespace(config=Config(list(patterns)))


def test_editor_migrates_legacy_and_styles_expired_auto(qapp):
    owner = app(["Legacy"])
    editor = ExclusionsEditor()
    editor.load(owner)
    assert editor.table.item(0, 0).text() == "Legacy"
    add_exclusion(owner, "Auto", "Cannot verify", "auto")
    entries = load_exclusions(owner)
    entries[0]["expires"] = "2000-01-01"
    save_exclusions(owner, entries)
    editor.load(owner)
    assert editor.table.item(0, 0).font().strikeOut()
    assert editor.table.item(1, 2).text() == "(auto)"
    assert editor.table.item(1, 1).text() == "Cannot verify"


def test_editor_edits_and_removes_without_losing_metadata(qapp, monkeypatch):
    owner = app()
    add_exclusion(owner, "Foo", "Pinned", "auto")
    original = load_exclusions(owner)[0]
    editor = ExclusionsEditor()
    editor.load(owner)
    editor.table.selectRow(0)

    def edit(dialog):
        dialog.pattern.setText("Foo.*")
        dialog.reason.setText("Waiting for QA")
        dialog.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExclusionDialog, "exec", edit)
    editor.edit(True)
    entry = load_exclusions(owner)[0]
    assert entry["pattern"] == "Foo.*"
    assert entry["reason"] == "Waiting for QA"
    assert (entry["added_by"], entry["added"], entry["origin"]) == (
        original["added_by"], original["added"], original["origin"])
    editor.table.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    editor.remove()
    assert load_exclusions(owner) == []


def test_dialog_dates_are_optional_and_round_trip(qapp):
    dialog = ExclusionDialog({"pattern": "Foo", "reason": "Pinned", "review_by": "2030-01-02"})
    dialog.accept()
    assert dialog.entry["review_by"] == "2030-01-02"
    assert dialog.entry["expires"] is None


def test_editor_adds_manual_metadata_and_reports_failed_save(qapp, monkeypatch):
    owner = app()
    editor = ExclusionsEditor()
    editor.load(owner)

    def add(dialog):
        dialog.pattern.setText("Foo.App")
        dialog.reason.setText("Waiting for QA")
        dialog.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExclusionDialog, "exec", add)
    monkeypatch.setattr(owner.config, "save", lambda: False)
    editor.edit()
    entry = load_exclusions(owner)[0]
    assert entry["pattern"] == "Foo.App"
    assert entry["reason"] == "Waiting for QA"
    assert entry["origin"] == "manual"
    assert entry["added_by"]
    assert "NOT saved" in editor.status.text()


def test_app_scan_retains_excluded_offers_and_reveals_them_after_removal(qapp):
    owner = app()
    add_exclusion(owner, "Foo", "Pinned")
    tab = _AppUpdatesTab()
    tab.set_app(owner)
    tab._on_updates([AppUpdate("Foo", "Foo.App", "1", "2", "winget"),
                     AppUpdate("Bar", "Bar.App", "1", "2", "winget")])
    assert [u.name for u in tab._updates] == ["Bar"]
    assert tab._excluded_offers.table.item(0, 1).text() == "Foo"
    assert tab._excluded_offers.table.item(0, 2).text() == "Pinned"
    save_exclusions(owner, [])
    tab.refresh_exclusions()
    assert [u.name for u in tab._updates] == ["Foo", "Bar"]
    assert tab._excluded_offers.table.rowCount() == 0


def test_auto_blocklist_keeps_outcome_reason(qapp):
    tab = _AppUpdatesTab()
    tab.set_app(app())
    from core.winget_list import DIFF_TECH
    tab._auto_blocklist_unfixable({"Foo.App": UpdateOutcome("Foo.App", DIFF_TECH, "detail")})
    entry = load_exclusions(tab.app)[0]
    assert entry["origin"] == "auto"
    assert entry["added_by"] == "WinClientTool"
    assert entry["reason"] == "not updatable via winget"


def test_windows_tab_shows_excluded_offer_reason(qapp, monkeypatch):
    monkeypatch.setattr("modules.updates.updates_module.is_reboot_pending", lambda: False)
    tab = _WinUpdatesTab()
    owner = app()
    add_exclusion(owner, "KB123", "Waiting for QA")
    tab.set_app(owner)
    tab._on_updates({"updates": [], "held": [], "excluded": [WindowsUpdate("KB123", "Fix", "", 0, "")]})
    assert tab._updates == []
    assert tab._excluded_offers.table.item(0, 0).text() == "Fix"
    assert tab._excluded_offers.table.item(0, 2).text() == "Waiting for QA"

def test_dialog_previews_which_offers_a_new_pattern_would_hide(qapp):
    offers = [AppUpdate("Foo Tool", "Foo.App", "1", "2", "winget"),
              AppUpdate("Bar Tool", "Bar.App", "1", "2", "winget")]
    dialog = ExclusionDialog(offers=offers)
    dialog.pattern.setText("Foo")
    assert "Would hide 1 current offer(s)" in dialog.preview.text()
    assert "Foo Tool" in dialog.preview.text()
    dialog.pattern.setText("Nothing")
    assert "Matches none" in dialog.preview.text()


def test_dialog_without_offers_says_so_instead_of_guessing(qapp):
    dialog = ExclusionDialog(offers=[])
    dialog.pattern.setText("Foo")
    assert "No current offers" in dialog.preview.text()


def test_editor_passes_scan_offers_into_the_preview(qapp, monkeypatch):
    editor = ExclusionsEditor()
    editor.load(app())
    editor.set_offers([("Foo Tool", "Foo.App")])
    seen = {}

    def add(dialog):
        dialog.pattern.setText("Foo")
        seen["text"] = dialog.preview.text()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(ExclusionDialog, "exec", add)
    editor.offers = [("Foo Tool", "Foo.App")]
    editor.edit()
    assert "Would hide 1 current offer(s)" in seen["text"]
