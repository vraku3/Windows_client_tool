import ctypes
import json
import os
from types import SimpleNamespace

import pytest

from modules.cleanup.duplicates import actions
from modules.cleanup.duplicates.models import DuplicateGroup, FileEntry, under
from modules.cleanup.cleanup_scanner import ScanItem, scanners_system


@pytest.mark.parametrize("path,root", [
    (r"\\?\C:\Windows\x.dll", r"C:\Windows"),
    (r"C:\Windows\x.dll", r"\\?\C:\Windows"),
    (r"\\?\UNC\server\share\folder\x", r"\\server\share\folder"),
])
def test_extended_paths_are_under_guard(path, root):
    assert under(path, root)
    assert not under(path, root + "other")


def test_extended_windows_path_is_always_refused(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    assert "Windows folder" in actions.always_refused(r"\\?\C:\Windows\x.dll")


@pytest.mark.parametrize("path", [r"D:\Epic Games\X\a.dll", r"E:\Games\Y\b.dll"])
def test_game_install_refused_before_reading_file(path, monkeypatch):
    monkeypatch.setattr(actions, "always_refused", lambda path: None)
    assert actions.protected_reason(SimpleNamespace(path=path)) == "Game installation files are never acted on"


def test_move_preserves_zone_identifier(tmp_path, monkeypatch):
    source = tmp_path / "download.bin"
    source.write_bytes(b"download")
    zone = b"[ZoneTransfer]\r\nZoneId=3\r\n"
    with open(str(source) + ":Zone.Identifier", "wb") as stream:
        stream.write(zone)
    destination = tmp_path / "moved"
    destination.mkdir()
    journal = []
    monkeypatch.setattr(actions, "_journal_move", lambda *args: journal.append(args))
    target = actions._move_file(str(source), str(destination))
    assert not source.exists()
    with open(target + ":Zone.Identifier", "rb") as stream:
        assert stream.read() == zone
    assert [entry[2] for entry in journal] == ["moving", "moved"]


def test_move_uses_copy_allowed_write_through_and_reports_winerror(tmp_path, monkeypatch):
    calls, journal = [], []

    def move(src, dst, flags):
        calls.append((src, dst, flags))
        ctypes.set_last_error(5)
        return False

    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(MoveFileExW=move))
    monkeypatch.setattr(actions, "_journal_move", lambda *args: journal.append(args))
    with pytest.raises(OSError) as caught:
        actions._move_file(str(tmp_path / "source"), str(tmp_path))
    assert caught.value.winerror == 5
    assert calls[0][2] == 0x2 | 0x8
    assert [entry[2] for entry in journal] == ["moving", "failed"]


def test_move_journal_durable_before_native_move(tmp_path, monkeypatch):
    from modules.cleanup import cleanup_history

    monkeypatch.setattr(cleanup_history, "_history_path", lambda: str(tmp_path / "history.json"))
    journal = tmp_path / "cleanup" / "duplicate_moves.jsonl"
    syncs = []
    real_fsync = os.fsync

    def fsync(fd):
        real_fsync(fd)
        syncs.append(fd)

    def move(src, dst, flags):
        assert len(syncs) == 1
        assert json.loads(journal.read_text().splitlines()[0])["state"] == "moving"
        return True

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(MoveFileExW=move))
    actions._move_file(str(tmp_path / "source"), str(tmp_path))
    assert len(syncs) == 2
    assert [json.loads(line)["state"] for line in journal.read_text().splitlines()] == ["moving", "moved"]


def test_move_journal_failure_prevents_native_move(tmp_path, monkeypatch):
    def unavailable(*args):
        raise OSError("journal unavailable")

    def native_move(*args, **kwargs):
        pytest.fail("native move must not run without a durable journal")

    monkeypatch.setattr(actions, "_journal_move", unavailable)
    monkeypatch.setattr(ctypes, "WinDLL", native_move)
    with pytest.raises(OSError, match="journal unavailable"):
        actions._move_file(str(tmp_path / "source"), str(tmp_path))


@pytest.mark.parametrize("mode", ["recycle", "move", "delete"])
@pytest.mark.parametrize("raises", [True, False])
def test_failed_actions_reach_history(tmp_path, monkeypatch, mode, raises):
    from modules.cleanup import cleanup_history

    entries = []
    for name in ("a", "b"):
        path = tmp_path / name
        path.write_bytes(b"same")
        entries.append(FileEntry.from_path(str(path)))
    monkeypatch.setattr(actions, "verify_pair", lambda *a, **kw: None)
    history = []
    monkeypatch.setattr(cleanup_history, "_append_entry", history.append)

    def fail(path):
        if raises:
            raise OSError("test refusal")

    destination = tmp_path / "destination"
    destination.mkdir()
    result = actions.perform_actions([DuplicateGroup(0, entries, "exact", "same")],
                                     {entries[1].path}, mode=mode,
                                     destination=str(destination), action=fail)
    assert result.outcomes[0].status == "failed"
    assert len(history) == 1
    assert history[0]["refused"] == [[entries[1].path, f"{mode}: {result.outcomes[0].reason}"]]


@pytest.mark.parametrize("stopped", [True, False, None])
@pytest.mark.parametrize("leaf", ["", "job.SPL"])
def test_print_queue_rechecks_spooler_at_delete(tmp_path, monkeypatch, stopped, leaf):
    monkeypatch.setenv("windir", str(tmp_path))
    path = tmp_path / "System32" / "spool" / "PRINTERS"
    monkeypatch.setattr(scanners_system, "_spooler_stopped", lambda: stopped)
    reason = scanners_system._refusal_reason(ScanItem(path=str(path / leaf), size=1, is_dir=not leaf))
    if stopped:
        assert reason is None
    else:
        assert reason and "Spooler" in reason


def test_print_queue_restarted_after_scan_is_not_deleted(tmp_path, monkeypatch):
    from modules.cleanup import cleanup_history

    monkeypatch.setenv("windir", str(tmp_path))
    queue = tmp_path / "System32" / "spool" / "PRINTERS"
    queue.mkdir(parents=True)
    job = queue / "job.SPL"
    job.write_bytes(b"queued document")
    monkeypatch.setattr(scanners_system, "_spooler_stopped", lambda: True)
    scanned = scanners_system.scan_print_spooler()
    assert scanned.items
    scanned.items[0].selected = True
    monkeypatch.setattr(scanners_system, "_spooler_stopped", lambda: False)
    monkeypatch.setattr(cleanup_history, "record_report", lambda *a, **kw: None)
    report, refused, _ = scanners_system.delete_items_report(scanned.items)
    assert not report.files
    assert refused and "Spooler" in refused[0][1]
    assert job.read_bytes() == b"queued document"


def test_game_folders_shared_and_bundled():
    from core.game_folders import GAME_FOLDERS
    from modules.game_mode.engine import catalog
    import pyinstaller_common

    assert catalog.GAME_FOLDERS is GAME_FOLDERS
    assert "core.game_folders" in pyinstaller_common.HIDDEN_IMPORTS
