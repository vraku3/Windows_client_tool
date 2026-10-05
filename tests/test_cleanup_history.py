# tests/test_cleanup_history.py
"""Mirrors tests/test_quick_fix_history.py's structure for the identically
-shaped cleanup_history module -- plus the total_freed_all_time() and
"zero-byte runs are not recorded" behaviour that is specific to Cleanup."""
import json
import os

from modules.cleanup import cleanup_history as ch


def test_history_path_is_under_the_real_appdata_folder():
    """Real-machine check: this box's actual %APPDATA% must be the parent
    of where the log lands, and _history_path() must be able to create
    that folder without raising (it does today via os.makedirs)."""
    path = ch._history_path()
    appdata = os.environ.get("APPDATA")
    assert appdata, "this machine has no APPDATA -- unexpected on Windows"
    assert os.path.commonpath([path, appdata]) == os.path.normpath(appdata)
    assert os.path.isdir(os.path.dirname(path))


def test_record_then_recent_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_history_path", lambda: str(tmp_path / "h.json"))
    ch.record(1024)
    ch.record(2048)
    entries = ch.recent(limit=10)
    assert len(entries) == 2
    assert entries[0]["freed_bytes"] == 2048  # most recent first
    assert entries[1]["freed_bytes"] == 1024


def test_recent_on_no_history_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_history_path", lambda: str(tmp_path / "nope.json"))
    assert ch.recent() == []


def test_record_caps_at_200_entries(tmp_path, monkeypatch):
    path = tmp_path / "h.json"
    monkeypatch.setattr(ch, "_history_path", lambda: str(path))
    for i in range(210):
        ch.record(i + 1)
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    assert len(entries) == 200
    assert entries[-1]["freed_bytes"] == 210


def test_zero_or_negative_freed_is_not_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_history_path", lambda: str(tmp_path / "h.json"))
    ch.record(0)
    ch.record(-5)
    assert ch.recent() == []


def test_total_freed_all_time_sums_every_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_history_path", lambda: str(tmp_path / "h.json"))
    assert ch.total_freed_all_time() == 0
    ch.record(500)
    ch.record(1500)
    assert ch.total_freed_all_time() == 2000


def test_total_freed_all_time_on_corrupt_file_is_zero_not_a_crash(tmp_path, monkeypatch):
    path = tmp_path / "h.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ch, "_history_path", lambda: str(path))
    assert ch.total_freed_all_time() == 0


# ── what was deleted (record_deleted / recent_deleted) ───────────────────

def test_delete_items_logs_the_paths_it_removed(tmp_path):
    from modules.cleanup.cleanup_scanner import scanners_system as ss
    from modules.cleanup.cleanup_scanner._common import ScanItem
    small, big = tmp_path / "small.tmp", tmp_path / "big.tmp"
    small.write_bytes(b"x")
    big.write_bytes(b"x" * 10)
    ss.delete_items([
        ScanItem(path=str(small), size=1, is_dir=False, selected=True, safety="safe"),
        ScanItem(path=str(big), size=10, is_dir=False, selected=True, safety="safe"),
        ScanItem(path=str(tmp_path / "unticked"), size=5, is_dir=False,
                 selected=False, safety="safe"),
    ])
    entry = ch.recent_deleted()[0]
    assert entry["count"] == 2 and entry["bytes"] == 11
    assert [p for p, _ in entry["largest"]] == [str(big), str(small)]   # largest first
    assert entry["refused"] == []


def test_a_refused_path_is_logged_as_refused_not_deleted(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import scanners_system as ss
    from modules.cleanup.cleanup_scanner._common import ScanItem
    cache = tmp_path / "Package Cache"
    cache.mkdir()
    monkeypatch.setenv("ProgramData", str(tmp_path))
    ss.delete_items([ScanItem(path=str(cache), size=1, is_dir=True,
                              selected=True, safety="caution")])
    entry = ch.recent_deleted()[0]
    assert entry["count"] == 0 and entry["refused"] == [str(cache)]


def test_only_the_largest_paths_are_kept_but_the_count_is_whole():
    ch.record_deleted([(rf"C:\t\{i}.tmp", i) for i in range(100)])
    entry = ch.recent_deleted()[0]
    assert entry["count"] == 100
    assert len(entry["largest"]) == ch.PATHS_PER_ENTRY
    assert entry["largest"][0] == [r"C:\t\99.tmp", 99]


def test_nothing_deleted_and_nothing_refused_is_not_an_entry():
    ch.record_deleted([], [])
    assert ch.recent_deleted() == []


def test_an_unwritable_log_never_fails_the_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path))   # a directory
    ch.record_deleted([(r"C:\x.tmp", 1)])                              # must not raise


def test_the_history_dialog_lists_deleted_and_refused_paths(qapp, tmp_path, monkeypatch):
    from modules.cleanup.cleanup_module import _CleanupHistoryDialog
    monkeypatch.setattr(ch, "_history_path", lambda: str(tmp_path / "h.json"))
    ch.record_deleted([(r"C:\Temp\big.tmp", 5_000_000)], [r"C:\ProgramData\Package Cache"])
    dialog = _CleanupHistoryDialog()
    top = dialog._deleted.topLevelItem(0)
    assert "deleted 1 item(s), refused 1" in top.text(0)
    children = [top.child(i).text(0) for i in range(top.childCount())]
    assert children == [r"C:\Temp\big.tmp", r"REFUSED: C:\ProgramData\Package Cache"]
