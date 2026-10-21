"""Scanner offers and UI safety labels must agree."""
from types import SimpleNamespace

import pytest

from modules.cleanup.cleanup_scanner import scanners_system as ss


@pytest.fixture
def tree(tmp_path, monkeypatch):
    for key in ("windir", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "TEMP"):
        monkeypatch.setenv(key, str(tmp_path / key))
    def put(env, relative):
        path = tmp_path / env / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / "data").write_bytes(b"content")
        return path
    return put


@pytest.mark.parametrize("name,env,relative", [
    ("scan_game_caches", "LOCALAPPDATA", "Programs/Steam/steamapps"),
    ("scan_game_caches", "LOCALAPPDATA", "Programs/Steam/downloads"),
    ("scan_game_caches", "LOCALAPPDATA", "Programs/Steam/shadercache"),
    ("scan_game_caches", "APPDATA", "Epic/EpicGamesLauncher/Data/Manifests"),
    ("scan_game_caches", "PROGRAMDATA", "XboxLiveDeviceInfo"),
    ("scan_game_caches", "LOCALAPPDATA", "Packages/Microsoft.GamingServices_test/LocalCache"),
    ("scan_game_caches", "LOCALAPPDATA", "Packages/FamilyNotifications.test/LocalState"),
    ("scan_ide_caches", "APPDATA", "Notepad++/backup"),
    ("scan_ide_caches", "APPDATA", "FileZilla"),
    ("scan_ide_caches", "LOCALAPPDATA", "Microsoft/VisualStudio/17.0/Settings"),
    ("scan_wmi_logs", "windir", "System32/wbem/Repository/FS"),
    ("scan_printer_driver_cache", "windir", "System32/spool/drivers"),
    ("scan_print_nightmare_logs", "windir", "System32/spool/drivers"),
    ("scan_winSxS_temp", "windir", "WinSxS/Temp"),
    ("scan_windows_optional_features", "windir", "WinSxS/InstallTemp"),
    ("scan_windows_connected_accounts_cache", "LOCALAPPDATA", "Microsoft/IdentityOLTCache"),
    ("scan_windows_terminal_settings_cache", "LOCALAPPDATA", "Microsoft/WindowsTerminal"),
    ("scan_windows_installer_rollback", "windir", "Installer/Patch"),
])
def test_scanner_never_offers_protected_data(tree, monkeypatch, name, env, relative):
    tree(env, relative)
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="STATE : 1 STOPPED"))
    assert getattr(ss, name)().items == []


@pytest.mark.parametrize("name", ["scan_print_spooler", "scan_printer_driver_cache", "scan_print_nightmare_logs",
                                  "scan_windows_printer_migration_cache"])
@pytest.mark.parametrize("status,code,offers", [("STATE : 4 RUNNING", 0, False), ("", 1, False),
                                               ("", 0, False),
                                               ("STATE : 3 STOP_PENDING", 0, False),
                                               ("STATE : 1 STOPPED", 0, True)])
def test_print_queue_requires_confirmed_stopped(tree, monkeypatch, name, status, code, offers):
    path = tree("windir", "System32/spool/PRINTERS")
    monkeypatch.setattr(ss.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code, stdout=status))
    items = getattr(ss, name)().items
    assert bool(items) is offers
    if offers:
        assert all(i.path == str(path) and i.safety == "caution" and not i.selected for i in items)


def test_msp_scanner_requires_orphan_evidence(tree, monkeypatch):
    folder = tree("windir", "Installer")
    (folder / "referenced.msp").write_bytes(b"package")
    monkeypatch.setattr(ss, "_referenced_installer_packages", lambda: None)
    assert ss.scan_msp_patches().items == []


def test_jetbrains_matches_versioned_system_caches(tree):
    cache = tree("LOCALAPPDATA", "JetBrains/PyCharm2026.2/caches")
    tree("LOCALAPPDATA", "JetBrains/PyCharm2026.2/LocalHistory")
    tree("APPDATA", "JetBrains/PyCharm2026.2")
    assert [i.path for i in ss.scan_ide_caches().items] == [str(cache)]


def test_game_scanner_preserves_profile_but_offers_cache_leaves(tree):
    cache = tree("LOCALAPPDATA", "Programs/Steam/htmlcache/Default/Cache")
    tree("LOCALAPPDATA", "Programs/Steam/htmlcache/Default/Local Storage")
    items = ss.scan_game_caches().items
    assert [i.path for i in items] == [str(cache)]
    assert all(i.safety == "caution" and not i.selected for i in items)


def test_recent_files_are_caution_and_never_selected(tree):
    folder = tree("APPDATA", "Microsoft/Windows/Recent/AutomaticDestinations")
    (folder / "pins.automaticDestinations-ms").write_bytes(b"pins")
    items = ss.scan_recent_files().items
    assert items and all(i.safety == "caution" and not i.selected for i in items)


def test_autodiscovery_is_review_only(tree):
    tree("LOCALAPPDATA", "SomeApp/Cache")
    items = ss.scan_appdata_autodiscover().items
    assert items and all(i.safety == "caution" and not i.selected for i in items)


def test_quick_cleanup_labels_and_large_items(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from modules.cleanup.tabs._large_items_tab import LARGE_SCANNERS
    # The map is built by helpers since the 2026-10-21 function-length split; ask them
    # (they only read `cs`) instead of parsing one method's source for a literal.
    from modules.cleanup import cleanup_scanner as cs
    definitions = QuickCleanupTab._main_scanner_definitions(None, cs)
    definitions.update(QuickCleanupTab._extra_scanner_definitions(None, cs))
    safety = {key: value[1] for key, value in definitions.items()}
    for category in ("recent", "games", "jets"):
        assert safety[category] == "caution"
    assert ss.scan_recycle_bin not in LARGE_SCANNERS


def test_chromium_cache_names():
    from modules.cleanup import browser_scanner as bs
    for names in (bs.CHROMIUM_CACHE_SUBDIRS, dict(bs.CHROMIUM_CACHE_CATEGORIES)):
        assert "GPUCache" in names
        assert "GPU Cache" not in names and "Cache2" not in names
        assert "blob_storage" not in names


@pytest.mark.parametrize("name", ["scan_recent_files", "scan_appdata_autodiscover"])
@pytest.mark.parametrize("preset", ["light", "thorough", "aggressive"])
def test_user_state_requires_manual_selection_even_with_presets(name, preset):
    from modules.cleanup.cleanup_presets import items_for_preset
    result = ss.ScanResult(items=[ss.ScanItem('state', 1, False, selected=False, safety='caution')])
    assert items_for_preset(preset, {'category': result}, {'category': name}) == []


def test_firefox_diagnostics_are_separate_caution_catalog_entries():
    from modules.cleanup import browser_scanner as bs
    from modules.cleanup.cleanup_scanner import load_catalog
    for names in (bs.FIREFOX_CACHE_SUBDIRS, dict(bs.FIREFOX_CACHE_CATEGORIES)):
        assert not any('crash' in name or 'minidump' in name for name in names)
    assert load_catalog()['firefox_crash_reports'].safety == 'caution'


def test_rollback_never_offers_installer_subdirectories(tree, monkeypatch):
    path = tree('windir', 'Installer/Patch')
    # The old scanner hardcoded C:\Windows, so redirect its glob as well.
    monkeypatch.setattr(ss.glob, 'glob', lambda pattern: [str(path)])
    assert ss.scan_windows_installer_rollback().items == []


@pytest.mark.parametrize('name', ['scan_print_spooler', 'scan_printer_driver_cache',
                                  'scan_print_nightmare_logs', 'scan_windows_printer_migration_cache'])
def test_unreadable_spooler_status_fails_closed(tree, monkeypatch, name):
    tree('windir', 'System32/spool/PRINTERS')
    def refused(*args, **kwargs):
        raise PermissionError('service status refused')
    monkeypatch.setattr(ss.subprocess, 'run', refused)
    assert getattr(ss, name)().items == []


@pytest.mark.parametrize('name,env,path', [
    ('scan_msi_logs', 'windir', 'Logs/MSI'),
    ('scan_powershell_logs', 'LOCALAPPDATA', 'Microsoft/Windows/PowerShell/TranscriptLogs'),
    ('scan_powershell_modules_cache', 'APPDATA', 'PowerShell/DownloadedModules'),
])
def test_handwritten_diagnostics_and_history_are_caution(tree, name, env, path):
    folder = tree(env, path)
    (folder / 'diagnostic.log').write_bytes(b'log')
    (folder / 'ConsoleHost_history.txt').write_bytes(b'command history')
    items = getattr(ss, name)().items
    assert items and all(item.safety == 'caution' for item in items)


@pytest.mark.parametrize('name', ['scan_powershell_logs', 'scan_powershell_modules_cache'])
def test_powershell_command_history_is_never_offered(tree, name):
    # The old targets looked in %LOCALAPPDATA% and found nothing; correcting
    # the path to %APPDATA% must not start deleting the Up-arrow history.
    for env in ('APPDATA', 'LOCALAPPDATA'):
        folder = tree(env, 'Microsoft/Windows/PowerShell/PSReadLine')
        (folder / 'ConsoleHost_history.txt').write_bytes(b'Get-ChildItem')
    assert not any('psreadline' in i.path.lower() for i in getattr(ss, name)().items)


def test_jetbrains_toolbox_and_unversioned_folders_are_not_ide_caches(tree):
    tree('LOCALAPPDATA', 'JetBrains/Toolbox/caches')
    tree('LOCALAPPDATA', 'JetBrains/Shared/index')
    cache = tree('LOCALAPPDATA', 'JetBrains/IntelliJIdea2025.3/index')
    assert [i.path for i in ss.scan_ide_caches().items] == [str(cache)]


def test_ide_scanner_offers_no_visual_studio_settings_or_vs_folder(tree):
    tree('LOCALAPPDATA', 'Microsoft/VisualStudio/17.0_abc/Settings')
    tree('LOCALAPPDATA', 'Microsoft/VisualStudio/17.0_abc/.vs')
    keep = tree('LOCALAPPDATA', 'Microsoft/VisualStudio/17.0_abc/ComponentModelCache')
    assert [i.path for i in ss.scan_ide_caches().items] == [str(keep)]
