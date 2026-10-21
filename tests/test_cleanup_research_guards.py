"""Deletion boundaries from the 2026-10-21 research audit."""
import os
import subprocess

import pytest

from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss


@pytest.fixture
def roots(tmp_path, monkeypatch):
    for key in ("windir", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "USERPROFILE"):
        folder = tmp_path / key
        folder.mkdir()
        monkeypatch.setenv(key, str(folder))
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])
    return tmp_path


PROTECTED = [
    ("LOCALAPPDATA", "Steam/steamapps/common/Game"),
    ("LOCALAPPDATA", "Programs/Steam/downloads"),
    ("APPDATA", "Epic/EpicGamesLauncher/Data/Manifests"),
    ("PROGRAMDATA", "XboxLiveDeviceInfo"),
    ("windir", "System32/spool/drivers/x64"),
    ("windir", "System32/wbem/Repository/FS"),
    ("windir", "WinSxS/Temp"),
    ("windir", "Installer/$PatchCache$"),
    ("windir", "Registration"),
    ("LOCALAPPDATA", "Microsoft/Windows/INetCache/Content.Outlook"),
    ("APPDATA", "Notepad++/backup"),
    ("APPDATA", "FileZilla"),
    ("LOCALAPPDATA", "Microsoft/VisualStudio/17.0_abc/Settings"),
    ("LOCALAPPDATA", "Microsoft/IdentityOLTCache"),
    ("LOCALAPPDATA", "Steam/htmlcache/Default"),
    ("APPDATA", "App/blob_storage"),
    ("APPDATA", "App/Service Worker/ScriptCache"),
    ("APPDATA", "App/Service Worker/CacheStorage"),
]


@pytest.mark.parametrize("env,relative", PROTECTED)
def test_refuses_root_contents_and_containing_folder(roots, env, relative):
    folder = roots / env / relative
    folder.mkdir(parents=True)
    payload = folder / "payload.bin"
    payload.write_bytes(b"keep")
    for path, is_dir in ((folder, True), (payload, False), (folder.parent, True)):
        assert ss._refusal_reason(ScanItem(str(path), 4, is_dir)), str(path)
    assert ss.delete_items([ScanItem(str(folder), 4, True)]) == (0, 1)
    assert payload.read_bytes() == b"keep"
    assert ss._protected_entry(str(folder), True)
    assert ss._protected_entry(str(payload), False)


@pytest.mark.parametrize("relative", ["Steam/steamapps", "Microsoft/IdentityOLTCache"])
def test_refuses_junction_alias_and_written_path(roots, tmp_path, relative):
    target = roots / "LOCALAPPDATA" / relative
    target.mkdir(parents=True)
    (target / "data").write_bytes(b"keep")
    alias = tmp_path / "innocent-cache"
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    try:
        assert ss._refusal_reason(ScanItem(str(alias), 4, True))
        assert ss._protected_entry(str(alias / "data"), False)
    finally:
        os.rmdir(alias)


@pytest.mark.parametrize("relative", ["Steam/steamapps", "Microsoft/IdentityOLTCache"])
def test_protected_written_name_cannot_escape_through_a_junction(roots, tmp_path, relative):
    target = tmp_path / "disposable-looking"
    target.mkdir()
    (target / "data").write_bytes(b"keep")
    alias = roots / "LOCALAPPDATA" / relative
    alias.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    try:
        assert ss._refusal_reason(ScanItem(str(alias), 4, True))
        assert ss._protected_entry(str(alias / "data"), False)
    finally:
        os.rmdir(alias)


@pytest.mark.parametrize("leaf", ["Cache", "Code Cache", "GPUCache"])
def test_htmlcache_rebuildable_leaves_are_allowed(roots, leaf):
    folder = roots / "LOCALAPPDATA" / "Steam/htmlcache/Default" / leaf
    folder.mkdir(parents=True)
    (folder / "data").write_bytes(b"cache")
    assert ss._refusal_reason(ScanItem(str(folder), 5, True)) is None
    assert ss.delete_items([ScanItem(str(folder), 5, True)]) == (1, 0)


def test_installer_files_require_current_orphan_proof(roots, monkeypatch):
    folder = roots / "windir" / "Installer"
    folder.mkdir()
    used = folder / "used.msp"
    orphan = folder / "orphan.msi"
    for path in (used, orphan):
        path.write_bytes(b"package")
    monkeypatch.setattr(ss, "_referenced_installer_packages", lambda: None)
    assert ss._refusal_reason(ScanItem(str(orphan), 7, False))
    monkeypatch.setattr(ss, "_referenced_installer_packages",
                        lambda: {os.path.normcase(os.path.normpath(str(used)))})
    assert ss._refusal_reason(ScanItem(str(used), 7, False))
    assert ss._refusal_reason(ScanItem(str(orphan), 7, False)) is None
    assert ss.delete_items([ScanItem(str(orphan), 7, False)]) == (1, 0)
    assert used.exists()


def test_walk_guard_protects_data_when_preflight_cannot_enumerate(roots, monkeypatch):
    parent = roots / "APPDATA" / "OtherApp"
    protected = parent / "blob_storage"
    protected.mkdir(parents=True)
    (protected / "data").write_bytes(b"keep")
    (parent / "cache.tmp").write_bytes(b"discard")
    monkeypatch.setattr(ss, "held_user_data", lambda *a, **k: (False, False))
    ss.delete_items([ScanItem(str(parent), 11, True)])
    assert (protected / "data").read_bytes() == b"keep"


def test_orphan_result_does_not_authorize_a_later_referenced_package(roots, monkeypatch):
    folder = roots / 'windir' / 'Installer'
    folder.mkdir()
    package = folder / 'candidate.msp'
    package.write_bytes(b'package')
    monkeypatch.setattr(ss, '_referenced_installer_packages', lambda: set())
    result = ss.scan_orphaned_installer_packages()
    assert [item.path for item in result.items] == [str(package)]
    monkeypatch.setattr(ss, '_referenced_installer_packages',
                        lambda: {os.path.normcase(str(package))})
    assert ss.delete_items(result.items) == (0, 1)
    assert package.read_bytes() == b'package'


def test_walk_guard_uses_the_root_resolution_for_entries(roots, tmp_path, monkeypatch):
    """Below a junctioned ancestor the walk still sees where an entry leads,
    even with the preflight check bypassed."""
    target = roots / "APPDATA" / "FileZilla"
    (target / "sub").mkdir(parents=True)
    (target / "sub" / "sitemanager.xml").write_bytes(b"keep")
    alias = tmp_path / "harmless"
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    try:
        monkeypatch.setattr(ss, "_refusal_reason", lambda item: None)
        ss.delete_items([ScanItem(str(alias / "sub"), 4, True)])
        assert (target / "sub" / "sitemanager.xml").read_bytes() == b"keep"
    finally:
        os.rmdir(alias)


def test_walk_resolves_once_per_item_not_once_per_file(roots, monkeypatch):
    folder = roots / "LOCALAPPDATA" / "SomeApp" / "Cache"
    folder.mkdir(parents=True)
    for i in range(60):
        (folder / f"f{i}").write_bytes(b"x")
    calls = []
    real = ss._resolved
    monkeypatch.setattr(ss, "_resolved", lambda p: calls.append(p) or real(p))
    assert ss.delete_items([ScanItem(str(folder), 60, True)]) == (1, 0)
    assert not any(folder.iterdir())
    assert len(calls) < 10, len(calls)


def test_whole_inetcache_is_refused_while_its_ie_folder_is_not(roots):
    inet = roots / "LOCALAPPDATA" / "Microsoft/Windows/INetCache"
    (inet / "Content.Outlook" / "ABC").mkdir(parents=True)
    (inet / "Content.Outlook" / "ABC" / "edited.docx").write_bytes(b"edits")
    (inet / "IE" / "XYZ").mkdir(parents=True)
    (inet / "IE" / "XYZ" / "page.htm").write_bytes(b"web")
    assert ss._refusal_reason(ScanItem(str(inet), 9, True))
    assert ss.delete_items([ScanItem(str(inet / "IE"), 3, True)]) == (1, 0)
    assert (inet / "Content.Outlook" / "ABC" / "edited.docx").read_bytes() == b"edits"
    assert not (inet / "IE" / "XYZ" / "page.htm").exists()


@pytest.mark.parametrize("relative", ["D/SteamLibrary/steamapps/common/Game",
                                      "D/Games/Steam/downloads/123"])
def test_game_state_is_refused_at_custom_library_locations(tmp_path, roots, relative):
    folder = tmp_path / relative
    folder.mkdir(parents=True)
    (folder / "data").write_bytes(b"keep")
    assert ss._refusal_reason(ScanItem(str(folder), 4, True))
    assert ss._refusal_reason(ScanItem(str(tmp_path / "D"), 4, True))
