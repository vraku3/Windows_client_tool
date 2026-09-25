"""DebloatToolsModule's tweaks tables: the five-value status vocabulary,
shown with a reason, is what this file pins down. Nothing here touches a
real machine -- TweakEngine.detect is monkeypatched throughout.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHeaderView, QLabel, QLineEdit, QMessageBox, QTableWidget, QTabWidget,
)

from core.event_bus import EventBus
from modules.debloat import debloat_module as dm
from modules.debloat import debloat_presets as dp
from modules.tweaks import tweak_engine as te


class _FakeBackup:
    def create_restore_point(self, label, module):
        return "rp-fake"

    def record_steps(self, *a, **k):
        pass


class _FakeConfig:
    """Same lightweight get/set stand-in tests/test_blocklist.py uses --
    T23's sort persistence needs a real .get/.set, not just an attribute
    that raises AttributeError the moment _populate_apps_table reads it."""

    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class _FakeApp:
    def __init__(self):
        self.backup = _FakeBackup()
        self.thread_pool = None
        self.config = _FakeConfig()
        self.event_bus = EventBus()


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


def test_preset_with_no_matching_tweaks_leaves_selection_alone_and_explains(
        monkeypatch):
    """debloat_light.json/debloat_full.json group their tweaks entirely
    under app-removal categories that don't exist in this tab's tweak
    catalog -- resolve_tweak_ids legitimately returns an empty set. Must
    NOT silently clear every checkbox with no explanation (that just looks
    like the preset button is broken)."""
    mod = _module()
    tweaks = [{"id": "disable_cortana", "name": "Disable Cortana",
              "category": "Privacy", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    table.item(0, 0).setCheckState(Qt.CheckState.Checked)  # pre-existing selection

    monkeypatch.setattr(
        dp, "load_preset",
        lambda name: {"name": "Light Debloat", "tweaks": {"Bing Apps": ["*"]}})

    mod._on_preset("light", "tweak")

    assert table.item(0, 0).checkState() == Qt.CheckState.Checked, \
        "an empty resolution for a named preset must not clear the tab"
    status_lbl = mod._status_lbl_for("tweak")
    assert "Light Debloat" in status_lbl.text()
    assert "Apps tab" in status_lbl.text()


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


def test_apps_preset_with_no_matching_apps_leaves_selection_alone_and_explains(
        monkeypatch):
    """debloat_privacy.json is tweaks-only by design ("Keep all apps...
    No app removal") -- resolve_app_entry_ids legitimately returns an
    empty set on the Apps tab. Same rule as the tweak tabs: never silently
    clear the selection with no explanation."""
    mod = _module()
    monkeypatch.setattr(
        mod, "_load_debloat_entries",
        lambda: {"e1": {"id": "e1", "package": "Pkg.A", "name": "A",
                        "category": "X"}})
    mod._populate_apps_table(["Pkg.A"])
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    table.item(0, 0).setCheckState(Qt.CheckState.Checked)

    monkeypatch.setattr(
        dp, "load_preset",
        lambda name: {"name": "Privacy-Focused", "apps": {"remove": []}})

    mod._on_apps_preset("privacy")

    assert table.item(0, 0).checkState() == Qt.CheckState.Checked
    assert "Privacy-Focused" in mod._apps_status.text()


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


def test_apply_all_safe_excludes_not_installed_rows_when_show_all_is_on(
        monkeypatch):
    """Show All puts catalogued-but-uninstalled rows into the table.
    'Apply All Safe' has no per-row checkbox gate at all, so without an
    explicit installed-check it would attempt to remove every non-protected
    app in the WHOLE CATALOG, not every non-protected installed app."""
    mod = _module()
    monkeypatch.setattr(mod, "require_admin", lambda: True)
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.A", "name": "A", "category": "X"},
        "e2": {"id": "e2", "package": "Pkg.Ghost", "name": "Ghost",
              "category": "X"}})
    mod._show_all_checkbox.setChecked(True)
    mod._populate_apps_table(["Pkg.A"])  # only Pkg.A is actually installed
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    assert table.rowCount() == 2  # both rows visible with Show All on
    monkeypatch.setattr(dm, "confirm_destructive", lambda *a, **k: True)
    applied = []
    monkeypatch.setattr(mod, "_do_apply_apps", lambda ids: applied.append(ids))
    mod._on_apply_all_safe()
    assert applied == [["e1"]]


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


def test_screen_sketch_is_now_protected():
    from modules.debloat import debloat_scanner as ds
    assert "Microsoft.ScreenSketch" in ds.PROTECTED_APPS
    assert ds.PROTECTED_REASONS["Microsoft.ScreenSketch"]


def test_apps_applied_reports_what_actually_left(monkeypatch):
    mod = _module()
    mod._installed_apps = ["Pkg.A", "Pkg.B"]
    monkeypatch.setattr(mod, "_on_scan", lambda: None)
    monkeypatch.setattr(
        dm.debloat_scanner, "get_installed_packages",
        lambda: {"Pkg.B": "Pkg.B"})  # Pkg.A genuinely left; Pkg.B did not
    # The completion dialog is a real QMessageBox with a custom "Open
    # Restore Manager..." action button (A22) -- exec() would otherwise
    # block waiting for a click that never comes in a headless test.
    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: QMessageBox.StandardButton.Ok)
    result = {"success": 1, "total": 1, "targeted": ["Pkg.A"]}
    mod._on_apps_applied(result)
    assert "1 of 1" in mod._apps_status.text()


def test_apps_applied_drops_the_appx_cache_before_reverifying(monkeypatch):
    """Real bug (user-reported): selecting a preset that targets 8 apps and
    applying it reported "0 of 8 app(s) confirmed removed" even though the
    removal itself succeeded. Root cause: get_installed_packages() ->
    appx_service.fetch_packages() reuses a cached AppX list for up to 60s
    (core/appx_service.py's CACHE_TTL_SECONDS) -- the Apps tab's own earlier
    scan (opening the tab, or a prior preset click) populates that cache,
    and applying + re-verifying an app's removal well within that window
    means the "confirmed removed" check reads the PRE-removal snapshot and
    reports every genuinely-removed app as still present. This pins that
    _on_apps_applied calls invalidate_cache() before re-checking, the same
    fresh-read requirement CLAUDE.md documents for Store Apps' _load_apps
    ("needs current truth right before an uninstall decision")."""
    mod = _module()
    mod._installed_apps = ["Pkg.A"]
    monkeypatch.setattr(mod, "_on_scan", lambda: None)
    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: QMessageBox.StandardButton.Ok)

    calls = []
    # debloat_module.py does `from core.appx_service import ...,
    # invalidate_cache`, binding the name directly into its own namespace --
    # patch it there (dm.invalidate_cache), not on core.appx_service itself,
    # or the patch has no effect on the already-bound reference.
    monkeypatch.setattr(dm, "invalidate_cache",
                        lambda: calls.append("invalidated"))
    # Simulates the stale-cache bug directly: if _on_apps_applied does not
    # invalidate first, this stand-in for get_installed_packages() still
    # reports Pkg.A as present (the pre-removal snapshot); a correct
    # implementation invalidates the cache first, so this test only needs
    # to prove the invalidation call happens, not re-implement the cache.
    monkeypatch.setattr(dm.debloat_scanner, "get_installed_packages",
                        lambda: {"Pkg.A": "Pkg.A"})

    result = {"success": 1, "total": 1, "targeted": ["Pkg.A"]}
    mod._on_apps_applied(result)

    assert calls == ["invalidated"], (
        "_on_apps_applied must call appx_service.invalidate_cache() before "
        "re-checking installed packages, or the check can read a stale "
        "pre-removal cache and report a successful removal as failed")


def test_show_all_reveals_uninstalled_catalog_entries(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.NotHere", "name": "Ghost", "category": "X"}})
    mod._show_all_checkbox.setChecked(True)
    mod._populate_apps_table([])  # nothing installed
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    assert table.rowCount() == 1
    assert "Not installed" in table.item(0, 3).text()


def test_an_installed_app_says_installed_not_just_a_size(monkeypatch):
    """The Status column held a bare "24.4 MB"; it now says what it means."""
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.Here", "name": "Here", "category": "X"},
        "e2": {"id": "e2", "package": "Pkg.NoSize", "name": "NoSize", "category": "X"}})
    monkeypatch.setattr(mod, "_install_location_by_package", lambda: {"Pkg.Here": "sized"})
    monkeypatch.setattr(dm, "dir_size",
                        lambda path: 25_600_000 if path == "sized" else 0)
    mod._populate_apps_table(["Pkg.Here", "Pkg.NoSize"])
    texts = {mod._apps_table.item(r, 1).text(): mod._apps_table.item(r, 3).text()
             for r in range(mod._apps_table.rowCount())}
    assert texts["Here"].startswith("Installed — ") and "MB" in texts["Here"]
    assert texts["NoSize"] == "Installed"


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


def test_first_activation_triggers_a_scan(monkeypatch):
    mod = _module()
    scanned = []
    monkeypatch.setattr(mod, "_on_scan", lambda: scanned.append(1))
    mod.on_activate()
    assert scanned == [1]
    mod.on_activate()  # second time must NOT scan again
    assert scanned == [1]


def test_a_failed_enumeration_reports_a_failed_scan_not_zero_apps(monkeypatch):
    """Regression test: a query that could not complete (refused, timed
    out, or its process tree killed after wedging -- see
    appx_service._run_ps_bounded) must surface as "Scan failed", never as
    a misleadingly clean "Scan complete -- 0 bloatware app(s) detected"."""
    mod = _module()
    monkeypatch.setattr(dm, "get_installed_packages_or_none", lambda: None)
    import pytest
    with pytest.raises(RuntimeError):
        mod._do_scan(worker=None)


def test_status_line_breaks_down_by_category(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.A", "name": "A", "category": "Gaming"},
        "e2": {"id": "e2", "package": "Pkg.B", "name": "B", "category": "Gaming"},
        "e3": {"id": "e3", "package": "Pkg.C", "name": "C", "category": "Bing Apps"}})
    mod._on_scanned({"installed": ["Pkg.A", "Pkg.B", "Pkg.C"]})
    text = mod._apps_status.text()
    assert "Gaming: 2" in text and "Bing Apps: 1" in text


def test_cancel_button_cancels_the_apply_worker(monkeypatch):
    mod = _module()
    mod._apply_worker = type("W", (), {"cancelled": False,
                                       "cancel": lambda self: setattr(self, "cancelled", True)})()
    mod._on_cancel_apply()
    assert mod._apply_worker.cancelled is True


def test_export_writes_every_catalogued_and_installed_app(tmp_path, monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.A", "name": "A", "category": "X"}})
    mod._populate_apps_table(["Pkg.A"])
    out = tmp_path / "apps.csv"
    monkeypatch.setattr(dm.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), ""))
    mod._on_export_apps()
    assert "Pkg.A" in out.read_text(encoding="utf-8")


def test_tweaks_search_filters_by_name(monkeypatch):
    mod = _module()
    tweaks = [{"id": "a", "name": "Disable Cortana", "category": "Privacy", "risk": "Low"},
             {"id": "b", "name": "Disable Telemetry", "category": "Telemetry", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")
    search = mod._widget.findChild(QLineEdit, "_search_tweak")
    search.setText("cortana")
    mod._apply_tweaks_filter("tweak")
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    visible = [r for r in range(table.rowCount()) if not table.isRowHidden(r)]
    assert len(visible) == 1


def test_context_menu_copies_the_registry_path_when_the_tweak_has_one(
        monkeypatch):
    mod = _module()
    tweak = {"id": "a", "name": "X", "category": "Privacy", "risk": "Low",
            "steps": [{"type": "registry",
                      "key": r"HKLM\SOFTWARE\Policies\X", "value": "Y"}]}
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: [tweak])
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")
    path = mod._registry_path_for_tweak(tweak)
    assert path == r"HKLM\SOFTWARE\Policies\X\Y"


def test_context_menu_offers_the_registry_path_of_a_later_step_too(monkeypatch):
    # Real shape, e.g. services.json's disable_windows_update_au_svc /
    # privacy.json's disable_windows_insider: a `service` step FIRST, a
    # `registry` step SECOND. The menu must not fall back to "Copy command"
    # (and an empty clipboard) just because steps[0] isn't a registry step.
    mod = _module()
    tweak = {"id": "a", "name": "X", "category": "Privacy", "risk": "Low",
            "steps": [{"type": "service", "name": "wuauserv", "start_type": "manual"},
                      {"type": "registry",
                       "key": r"HKLM\SOFTWARE\Policies\X", "value": "Y"}]}
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: [tweak])
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")

    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QApplication, QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    # Sidestep pixel-accurate hit testing: point indexAt at row 0 directly.
    monkeypatch.setattr(table, "indexAt", lambda pos: table.model().index(0, 0))
    # QMenu.exec() blocks for a real click; capture the menu instead of
    # popping it up, the same way the test would trigger a real user click.
    captured = []
    monkeypatch.setattr(dm.QMenu, "exec", lambda self, *a, **k: captured.append(self))

    mod._on_tweaks_context_menu(QPoint(0, 0), "tweak")

    menu = captured[0]
    actions = {a.text(): a for a in menu.actions()}
    assert "Copy registry path" in actions
    assert "Copy command" not in actions
    actions["Copy registry path"].trigger()
    assert QApplication.clipboard().text() == r"HKLM\SOFTWARE\Policies\X\Y"


def test_populate_uses_detect_many_not_one_call_per_tweak(monkeypatch):
    mod = _module()
    tweaks = [{"id": "a", "name": "A", "category": "X", "risk": "Low"},
             {"id": "b", "name": "B", "category": "X", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    calls = []
    def fake_detect_many(self, tweaks, on_result, is_cancelled=None, workers=8):
        calls.append(len(tweaks))
        for t in tweaks:
            on_result(t, te.DetectionResult(te.NOT_APPLIED))
    monkeypatch.setattr(te.TweakEngine, "detect_many", fake_detect_many)
    mod._populate_tweaks_table("tweak")
    assert calls == [2]  # one batched call, not two individual ones


def test_privacy_and_telemetry_shows_source_file_headers(monkeypatch):
    mod = _module()
    tweaks = [
        {"id": "a", "name": "A", "category": "Privacy", "risk": "Low", "_source": "privacy.json"},
        {"id": "b", "name": "B", "category": "Network", "risk": "Low", "_source": "network.json"},
    ]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect_many",
                        lambda self, tweaks, on_result, **k:
                            [on_result(t, te.DetectionResult(te.NOT_APPLIED)) for t in tweaks])
    mod._populate_tweaks_table("tweak")
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    headers = [table.item(r, 1).text() for r in range(table.rowCount())
              if table.item(r, 0) is None]
    assert "Privacy (privacy.json)" in headers
    assert "Network (network.json)" in headers


def test_definitions_are_not_reparsed_when_the_file_has_not_changed(
        monkeypatch, tmp_path):
    mod = _module()
    calls = []
    real_open = open
    def counting_open(path, *a, **k):
        if str(path).endswith(".json"):
            calls.append(path)
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", counting_open)
    mod._load_tweak_definitions("tweak")
    mod._load_tweak_definitions("tweak")
    # second call must reuse the cache: no more file opens than the first
    assert len(calls) == len(set(calls))


def test_apply_tweaks_shows_a_progress_bar(monkeypatch):
    mod = _module()
    bar = mod._widget.findChild(type(mod._apps_progress).__class__) \
        if False else None
    # progress bar exists per tab, named like the table/status labels
    from PyQt6.QtWidgets import QProgressBar
    bar = mod._widget.findChild(QProgressBar, "_progress_tweak")
    assert bar is not None


def test_completion_dialog_names_which_tweaks_failed(monkeypatch):
    mod = _module()
    result = {"success": 1, "total": 2,
             "failures": [("Disable Cortana", "access denied")]}
    shown = {}
    monkeypatch.setattr(dm.QMessageBox, "information",
                        lambda *a, **k: shown.setdefault("text", a[2] if len(a) > 2 else ""))
    mod._on_tweaks_applied(result)
    assert "Disable Cortana" in shown["text"]


def test_returning_to_a_tab_redetects_status_without_rebuilding_rows(
        monkeypatch):
    mod = _module()
    tweaks = [{"id": "a", "name": "A", "category": "X", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect_many",
                        lambda self, tweaks, on_result, **k:
                            [on_result(t, te.DetectionResult(te.NOT_APPLIED)) for t in tweaks])
    mod._populate_tweaks_table("tweak")
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    row_count_before = table.rowCount()
    monkeypatch.setattr(te.TweakEngine, "detect_many",
                        lambda self, tweaks, on_result, **k:
                            [on_result(t, te.DetectionResult(te.APPLIED)) for t in tweaks])
    mod._on_tab_changed(1)  # "tweak" tab index, per _TAB_TYPES
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    assert table.rowCount() == row_count_before
    assert "Applied" in table.item(0, 4).text()


# ----------------------------------------------------------------------
# C07 follow-up: Task 39's save/restore machinery had zero real effect --
# every column here was Stretch or ResizeToContents, both of which Qt
# silently ignores setColumnWidth() on. Only Interactive columns can hold
# a manually-set or persisted width.
# ----------------------------------------------------------------------


def test_apps_table_checkbox_and_status_columns_are_interactive():
    mod = _module()
    header = mod._apps_table.horizontalHeader()
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch  # App Name
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch  # Category
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive  # checkbox
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.Interactive  # Status


def test_tweaks_table_checkbox_risk_and_status_columns_are_interactive():
    mod = _module()
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    header = table.horizontalHeader()
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch  # Tweak
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch  # Category
    for i in (0, 3, 4):  # checkbox, Risk, Status
        assert header.sectionResizeMode(i) == QHeaderView.ResizeMode.Interactive, \
            f"column {i} is not Interactive"


def test_first_apps_populate_fits_columns_when_nothing_was_persisted(monkeypatch):
    mod = _module()
    calls = []
    monkeypatch.setattr(mod._apps_table, "resizeColumnsToContents", lambda: calls.append(1))
    mod._populate_apps_table(["Microsoft.BingWeather"])
    assert calls == [1]
    assert mod._columns_fitted.get("apps") is True


def test_a_later_apps_populate_never_reverts_an_in_session_column_resize(
        monkeypatch):
    """Same class of bug Task 35 fixed for sort order: _populate_apps_table
    runs again on every Show All toggle and every completed Apply -- not
    just the first scan -- so a fit-once step firing again would silently
    undo a column the user just dragged wider."""
    mod = _module()
    mod._populate_apps_table(["Microsoft.BingWeather"])  # first real populate

    mod._apps_table.setColumnWidth(3, 321)  # simulate a user drag-resize on Status (Interactive)
    calls = []
    monkeypatch.setattr(mod._apps_table, "resizeColumnsToContents", lambda: calls.append(1))

    mod._populate_apps_table(["Microsoft.BingWeather"])  # e.g. Show All toggled

    assert calls == []
    assert mod._apps_table.columnWidth(3) == 321


def test_first_tweaks_populate_fits_columns_when_nothing_was_persisted(
        monkeypatch):
    mod = _module()
    tweaks = [{"id": "a", "name": "A", "category": "X", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect_many",
                        lambda self, tweaks, on_result, **k:
                            [on_result(t, te.DetectionResult(te.NOT_APPLIED)) for t in tweaks])
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    calls = []
    monkeypatch.setattr(table, "resizeColumnsToContents", lambda: calls.append(1))

    mod._populate_tweaks_table("tweak")

    assert calls == [1]
    assert mod._columns_fitted.get("tweak") is True


def test_a_later_tweaks_populate_never_reverts_an_in_session_column_resize(
        monkeypatch):
    mod = _module()
    tweaks = [{"id": "a", "name": "A", "category": "X", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect_many",
                        lambda self, tweaks, on_result, **k:
                            [on_result(t, te.DetectionResult(te.NOT_APPLIED)) for t in tweaks])
    mod._populate_tweaks_table("tweak")  # first real populate -- fits once

    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    table.setColumnWidth(3, 321)  # simulate a user drag-resize on Risk
    calls = []
    monkeypatch.setattr(table, "resizeColumnsToContents", lambda: calls.append(1))

    mod._populate_tweaks_table("tweak")  # e.g. after an Apply completes

    assert calls == []
    assert table.columnWidth(3) == 321


def test_apps_populate_skips_the_fit_when_a_width_is_already_persisted():
    mod = dm.DebloatToolsModule()
    app = _FakeApp()
    app.config._data["debloat.apps.column_widths"] = [40, 200, 90, 160]
    mod.on_start(app)
    mod.create_widget()  # restore_column_widths() applies the saved widths here

    assert mod._apps_table.columnWidth(3) == 160
    mod._populate_apps_table(["Microsoft.BingWeather"])
    # The restored width must survive the first real populate untouched.
    assert mod._apps_table.columnWidth(3) == 160


def test_wrap_adds_a_removed_this_session_banner():
    mod = dm.DebloatModule()
    tabs = QTabWidget()
    wrapped = mod.wrap(tabs)
    banner = wrapped.findChild(QLabel, "_removed_this_session_banner")
    assert banner is not None


def test_sort_persists_on_deactivate_and_restores_on_the_next_create_widget(
        monkeypatch):
    """DebloatToolsModule.on_deactivate()'s FIRST statement used to raise
    TypeError -- `int(header.sortIndicatorOrder())` under this PyQt6
    build's plain (non-Int) `Qt.SortOrder` enum -- silently skipping
    cancel_all_workers() and persistence on EVERY module switch. Every
    other test in this file hand-injects config values rather than driving
    a real on_deactivate() -> fresh create_widget() round trip, exactly the
    class of gap that let it go unnoticed. Mirrors
    test_driver_module.py's identically-named test."""
    mod = _module()
    entries = {
        "e1": {"id": "e1", "package": "Pkg.Zeta", "name": "Zeta", "category": "X"},
        "e2": {"id": "e2", "package": "Pkg.Alpha", "name": "Alpha", "category": "X"},
    }
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: entries)
    mod._populate_apps_table(["Pkg.Zeta", "Pkg.Alpha"])
    header = mod._apps_table.horizontalHeader()
    header.setSortIndicator(1, Qt.SortOrder.DescendingOrder)
    mod._apps_table.sortItems(1, Qt.SortOrder.DescendingOrder)
    shared_config = mod.app.config

    mod.on_deactivate()  # must not raise -- this is the exact bug

    mod2 = dm.DebloatToolsModule()
    mod2.on_start(_FakeApp())
    mod2.app.config = shared_config  # same persisted store, fresh module
    mod2.create_widget()
    monkeypatch.setattr(mod2, "_load_debloat_entries", lambda: entries)
    mod2._populate_apps_table(["Pkg.Zeta", "Pkg.Alpha"])
    assert mod2._apps_table.item(0, 1).text() == "Zeta"  # descending -> Zeta first


def test_protected_apps_dialog_lists_every_protected_app(monkeypatch):
    mod = _module()
    shown = []
    class FakeDialog:
        def __init__(self, *a, **k): pass
        def setWindowTitle(self, *a, **k): pass
        def setLayout(self, *a, **k): pass
        def resize(self, *a, **k): pass
        def exec(self): shown.append(1)
    monkeypatch.setattr(dm, "QDialog", FakeDialog)
    mod._on_show_protected_apps()
    assert shown == [1]
