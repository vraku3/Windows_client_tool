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
