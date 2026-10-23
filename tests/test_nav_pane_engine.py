"""All mutation tests use memory HKCU and scratch-only links; no real registry writes."""
import copy
import os
from pathlib import Path

import pytest

from modules.nav_pane.engine import junction, onedrive
from modules.nav_pane.engine.model import (ADVANCED, CLSID, DEFAULT_ICON, HIDE, NAMESPACE,
                                         Health, Kind, Verification, NavEntry, values_for)
from modules.nav_pane.engine.operations import Operations
from modules.nav_pane.engine.registry import MemoryRegistry, optional, snapshot
from modules.nav_pane.engine.shell import verdict


class FakeLinks:
    def __init__(self):
        self.paths = {}

    def choose(self, profile, name):
        return junction.choose(profile, name)

    def create(self, path, destination):
        if path in self.paths and self.paths[path] != destination:
            raise OSError("Changed junction")
        self.paths[path] = destination

    def remove(self, path, destination):
        if path in self.paths and self.paths[path] != destination:
            raise OSError("Changed junction")
        self.paths.pop(path, None)

    def target(self, path):
        return self.paths.get(path)


@pytest.fixture
def engine(tmp_path):
    return Operations(MemoryRegistry(), tmp_path / "journal", links=FakeLinks(),
                      account_check=lambda: "", profile=str(tmp_path), root_paths=[], notify=lambda: None)


def folder(tmp_path, name="folder"):
    result = tmp_path / name
    result.mkdir()
    return str(result)


def seed_handmade(engine, path):
    guid = "{FE3197AB-09E3-49B4-84BA-AA194A0FDC32}"
    for key, name, value, kind in values_for(guid, "Aplicații", path, 48, DEFAULT_ICON):
        engine.registry.write(key, name, value, kind)
    return guid


def test_add_update_remove_exact_values_and_types(engine, tmp_path):
    path = folder(tmp_path)
    guid = engine.add_entry(path, "Aplicații țșă")
    for key, name, value, kind in values_for(guid, "Aplicații țșă", path, 48, DEFAULT_ICON):
        assert engine.registry.read(key, name) == (value, kind)
    assert engine.list_entries()[0].kind == Kind.MANAGED
    assert engine.list_entries()[0].health == Health.OK
    engine.update_entry(guid, path, "Renamed șț", 80, DEFAULT_ICON)
    assert engine.list_entries()[0].name == "Renamed șț"
    assert engine.list_entries()[0].sort_order == 80
    engine.remove_entry(guid)
    assert snapshot(engine.registry, CLSID + "\\" + guid) is None
    assert snapshot(engine.registry, NAMESPACE + "\\" + guid) is None
    assert optional(engine.registry, HIDE, guid) is None
    assert Path(path).is_dir()


def test_onedrive_accounts_and_prefix_trap(engine):
    engine.registry.write(onedrive.ACCOUNTS + r"\Business1", "UserFolder", r"C:\Work Cloud", 1)
    roots = onedrive.roots(engine.registry, {"OneDrive": r"C:\OneDrive"})
    assert onedrive.contains(r"c:\ONEDRIVE\Pictures", roots)
    assert onedrive.contains(r"C:\Work Cloud\Stuff", roots)
    assert not onedrive.contains(r"C:\OneDriveX\Pictures", roots)
    assert not onedrive.contains(r"D:\OneDrive", roots)


def test_handmade_backup_before_change_and_unsigned_dword(engine, tmp_path):
    path = folder(tmp_path)
    guid = seed_handmade(engine, path)
    engine.registry.write(CLSID + "\\" + guid + r"\ShellFolder", "Attributes", -260046771, 4)
    assert engine.registry.read(CLSID + "\\" + guid + r"\ShellFolder", "Attributes") == (0xF080004D, 4)
    assert engine.list_entries()[0].kind == Kind.HANDMADE
    engine.update_entry(guid, path, "Other")
    assert len(engine.registry.exports) == 3
    assert list((engine.journal.directory / "backups").rglob("hidden-value.json"))
    assert engine.list_entries()[0].kind == Kind.HANDMADE
    engine.remove_entry(guid)
    assert len(engine.registry.exports) == 6


def test_failed_backup_means_no_change(engine, tmp_path, monkeypatch):
    path = folder(tmp_path)
    guid = seed_handmade(engine, path)
    before = copy.deepcopy(engine.registry.keys)
    def fail(*args):
        raise PermissionError("Backup refused")
    monkeypatch.setattr(engine.registry, "export", fail)
    with pytest.raises(PermissionError, match="Backup refused"):
        engine.remove_entry(guid)
    assert engine.registry.keys == before


def test_system_entry_is_never_editable(engine, tmp_path):
    guid = "{018D5C66-4533-4307-9B53-224DE2ED1FE6}"
    engine.registry.write(NAMESPACE + "\\" + guid, "", "OneDrive - Personal", 1)
    engine.registry.write(CLSID + "\\" + guid, "", "OneDrive - Personal", 1)
    assert engine.list_entries()[0].kind == Kind.SYSTEM
    with pytest.raises(PermissionError, match="read-only"):
        engine.remove_entry(guid)
    with pytest.raises(PermissionError, match="read-only"):
        engine.update_entry(guid, folder(tmp_path), "Other")


@pytest.mark.parametrize("finish", [True, False])
def test_crash_mid_add_repairs_both_ways(engine, tmp_path, monkeypatch, finish):
    path = folder(tmp_path)
    original = engine.registry.write
    calls = []
    def crash(*args):
        calls.append(args)
        original(*args)
        if len(calls) == 5:
            raise OSError("Power lost after write")
    monkeypatch.setattr(engine.registry, "write", crash)
    with pytest.raises(OSError, match="Power lost"):
        engine.add_entry(path, "Interrupted")
    entry = engine.list_entries()[0]
    assert entry.health == Health.INCOMPLETE
    monkeypatch.setattr(engine.registry, "write", original)
    engine.repair(entry.guid, finish)
    assert not engine.journal.read()["transactions"]
    if finish:
        assert engine.list_entries()[0].health == Health.OK
    else:
        assert engine.list_entries() == []
        assert snapshot(engine.registry, CLSID + "\\" + entry.guid) is None


def test_refused_read_is_unreadable(engine, tmp_path):
    guid = seed_handmade(engine, folder(tmp_path))
    engine.registry.refused.add(CLSID + "\\" + guid)
    assert engine.list_entries()[0].health == Health.UNREADABLE
    with pytest.raises(PermissionError):
        engine.remove_entry(guid)


def test_missing_target_is_not_refusal(engine, tmp_path):
    path = folder(tmp_path)
    engine.add_entry(path, "Gone")
    os.rmdir(path)
    assert engine.list_entries()[0].health == Health.TARGET_MISSING


def test_different_account_refuses_every_mutation(engine, tmp_path):
    engine.account_check = lambda: "Process account differs from desktop account"
    with pytest.raises(PermissionError, match="differs"):
        engine.add_entry(folder(tmp_path), "No")
    with pytest.raises(PermissionError):
        engine.set_show_all_folders(True)
    assert not engine.registry.keys
    assert not engine.journal.path.exists()


@pytest.mark.parametrize("prior", [None, (0, 4), (1, 4)])
def test_show_all_exact_revert(engine, prior):
    if prior:
        engine.registry.write(ADVANCED, "NavPaneShowAllFolders", *prior)
    engine.set_show_all_folders(True)
    assert engine.show_all_folders() is True
    engine.revert_show_all_folders()
    assert optional(engine.registry, ADVANCED, "NavPaneShowAllFolders") == prior


def test_cloud_junction_recorded_before_creation(engine, tmp_path):
    cloud = folder(tmp_path, "cloud")
    engine.root_paths = [cloud]
    original = engine.links.create
    def check(path, destination):
        data = engine.journal.read()
        transaction = next(iter(data["transactions"].values()))
        assert transaction["steps"][0]["done"] is False
        original(path, destination)
    engine.links.create = check
    guid = engine.add_entry(cloud, "Cloud șț")
    record = engine.journal.read()["entries"][guid]
    assert record["junction"] in engine.links.paths
    engine.remove_entry(guid)
    assert not engine.links.paths
    assert Path(cloud).is_dir()


def test_cloud_entry_with_a_real_junction_can_be_removed(tmp_path):
    # FakeLinks never exist on disk, so realpath() could not follow them; the
    # real round trip (2026-10-23) found the removal guard resolving the link
    # to its OneDrive target and refusing every OneDrive removal.
    profile = tmp_path / "profile"
    profile.mkdir()
    cloud = folder(tmp_path, "cloud")
    engine = Operations(MemoryRegistry(), tmp_path / "journal", account_check=lambda: "",
                        profile=str(profile), root_paths=[cloud], notify=lambda: None)
    guid = engine.add_entry(cloud, "Cloud link")
    link = engine.journal.read()["entries"][guid]["junction"]
    assert os.path.lexists(link)
    engine.remove_entry(guid)
    assert not os.path.lexists(link)
    assert Path(cloud).is_dir()
    assert not engine.journal.read()["transactions"]


def test_real_junction_scratch_only(tmp_path):
    destination = folder(tmp_path, "target")
    other = folder(tmp_path, "other")
    link = str(tmp_path / "scratch-link")
    junction.create(link, destination)
    try:
        assert onedrive.normal(junction.target(link)) == onedrive.normal(destination)
        assert os.lstat(link).st_file_attributes & 2
        with pytest.raises(OSError, match="Refusing"):
            junction.remove(link, other)
        with pytest.raises(OSError, match="Refusing"):
            junction.remove(destination, destination)
    finally:
        junction.remove(link, destination)
    assert Path(destination).is_dir()
    assert not os.path.lexists(link)


def test_fresh_shell_verdict_checks_name_and_path():
    entry = NavEntry("{11111111-1111-1111-1111-111111111111}", "Aplicații", r"C:\Apps", r"C:\Apps")
    assert verdict(entry, {"listed": False}).status == Verification.NOT_LISTED
    assert verdict(entry, {"listed": True, "name": "Wrong"}).status == Verification.WRONG_NAME
    assert verdict(entry, {"listed": True, "name": entry.name, "path": r"C:\Other"}).status == Verification.COULD_NOT_CHECK
    assert verdict(entry, {"listed": True, "name": entry.name, "path": entry.target}).status == Verification.VERIFIED


def test_validation_rejects_unc_missing_and_invalid_cloud_name(engine, tmp_path):
    with pytest.raises(ValueError, match="Network"):
        engine.add_entry(r"\\server\share", "Network")
    with pytest.raises(FileNotFoundError):
        engine.add_entry(str(tmp_path / "absent"), "Missing")
    cloud = folder(tmp_path)
    engine.root_paths = [cloud]
    with pytest.raises(ValueError, match="valid"):
        engine.add_entry(cloud, "Bad/name")


@pytest.mark.parametrize("finish", [False, True])
def test_crash_during_remove_repairs_without_touching_other_entries(engine, tmp_path, monkeypatch, finish):
    path = folder(tmp_path)
    guid = engine.add_entry(path, "Remove me")
    other = seed_handmade(engine, path)
    before_other = copy.deepcopy(snapshot(engine.registry, CLSID + "\\" + other))
    original = engine.registry.delete_tree
    calls = []
    def fail(key):
        calls.append(key)
        original(key)
        if len(calls) == 1:
            raise OSError("Interrupted deletion")
    monkeypatch.setattr(engine.registry, "delete_tree", fail)
    with pytest.raises(OSError):
        engine.remove_entry(guid)
    monkeypatch.setattr(engine.registry, "delete_tree", original)
    assert next(e for e in engine.list_entries() if e.guid == guid).health == Health.INCOMPLETE
    engine.repair(guid, finish)
    assert (snapshot(engine.registry, CLSID + "\\" + guid) is None) == finish
    assert snapshot(engine.registry, CLSID + "\\" + other) == before_other


def test_rollback_refuses_external_value_change(engine, tmp_path, monkeypatch):
    path = folder(tmp_path)
    original = engine.registry.write
    def crash(key, name, value, kind):
        original(key, name, value, kind)
        raise OSError("Interrupted")
    monkeypatch.setattr(engine.registry, "write", crash)
    with pytest.raises(OSError):
        engine.add_entry(path, "Original")
    guid = next(iter(engine.journal.read()["transactions"]))
    monkeypatch.setattr(engine.registry, "write", original)
    original(CLSID + "\\" + guid, "", "Changed by someone else", 1)
    with pytest.raises(OSError, match="changed"):
        engine.repair(guid, False)
    assert engine.registry.read(CLSID + "\\" + guid)[0] == "Changed by someone else"
    assert guid in engine.journal.read()["transactions"]


def test_failed_readback_leaves_journal_incomplete(engine, tmp_path, monkeypatch):
    path = folder(tmp_path)
    def ignores_write(*args):
        return None
    monkeypatch.setattr(engine.registry, "write", ignores_write)
    with pytest.raises(FileNotFoundError):
        engine.add_entry(path, "Ignored")
    assert engine.list_entries()[0].health == Health.INCOMPLETE


def test_corrupt_journal_refuses_writes_and_is_preserved(engine, tmp_path):
    engine.journal.directory.mkdir()
    engine.journal.path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        engine.add_entry(folder(tmp_path), "No")
    assert engine.journal.path.read_text(encoding="utf-8") == "not json"
    assert not engine.registry.keys


def test_junction_collision_picks_unused_path(tmp_path):
    collision = tmp_path / ".navpane-Aplicații"
    collision.mkdir()
    picked = junction.choose(tmp_path, "Aplicații")
    assert picked == str(tmp_path / ".navpane-Aplicații (2)")
    assert collision.is_dir()


def test_verify_uses_fresh_safe_process_and_cleans_script(monkeypatch):
    import json
    from types import SimpleNamespace
    from modules.nav_pane.engine import shell
    calls = []
    entry = NavEntry("{11111111-1111-1111-1111-111111111111}", "Aplicații ' ; $name", r"C:\Folder", r"C:\Folder")
    monkeypatch.setattr(shell, "powershell_exe", lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    def run(args, **kwargs):
        calls.append(args)
        assert "-NoProfile" in args
        assert args[-4:] == [entry.guid, entry.name, entry.target, entry.resolved_target]
        script = Path(args[args.index("-File") + 1])
        assert script.exists()
        assert entry.name not in script.read_text(encoding="utf-8-sig")
        assert kwargs["timeout"] == 30
        return SimpleNamespace(returncode=0, stdout=json.dumps({"listed": True, "name": entry.name, "path": entry.target}), stderr="")
    monkeypatch.setattr(shell.subprocess, "run", run)
    assert shell.verify(entry).status == Verification.VERIFIED
    assert not Path(calls[0][calls[0].index("-File") + 1]).exists()


def test_real_account_guard_compares_process_token_with_desktop(monkeypatch):
    import win32security
    from modules.nav_pane.engine import shell
    class Token:
        # Like pywin32's PyHANDLE: Close() only, NOT a context manager. The
        # old fake had __enter__, which hid a TypeError on every real machine.
        def Close(self):
            pass
    monkeypatch.setattr(win32security, "OpenProcessToken", lambda *a: Token())
    monkeypatch.setattr(win32security, "GetTokenInformation", lambda *a: ("sid", 0))
    monkeypatch.setattr(win32security, "LookupAccountSid", lambda *a: ("Admin", "PC", 1))
    monkeypatch.setattr(shell, "desktop_account", lambda: r"PC\Owner")
    assert "differs" in shell.write_refusal()
    monkeypatch.setattr(shell, "desktop_account", lambda: r"pc\ADMIN")
    assert shell.write_refusal() == ""
    monkeypatch.setattr(shell, "desktop_account", lambda: None)
    assert "Could not identify" in shell.write_refusal()


def test_refused_hide_value_is_not_desktop_icon_missing(engine, tmp_path):
    seed_handmade(engine, folder(tmp_path))
    engine.registry.refused.add(HIDE)
    assert engine.list_entries()[0].health == Health.UNREADABLE


def test_broken_recorded_junction_health_and_removal_refusal(engine, tmp_path):
    target = folder(tmp_path, "cloud-folder")
    other = folder(tmp_path, "elsewhere")
    link = str(tmp_path / ".navpane-Test")
    junction.create(link, target)
    guid = seed_handmade(engine, link)
    data = engine.journal.read()
    data["entries"][guid] = {"owned": True, "junction": link, "resolved_target": target}
    engine.journal.save(data)
    try:
        assert engine.list_entries()[0].health == Health.OK
        junction.remove(link, target)
        junction.create(link, other)
        assert engine.list_entries()[0].health == Health.JUNCTION_BROKEN
        with pytest.raises(OSError, match="Refusing"):
            junction.remove(link, target)
    finally:
        junction.remove(link, other if onedrive.normal(junction.target(link)) == onedrive.normal(other) else target)
