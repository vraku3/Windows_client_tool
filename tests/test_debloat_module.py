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
