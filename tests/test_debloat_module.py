"""DebloatToolsModule's tweaks tables: the five-value status vocabulary,
shown with a reason, is what this file pins down. Nothing here touches a
real machine -- TweakEngine.detect is monkeypatched throughout.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt

from modules.debloat import debloat_module as dm
from modules.debloat import debloat_presets as dp
from modules.tweaks import tweak_engine as te


class _FakeBackup:
    def create_restore_point(self, label, module):
        return "rp-fake"

    def record_steps(self, *a, **k):
        pass


class _FakeApp:
    def __init__(self):
        self.backup = _FakeBackup()
        self.thread_pool = None


def _module():
    mod = dm.DebloatToolsModule()
    mod.on_start(_FakeApp())
    mod.create_widget()
    return mod


def _tweak(id_, status, reason):
    return {"id": id_, "name": f"Tweak {id_}", "category": "Privacy",
            "risk": "Low"}, te.DetectionResult(status=status, reason=reason)


def test_all_five_statuses_render_as_five_distinct_labels(monkeypatch):
    mod = _module()
    fakes = [
        _tweak("a", te.APPLIED, "the value matches"),
        _tweak("b", te.NOT_APPLIED, "the key does not exist"),
        _tweak("c", te.PARTIAL, "3 of 5 steps are in place"),
        _tweak("d", te.NOT_APPLICABLE, "this edition has no Group Policy"),
        _tweak("e", te.UNKNOWN, "access denied reading the key"),
    ]
    tweaks = [t for t, _ in fakes]
    results = {t["id"]: r for t, r in fakes}
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, tweak: results[tweak["id"]])

    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")

    labels = {table.item(r, 4).text() for r in range(table.rowCount())}
    assert labels == {
        "● Applied", "○ Not Applied", "◑ Partially Applied",
        "– Not Applicable", "❓ Unknown",
    }


def test_every_row_carries_its_reason_as_a_tooltip(monkeypatch):
    mod = _module()
    tweak, result = _tweak("a", te.PARTIAL, "3 of 5 steps are in place")
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: [tweak])
    monkeypatch.setattr(te.TweakEngine, "detect", lambda self, t: result)

    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    assert table.item(0, 4).toolTip() == "3 of 5 steps are in place"


def test_a_bare_status_with_no_reason_still_shows_something(monkeypatch):
    """The bug this whole task exists to prevent: a status with nothing
    behind it. Never blank."""
    mod = _module()
    tweak, result = _tweak("a", te.UNKNOWN, "")
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: [tweak])
    monkeypatch.setattr(te.TweakEngine, "detect", lambda self, t: result)

    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    assert table.item(0, 4).toolTip()  # non-empty


def test_preset_button_checks_exactly_what_the_json_file_selects(
        monkeypatch):
    mod = _module()
    tweaks = [{"id": "disable_cortana", "name": "Disable Cortana",
              "category": "Privacy", "risk": "Low"},
             {"id": "disable_telemetry", "name": "Disable Telemetry",
              "category": "Telemetry", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")

    monkeypatch.setattr(dp, "load_preset",
                        lambda name: {"tweaks": {"Privacy": ["*"]}})

    mod._on_preset("privacy", "tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    checked = {table.item(r, 0).data(Qt.ItemDataRole.UserRole)
              for r in range(table.rowCount())
              if table.item(r, 0).checkState() == Qt.CheckState.Checked}
    assert checked == {"disable_cortana"}


def test_apps_tab_has_preset_buttons_that_check_the_right_packages(
        monkeypatch):
    mod = _module()
    monkeypatch.setattr(
        mod, "_load_debloat_entries",
        lambda: {"remove_bing_weather": {
            "id": "remove_bing_weather", "package": "Microsoft.BingWeather",
            "name": "Bing Weather", "category": "Bing Apps"}})
    mod._populate_apps_table(["Microsoft.BingWeather"])

    monkeypatch.setattr(
        dp, "load_preset",
        lambda name: {"apps": {"remove": ["Microsoft.BingWeather"]}})

    mod._on_apps_preset("light")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") \
        if mod._widget.findChild(QTableWidget, "_apps_table") \
        else mod._apps_table
    checked = sum(1 for r in range(table.rowCount())
                 if table.item(r, 0).checkState() == Qt.CheckState.Checked)
    assert checked == 1


def test_custom_preset_saves_the_live_selection_and_reloads_it(
        monkeypatch, tmp_path):
    mod = _module()
    tweaks = [{"id": "disable_cortana", "name": "Disable Cortana",
              "category": "Privacy", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    table.item(0, 0).setCheckState(Qt.CheckState.Checked)

    # No Custom preset saved yet -- the merge-with-existing branch reads
    # this, and a fresh machine has no debloat_custom.json to merge with.
    def _no_existing_custom(name):
        raise FileNotFoundError("no custom preset yet")
    monkeypatch.setattr(dp, "load_preset", _no_existing_custom)

    saved = {}
    monkeypatch.setattr(
        dp, "save_custom_tweaks_and_apps",
        lambda tweaks_by_category, apps: saved.update(
            tweaks=tweaks_by_category, apps=apps))

    mod._on_save_tweaks_as_custom("tweak")

    assert saved["tweaks"] == {"Privacy": ["disable_cortana"]}


def test_apply_all_safe_asks_first(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "require_admin", lambda: True)
    monkeypatch.setattr(mod, "_load_debloat_entries",
                        lambda: {"e1": {"id": "e1", "package": "Pkg.A",
                                        "name": "A", "category": "X"}})
    mod._populate_apps_table(["Pkg.A"])
    asked = []
    monkeypatch.setattr(dm, "confirm_destructive",
                        lambda *a, **k: asked.append(1) or False)
    applied = []
    monkeypatch.setattr(mod, "_do_apply_apps", lambda ids: applied.append(ids))
    mod._on_apply_all_safe()
    assert asked and applied == []


def test_multiple_protected_apps_get_one_dialog_not_several(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "require_admin", lambda: True)
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Microsoft.WindowsStore",
              "name": "Store", "category": "X"},
        "e2": {"id": "e2", "package": "Microsoft.WindowsTerminal",
              "name": "Terminal", "category": "X"}})
    mod._populate_apps_table(
        ["Microsoft.WindowsStore", "Microsoft.WindowsTerminal"])
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    for r in range(table.rowCount()):
        table.item(r, 0).setCheckState(Qt.CheckState.Checked)
    calls = []
    monkeypatch.setattr(dm, "confirm_destructive",
                        lambda *a, **k: calls.append(1) or True)
    monkeypatch.setattr(mod, "_do_apply_apps", lambda ids: None)
    mod._on_apply_selected()
    assert len(calls) == 1


def test_search_hides_non_matching_rows(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Microsoft.BingWeather",
              "name": "Bing Weather", "category": "Bing Apps"},
        "e2": {"id": "e2", "package": "Microsoft.XboxApp",
              "name": "Xbox", "category": "Gaming"}})
    mod._populate_apps_table(["Microsoft.BingWeather", "Microsoft.XboxApp"])
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    mod._apps_search.setText("xbox")
    mod._apply_apps_filter()
    visible = [r for r in range(table.rowCount()) if not table.isRowHidden(r)]
    assert len(visible) == 1
    assert "Xbox" in table.item(visible[0], 1).text()


def test_select_all_checks_every_visible_row(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.A", "name": "A", "category": "X"},
        "e2": {"id": "e2", "package": "Pkg.B", "name": "B", "category": "X"}})
    mod._populate_apps_table(["Pkg.A", "Pkg.B"])
    mod._on_apps_select_all()
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    assert all(table.item(r, 0).checkState() == Qt.CheckState.Checked
              for r in range(table.rowCount()))


def test_selected_count_label_tracks_checked_rows(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.A", "name": "A", "category": "X"}})
    mod._populate_apps_table(["Pkg.A"])
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert "1 selected" in mod._apps_selected_lbl.text()
