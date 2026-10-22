"""Temp-only contents exception and one cancellable protection pre-walk."""
import os

import pytest

from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    from modules.cleanup import cleanup_history
    from modules.cleanup.cleanup_scanner import exclusions
    monkeypatch.setattr(cleanup_history, "record_report", lambda *a, **k: None)
    monkeypatch.setattr(exclusions, "list_exclusions", lambda: [])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])
    for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMDATA", "windir", "SystemRoot"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    monkeypatch.setenv("TMP", str(tmp_path / "tmp"))
    return tmp_path


@pytest.mark.parametrize("container", [False, True])
def test_only_temp_container_can_clean_beside_profile(scratch, container):
    root = scratch / ("temp" if container else "profile")
    profile = root / "test-tree" / "Default"
    store = profile / "Local Storage"
    store.mkdir(parents=True)
    (store / "tokens").write_bytes(b"keep")
    prefs = profile / "Preferences"
    prefs.write_bytes(b"keep")
    junk = root / "old.tmp"
    junk.write_bytes(b"junk")
    report, refused, _ = ss.delete_items_report([ScanItem(str(root), 12, True)])
    assert prefs.exists() and (store / "tokens").exists()
    assert junk.exists() is not container
    assert bool(refused) is not container
    assert report.freed == (4 if container else 0)


def test_profile_root_itself_is_refused(scratch):
    root = scratch / "profile"
    (root / "IndexedDB").mkdir(parents=True)
    prefs = root / "Preferences"
    prefs.write_text("keep")
    report, refused, _ = ss.delete_items_report([ScanItem(str(root), 4, True)])
    assert refused and report.freed == 0 and prefs.exists()


def test_temp_precheck_visits_each_directory_once(scratch, monkeypatch):
    root = scratch / "temp"
    deep = root / "one" / "two" / "three"
    deep.mkdir(parents=True)
    (deep / "old.tmp").write_bytes(b"junk")
    counts = {}
    scandir = os.scandir
    def counted(path):
        key = os.path.normcase(os.fspath(path)).removeprefix('\\\\?\\')
        counts[key] = counts.get(key, 0) + 1
        return scandir(path)
    monkeypatch.setattr(ss.os, "scandir", counted)
    report, refused, _ = ss.delete_items_report([ScanItem(str(root), 4, True)])
    assert not refused and report.freed == 4
    # One protection pre-walk, one deleting walk; no recursive walk per ancestor.
    assert counts and max(counts.values()) <= 2


def test_cancel_during_temp_precheck_does_not_delete(scratch, monkeypatch):
    root = scratch / "temp"
    for i in range(10):
        folder = root / str(i)
        folder.mkdir(parents=True)
        (folder / "old.tmp").write_bytes(b"keep")
    calls = []
    scandir = os.scandir
    def counted(path):
        calls.append(path)
        return scandir(path)
    monkeypatch.setattr(ss.os, "scandir", counted)
    report, _, _ = ss.delete_items_report([ScanItem(str(root), 40, True)],
                                         cancelled=lambda: len(calls) >= 2)
    assert report.cancelled and report.freed == 0
    assert len(calls) <= 2
    assert len(list(root.rglob("old.tmp"))) == 10
