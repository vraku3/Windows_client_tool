import os

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QPushButton

from core.appx_service import _version_key
from modules.store_apps import store_apps_module as sam
from modules.store_apps.store_apps_module import (
    failure_hint,
    friendly_name_from_location,
    human_size,
    is_opaque_identifier,
    is_system_package,
    resolve_package_name,
    resolve_sid_to_name,
    shorten_app_name,
    short_publisher,
)


def test_is_opaque_identifier():
    assert is_opaque_identifier("1527c705-839a-4832-9118-54d4Bd6a0c89")
    assert is_opaque_identifier("S-1-5-18")
    assert is_opaque_identifier("123456789")
    assert not is_opaque_identifier("Microsoft.WindowsCalculator")
    assert not is_opaque_identifier("")


def test_friendly_name_from_location():
    assert (
        friendly_name_from_location(
            r"C:\Windows\SystemApps\Microsoft.Windows.FilePicker_cw5n1h2txyewy"
        )
        == "Microsoft.Windows.FilePicker"
    )
    assert friendly_name_from_location("") == ""


def test_friendly_name_ignores_opaque_folders():
    assert (
        friendly_name_from_location(
            r"C:\Program Files\WindowsApps\1527c705-839a-4832-9118-54d4Bd6a0c89_cw5n1h2txyewy"
        )
        == ""
    )


def test_resolve_package_name_guid_via_location():
    assert (
        resolve_package_name(
            "1527c705-839a-4832-9118-54d4Bd6a0c89",
            r"C:\Windows\SystemApps\Microsoft.Windows.FilePicker_cw5n1h2txyewy",
        )
        == "Microsoft.Windows.FilePicker"
    )


def test_resolve_package_name_normal_name_unchanged():
    assert resolve_package_name("Microsoft.WindowsCalculator", "") == "Microsoft.WindowsCalculator"


def test_resolve_package_name_sid():
    assert resolve_sid_to_name("S-1-5-18") == "NT AUTHORITY\\SYSTEM"
    assert resolve_package_name("S-1-5-18", "") == "NT AUTHORITY\\SYSTEM"


def test_short_publisher_extracts_organization():
    assert (
        short_publisher(
            "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"
        )
        == "Microsoft Corporation"
    )
    assert short_publisher("SomeOther") == "SomeOther"
    assert short_publisher("") == ""


def test_is_system_package_by_location():
    assert is_system_package(
        "Microsoft.Windows.StartMenuExperienceHost",
        r"C:\Windows\SystemApps\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy",
    )
    assert is_system_package("Microsoft.WindowsStore", r"C:\Program Files\WindowsApps\x")
    assert not is_system_package(
        "Microsoft.WindowsCalculator",
        r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_8wekyb3d8bbwe",
    )
    assert not is_system_package("Microsoft.WindowsCalculator", "")


def test_version_key():
    assert _version_key("11.2607.0.0") > _version_key("11.2400.0.0")
    assert _version_key("10.0.22621.1") == _version_key("10.0.22621.1")
    assert _version_key("1.2.3-suffix") == _version_key("1.2.3")


def test_shorten_app_name_drops_vendor_prefix():
    assert shorten_app_name("Microsoft.WindowsCalculator") == "Calculator"
    assert shorten_app_name("Microsoft.Windows.FilePicker") == "FilePicker"
    assert shorten_app_name("Microsoft.BingWeather") == "BingWeather"
    assert shorten_app_name("Microsoft.WindowsTerminal") == "Terminal"
    assert shorten_app_name("SpotifyAB.SpotifyMusic") == "SpotifyMusic"
    assert shorten_app_name("Microsoft.MicrosoftOfficeHub") == "OfficeHub"


def test_shorten_app_name_leaves_short_and_flat_names():
    assert shorten_app_name("Microsoft.Windows") == "Microsoft.Windows"
    assert shorten_app_name("1527c705-839a-4832-9118-54d4Bd6a0c89") == "1527c705-839a-4832-9118-54d4Bd6a0c89"


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"
    assert human_size(-1) == "n/a"


def test_failure_hint():
    assert (
        failure_hint("0x80073CFB ... in use ...")
        == "The app may be running. Close it and try again."
    )
    assert (
        failure_hint("The file is being used by another process")
        == "The app may be running. Close it and try again."
    )
    assert failure_hint("Access is denied") == ""


def _make_fake_app(tmp_path):
    """A minimal App stand-in with a real backup/config/thread-pool."""
    from PyQt6.QtCore import QThreadPool

    from core.backup_service import BackupService
    from core.config_manager import ConfigManager
    from core.event_bus import EventBus

    class FakeApp:
        pass

    FakeApp.backup = BackupService(str(tmp_path))
    FakeApp.config = ConfigManager(str(tmp_path), {"version": 1})
    FakeApp.config.load()
    FakeApp.thread_pool = QThreadPool.globalInstance()
    FakeApp.event_bus = EventBus()
    return FakeApp


def store_module():
    """A StoreAppsModule with a real widget, for tests that don't need a
    tmp_path fixture -- P08's badge test just needs on_start/create_widget
    to have run so _debloat_packages and _table exist."""
    import tempfile

    mod = sam.StoreAppsModule()
    mod.on_start(_make_fake_app(tempfile.mkdtemp()))
    mod.create_widget()
    return mod


def load_two_apps(mod):
    """Populate mod's table with two rows via the real load path: one
    Microsoft-published, one not (matching field-scoped search tests)."""
    apps = [
        {"Name": "Microsoft.WindowsCalculator",
         "Publisher": "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
         "Version": "1.0.0.0", "InstallLocation": "",
         "PackageFamilyName": "Microsoft.WindowsCalculator_8wekyb3d8bbwe",
         "Architecture": "X64"},
        {"Name": "SpotifyAB.SpotifyMusic",
         "Publisher": "CN=Spotify AB, O=Spotify AB, L=Stockholm, C=SE",
         "Version": "1.0.0.0", "InstallLocation": "",
         "PackageFamilyName": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0",
         "Architecture": "X64"},
    ]
    mod._on_apps_loaded(apps, None)
    return apps


def test_module_creates_widget_and_sorts(qapp, tmp_path):
    from modules.store_apps.store_apps_module import StoreAppsModule

    mod = StoreAppsModule()
    mod.on_start(_make_fake_app(tmp_path))
    assert mod.create_widget() is not None

    sample = [
        {"Name": "Microsoft.WindowsCalculator",
         "Publisher": "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
         "Version": "11.2607.0.0",
         "InstallLocation": r"C:\Program Files\WindowsApps\Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe",
         "PackageFamilyName": "Microsoft.WindowsCalculator_8wekyb3d8bbwe",
         "Architecture": "X64"},
        {"Name": "1527c705-839a-4832-9118-54d4Bd6a0c89",
         "Publisher": "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
         "Version": "10.0.19640.1000",
         "InstallLocation": r"C:\Windows\SystemApps\Microsoft.Windows.FilePicker_cw5n1h2txyewy",
         "PackageFamilyName": "1527c705-839a-4832-9118-54d4Bd6a0c89_cw5n1h2txyewy",
         "Architecture": "Neutral"},
        {"Name": "SpotifyAB.SpotifyMusic",
         "Publisher": "CN=Spotify AB, O=Spotify AB, L=Stockholm, C=SE",
         "Version": "1.230.0",
         "InstallLocation": r"C:\Program Files\WindowsApps\SpotifyAB.SpotifyMusic_1.230.0_x64__zpdnekdrzrea0",
         "PackageFamilyName": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0",
         "Architecture": "X64"},
    ]
    mod._on_apps_loaded(sample, None)

    assert mod._table_stack.currentIndex() == 0
    names = [mod._table.item(r, 0).text() for r in range(mod._table.rowCount())]
    assert names == sorted(names, key=str.lower)
    assert names == ["Calculator", "FilePicker", "SpotifyMusic"]

    removable = [mod._table.item(r, 4).text() for r in range(mod._table.rowCount())]
    assert removable == ["✅ Yes", "❌ System", "✅ Yes"]


def test_module_empty_state(qapp, tmp_path):
    from modules.store_apps.store_apps_module import StoreAppsModule

    mod = StoreAppsModule()
    mod.on_start(_make_fake_app(tmp_path))
    mod.create_widget()
    assert mod._table_stack.currentIndex() == 1

    mod._on_apps_loaded([], None)
    assert mod._table_stack.currentIndex() == 1


def test_module_filter_and_select(qapp, tmp_path):
    from modules.store_apps.store_apps_module import StoreAppsModule

    mod = StoreAppsModule()
    mod.on_start(_make_fake_app(tmp_path))
    mod.create_widget()
    sample = [
        {"Name": "Microsoft.WindowsCalculator", "Version": "1.0",
         "InstallLocation": r"C:\Program Files\WindowsApps\Calculator_8wekyb3d8bbwe"},
        {"Name": "Microsoft.Windows.DevHome", "Version": "1.0",
         "InstallLocation": r"C:\Windows\SystemApps\DevHome_cw5n1h2txyewy"},
        {"Name": "SpotifyAB.SpotifyMusic", "Version": "1.0",
         "InstallLocation": r"C:\Program Files\WindowsApps\SpotifyMusic_zpdnekdrzrea0"},
    ]
    mod._on_apps_loaded(sample, None)

    # Filter: system only
    mod._filter_combo.setCurrentIndex(2)
    visible = [mod._table.item(r, 0).text() for r in range(mod._table.rowCount())
               if not mod._table.isRowHidden(r)]
    assert visible == ["DevHome"]

    mod._filter_combo.setCurrentIndex(0)
    # Search matches the real (unshortened) package name too
    mod._search.setText("SpotifyAB")
    visible = [mod._table.item(r, 0).text() for r in range(mod._table.rowCount())
               if not mod._table.isRowHidden(r)]
    assert visible == ["SpotifyMusic"]
    mod._search.clear()

    # Select non-system selects only removable rows
    mod._select_non_system()
    sel = sorted({i.row() for i in mod._table.selectedIndexes()})
    assert len(sel) == 2


def test_a_catalogued_package_gets_the_bloatware_badge(monkeypatch):
    mod = store_module()
    monkeypatch.setattr(sam, "_debloat_catalog_packages",
                        lambda: {"Microsoft.BingWeather"})
    mod._apps = [{"Name": "Microsoft.BingWeather", "InstallLocation": "",
                 "Publisher": "", "Version": ""}]
    mod._on_apps_loaded(mod._apps, None)
    assert mod._table.item(0, 0).toolTip().startswith("Known bloatware")


def test_resolve_sid_to_name_logs_on_failure(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    def boom(*a, **k):
        raise OSError("no such account")
    monkeypatch.setattr("win32security.ConvertStringSidToSid", boom)
    result = sam.resolve_sid_to_name("S-1-5-21-1-2-3-1001")
    assert result == ""
    assert "no such account" in caplog.text


def test_uninstall_result_is_verified_against_a_fresh_appx_list(monkeypatch):
    """returncode==0 alone must not be believed -- Remove-AppxPackage exits
    0 while removing nothing, the same failure V07 documents for the Tweaks
    Apps tab."""
    calls = []
    def fake_run(cmd, **k):
        calls.append(cmd)
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    monkeypatch.setattr(sam.subprocess, "run", fake_run)
    # still "installed" after the removal call -- Windows lied about success
    monkeypatch.setattr(sam, "fetch_packages",
                        lambda use_cache=False: [{"Name": "Pkg.Ghost"}])
    ok, reason = sam.verify_uninstalled("Pkg.Ghost")
    assert ok is False
    assert "still installed" in reason.lower()


def test_size_column_is_a_numeric_sort_item(monkeypatch):
    mod = store_module()
    monkeypatch.setattr(sam, "fetch_packages", lambda **k: [
        {"Name": "Pkg.A", "InstallLocation": "", "Publisher": "", "Version": ""}])
    mod._on_apps_loaded(sam.dedupe_by_name(sam.fetch_packages()), None)
    assert isinstance(mod._table.item(0, 3), sam.NumericSortItem)


def test_size_scan_uses_a_thread_pool_not_one_daemon_thread(monkeypatch):
    mod = store_module()
    mod._apps = [{"Name": f"Pkg.{i}", "InstallLocation": ""} for i in range(20)]
    submitted = []

    class FakePool:
        def submit(self, fn, *a):
            submitted.append(a)
            fn(*a)

    monkeypatch.setattr(mod, "_size_pool", FakePool())
    mod._start_size_scan()
    assert len(submitted) == 20


def test_row_index_avoids_a_linear_scan(monkeypatch):
    mod = store_module()
    mod._apps = [{"Name": "Pkg.A"}]
    mod._on_apps_loaded(mod._apps, None)
    assert mod._row_index.get("Pkg.A") == 0
    assert mod._row_of("Pkg.A") == 0  # now O(1) via the index


def test_a_partial_size_read_is_marked_approximate(monkeypatch):
    mod = store_module()
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(os, "walk", lambda p: iter(
        [("root", [], ["a"])]))
    monkeypatch.setattr(os.path, "getsize",
                        lambda p: (_ for _ in ()).throw(OSError("denied")))
    size, approximate = mod._dir_size_detailed("C:\\fake")
    assert approximate is True


def test_uninstall_confirmation_names_apps_even_when_few():
    mod = store_module()
    text = mod._uninstall_confirmation_text(
        names=["Calculator"], skipped_names=[], total_bytes=0)
    assert "Calculator" in text


def test_confirmation_shows_aggregate_size():
    mod = store_module()
    text = mod._uninstall_confirmation_text(
        names=["A", "B"], skipped_names=[], total_bytes=2_500_000_000)
    assert "2." in text and "GB" in text


def test_skipped_system_apps_are_named():
    mod = store_module()
    text = mod._uninstall_confirmation_text(
        names=["A"], skipped_names=["Microsoft.Windows"], total_bytes=0)
    assert "Microsoft.Windows" in text


def _open_context_menu(mod, name, monkeypatch, captured):
    """Trigger _on_context_menu for the row holding `name`, bypassing pixel
    math -- indexAt is stubbed to return that row directly, and QMenu.exec
    is stubbed to capture the built menu instead of blocking on a real
    (headless) event loop."""
    row = mod._row_of(name)
    assert row >= 0, f"{name} not found in table"
    monkeypatch.setattr(mod._table, "indexAt", lambda pos: mod._table.model().index(row, 0))
    monkeypatch.setattr(sam.QMenu, "exec", lambda self, *a, **k: captured.append(self))
    captured.clear()
    mod._on_context_menu(QPoint(0, 0))
    return {a.text(): a for a in captured[0].actions()}


def test_context_menu_copy_pfn_and_location_actions(monkeypatch):
    mod = store_module()
    apps = [
        {"Name": "Pkg.WithBoth",
         "InstallLocation": r"C:\Program Files\WindowsApps\Pkg.WithBoth_1.0_x64__abc",
         "Publisher": "Pub", "Version": "1.0", "PackageFamilyName": "Pkg.WithBoth_abc"},
        {"Name": "Pkg.Bare", "InstallLocation": "", "Publisher": "", "Version": "",
         "PackageFamilyName": ""},
    ]
    mod._apps = apps
    monkeypatch.setattr(mod, "_start_size_scan", lambda: None)  # no background thread to race teardown
    mod._on_apps_loaded(apps, None)

    captured = []
    actions_with = _open_context_menu(mod, "Pkg.WithBoth", monkeypatch, captured)
    assert "Copy Package Family Name" in actions_with
    assert "Copy install location" in actions_with
    assert actions_with["Copy Package Family Name"].isEnabled()
    assert actions_with["Copy install location"].isEnabled()

    actions_bare = _open_context_menu(mod, "Pkg.Bare", monkeypatch, captured)
    assert not actions_bare["Copy Package Family Name"].isEnabled()
    assert not actions_bare["Copy install location"].isEnabled()


def test_open_folder_guard_reports_missing_location_without_calling_startfile(
        monkeypatch, tmp_path):
    mod = store_module()
    missing = str(tmp_path / "does_not_exist_anymore")
    apps = [
        {"Name": "Pkg.Gone", "InstallLocation": missing,
         "Publisher": "", "Version": "", "PackageFamilyName": "Pkg.Gone_abc"},
    ]
    mod._apps = apps
    monkeypatch.setattr(mod, "_start_size_scan", lambda: None)  # no background thread to race teardown
    mod._on_apps_loaded(apps, None)

    def boom(path):
        raise AssertionError(f"os.startfile must not be called for a missing path: {path}")
    monkeypatch.setattr(sam.os, "startfile", boom)

    info_calls = []
    monkeypatch.setattr(sam.QMessageBox, "information",
                        lambda *a, **k: info_calls.append(a))

    captured = []
    actions = _open_context_menu(mod, "Pkg.Gone", monkeypatch, captured)
    actions["Open install folder"].trigger()

    assert len(info_calls) == 1
    assert "no longer exists" in info_calls[0][-1]
    assert missing in info_calls[0][-1]


def test_csv_export_writes_all_six_columns(monkeypatch, tmp_path):
    mod = store_module()
    apps = [
        {"Name": "Pkg.Exportable",
         "InstallLocation": r"C:\Program Files\WindowsApps\Pkg.Exportable_1.2.3_x64__abc",
         "Publisher": "CN=Vendor Inc, O=Vendor Inc, L=City, S=ST, C=US",
         "Version": "1.2.3", "PackageFamilyName": "Pkg.Exportable_abc",
         "Architecture": "X64"},
    ]
    mod._apps = apps
    monkeypatch.setattr(mod, "_start_size_scan", lambda: None)  # no background thread to race teardown
    mod._on_apps_loaded(apps, None)

    csv_path = tmp_path / "export.csv"
    monkeypatch.setattr(sam.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(csv_path), "CSV file (*.csv)"))
    monkeypatch.setattr(sam.QMessageBox, "information", lambda *a, **k: None)

    mod._export()

    assert csv_path.exists()
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(sam.csv.reader(f))
    assert rows[0] == ["Package Name", "Display Name", "Publisher",
                       "Version", "Size", "Architecture"]
    assert len(rows) == 2
    data = rows[1]
    assert len(data) == 6
    assert data[0] == "Pkg.Exportable"
    assert data[1] == "Exportable"
    assert data[2] == "Vendor Inc"
    assert data[3] == "1.2.3"
    assert data[5] == "X64"


def test_system_tooltip_distinguishes_exact_match_from_path_based(monkeypatch):
    mod = store_module()
    apps = [
        {"Name": "Microsoft.WindowsStore",  # exact match in SYSTEM_PACKAGES
         "InstallLocation": r"C:\Program Files\WindowsApps\Microsoft.WindowsStore_x64__8wekyb3d8bbwe",
         "Publisher": "", "Version": ""},
        {"Name": "Microsoft.Windows.StartMenuExperienceHost",  # path-based only
         "InstallLocation": r"C:\Windows\SystemApps\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy",
         "Publisher": "", "Version": ""},
    ]
    mod._apps = apps
    monkeypatch.setattr(mod, "_start_size_scan", lambda: None)  # no background thread to race teardown
    mod._on_apps_loaded(apps, None)

    exact_row = mod._row_of("Microsoft.WindowsStore")
    path_row = mod._row_of("Microsoft.Windows.StartMenuExperienceHost")
    exact_tip = mod._table.item(exact_row, 4).toolTip()
    path_tip = mod._table.item(path_row, 4).toolTip()

    assert exact_tip != path_tip
    assert "exact-match core Windows package" in exact_tip
    assert f"installed under {sam.system_root()}\\SystemApps" in path_tip
    assert "exact-match" not in path_tip
    assert "Removes for every user on this machine." in exact_tip
    assert "Removes for every user on this machine." in path_tip


def test_field_scoped_search_matches_publisher_only(monkeypatch):
    mod = store_module()
    load_two_apps(mod)  # existing test helper; one Microsoft-published, one not
    mod._search.setText("publisher:microsoft")
    mod._apply_filter()
    visible = [r for r in range(mod._table.rowCount()) if not mod._table.isRowHidden(r)]
    assert len(visible) == 1


def test_select_non_system_only_selects_currently_visible_rows(monkeypatch):
    mod = store_module()
    load_two_apps(mod)
    mod._search.setText("nonexistent-app-name")
    mod._apply_filter()
    mod._select_non_system()
    assert mod._table.selectionModel().selectedRows() == []


def test_status_line_shows_selection_and_size_while_active(monkeypatch):
    mod = store_module()
    load_two_apps(mod)
    mod._table.selectRow(0)
    text = mod.get_status_info()
    assert "selected" in text


def test_sort_column_is_actually_restored_after_a_restart(monkeypatch):
    import tempfile
    mod = sam.StoreAppsModule()
    app = _make_fake_app(tempfile.mkdtemp())
    mod.on_start(app)
    mod.app.config.set("modules.store_apps.sort_column", 2)
    mod.app.config.set("modules.store_apps.sort_order", Qt.SortOrder.DescendingOrder.value)
    mod.create_widget()  # NOW create_widget() reads the already-set config
    load_two_apps(mod)
    header = mod._table.horizontalHeader()
    assert header.sortIndicatorSection() == 2


def test_last_refreshed_label_updates_after_a_load(monkeypatch):
    mod = store_module()
    load_two_apps(mod)
    assert mod._last_refreshed_lbl.text() != ""


def test_a_row_vanishing_mid_size_scan_is_logged(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    mod = store_module()
    load_two_apps(mod)
    mod._on_size_ready("Pkg.DoesNotExistAnymore", 100)
    assert "no longer in the table" in caplog.text.lower()


def test_uninstall_button_hints_the_delete_shortcut():
    mod = store_module()
    btn = mod._widget.findChild(QPushButton, "_uninstall_btn")
    assert "Del" in btn.toolTip()


def test_shortcuts_legend_is_reachable_from_the_toolbar():
    mod = store_module()
    legend_btn = mod._widget.findChild(QPushButton, "_shortcuts_btn")
    assert legend_btn is not None


# ----------------------------------------------------------------------
# Task 38 (C01): get_search_provider() wires a live handle onto self._apps,
# not a snapshot taken when the provider was built -- see
# tests/test_store_apps_search_provider.py for the provider's own unit
# tests. These confirm StoreAppsModule wires it correctly.
# ----------------------------------------------------------------------


def test_get_search_provider_returns_a_store_apps_search_provider():
    mod = store_module()
    provider = mod.get_search_provider()
    assert type(provider).__name__ == "StoreAppsSearchProvider"
    assert provider.module_name == "Store Apps"


def test_get_search_provider_sees_current_apps_by_name_and_publisher():
    from core.search_provider import SearchQuery

    mod = store_module()
    load_two_apps(mod)
    provider = mod.get_search_provider()
    by_name = provider.search(SearchQuery(text="calculator"))
    assert any("Microsoft.WindowsCalculator" in r.summary for r in by_name)
    by_publisher = provider.search(SearchQuery(text="microsoft corporation"))
    assert any("Microsoft.WindowsCalculator" in r.summary for r in by_publisher)


def test_get_search_provider_before_any_load_finds_nothing_not_none():
    from core.search_provider import SearchQuery

    mod = store_module()
    provider = mod.get_search_provider()
    assert provider.search(SearchQuery(text="anything")) == []
