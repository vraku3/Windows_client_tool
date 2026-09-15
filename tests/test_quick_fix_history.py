# tests/test_quick_fix_history.py
"""Mirrors tests/test_debloat_history.py's structure for the identically
-shaped quick_fix_history module."""
import json

from modules.quick_fix import quick_fix_history as qfh


def test_record_then_recent_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "h.json"))
    qfh.record("Flush DNS", "ok")
    qfh.record("Reset Winsock", "error")
    entries = qfh.recent(limit=10)
    assert len(entries) == 2
    assert entries[0]["action"] == "Reset Winsock"  # most recent first
    assert entries[0]["outcome"] == "error"
    assert entries[1]["action"] == "Flush DNS"


def test_recent_on_no_history_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "nope.json"))
    assert qfh.recent() == []


def test_record_caps_at_200_entries(tmp_path, monkeypatch):
    path = tmp_path / "h.json"
    monkeypatch.setattr(qfh, "_history_path", lambda: str(path))
    for i in range(210):
        qfh.record(f"Action {i}", "ok")
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    assert len(entries) == 200
    assert entries[-1]["action"] == "Action 209"
