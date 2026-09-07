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
