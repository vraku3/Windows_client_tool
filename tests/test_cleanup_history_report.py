import json

from modules.cleanup import cleanup_history as ch
from modules.cleanup.cleanup_scanner.deleter import DeleteReport


def test_record_report_keeps_measured_bytes_and_every_kind_of_leftover(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    report = DeleteReport(freed=2_000, files=3, locked=[r"C:\T\a.log"], denied=[r"C:\T\x"], kept_recent=4)
    ch.record_report(report, [(r"C:\Pkg\LocalCache", "holds an app's sign-in")],
                     owners={"Teams.exe": 1})
    entry = json.loads((tmp_path / "log.json").read_text())[-1]
    assert (entry["bytes"], entry["count"], entry["locked_count"], entry["kept_recent"]) == (2000, 3, 1, 4)
    assert entry["owners"] == {"Teams.exe": 1}
    assert entry["denied"] == [r"C:\T\x"] and entry["denied_count"] == 1
    assert entry["refused"] == [[r"C:\Pkg\LocalCache", "holds an app's sign-in"]]


def test_record_report_keeps_the_largest_removed_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    removed = [(rf"C:\t\{i}.tmp", i) for i in range(1, 101)]
    ch.record_report(DeleteReport(freed=5050, files=100), [], removed=removed)
    entry = json.loads((tmp_path / "log.json").read_text())[-1]
    assert len(entry["largest"]) == ch.PATHS_PER_ENTRY
    assert entry["largest"][0] == [r"C:\t\100.tmp", 100]


def test_summarise_names_what_was_left_and_why():
    entry = {"bytes": 2_254_857_830, "count": 1204, "locked_count": 312,
             "owners": {"Teams.exe": 300, "WhatsApp.exe": 12}, "denied_count": 0,
             "kept_recent": 9, "refused": [["p", "r"]] * 3, "cancelled": False}
    text = ch.summarise(entry)
    assert text.startswith("Freed 2.1 GB (1,204 files).")
    assert "312 in use by Teams.exe, WhatsApp.exe" in text
    assert "9 newer than a day kept" in text and "3 refused" in text


def test_an_old_entry_still_summarises():
    old = {"at": "2026-10-02T18:26:40", "count": 834, "bytes": 4278726303,
           "largest": [[r"C:\x", 1]], "refused": [r"C:\y"]}
    assert ch.summarise(old) == "Freed 4.0 GB (834 items). 1 refused."


def test_nothing_to_record_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    ch.record_report(DeleteReport(), [])
    assert not (tmp_path / "log.json").exists()


import pytest


@pytest.mark.parametrize("payload,starts_over", [(b"{}", True), (b"null", True), (b"\xff\xfe\x00bad", True)])
def test_a_malformed_log_never_raises_and_is_started_over(tmp_path, monkeypatch, payload, starts_over):
    log = tmp_path / "log.json"
    log.write_bytes(payload)
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(log))
    ch.record_report(DeleteReport(freed=1, files=1), [])
    data = json.loads(log.read_text())
    assert isinstance(data, list) and len(data) == 1


def test_a_failing_path_resolution_does_not_raise(monkeypatch):
    def boom():
        raise OSError("no appdata")
    monkeypatch.setattr(ch, "_deleted_path", boom)
    ch.record_report(DeleteReport(freed=1, files=1), [])


def test_a_zero_byte_deleted_file_is_still_in_largest(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    from modules.cleanup.cleanup_scanner import scanners_system as ss
    from modules.cleanup.cleanup_scanner._common import ScanItem
    empty = tmp_path / "empty.tmp"
    empty.write_bytes(b"")
    ss.delete_items([ScanItem(path=str(empty), size=0, is_dir=False, selected=True, safety="safe")])
    entry = ch.recent_deleted()[0]
    assert [str(empty), 0] in entry["largest"]


def test_summarise_mentions_cloud_only_files():
    entry = {"bytes": 10, "count": 1, "locked_count": 0, "cloud_only": 3}
    assert "3 cloud-only files left." in ch.summarise(entry)
