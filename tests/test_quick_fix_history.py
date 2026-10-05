# tests/test_quick_fix_history.py
"""Mirrors tests/test_debloat_history.py's structure for the identically
-shaped quick_fix_history module."""
import json
from datetime import datetime, timedelta

import pytest

from core.admin_utils import is_admin
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


def test_record_returns_the_entry_it_wrote(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "h.json"))
    entry = qfh.record("Flush DNS", "ok")
    assert entry["action"] == "Flush DNS"
    assert entry["outcome"] == "ok"
    assert "at" in entry


def test_last_by_action_keeps_only_the_most_recent_row_per_title(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "h.json"))
    qfh.record("Flush DNS", "ok")
    qfh.record("Reset Winsock", "error")
    qfh.record("Flush DNS", "error")   # a second, later run of the same action
    result = qfh.last_by_action()
    assert set(result) == {"Flush DNS", "Reset Winsock"}
    # The later "Flush DNS" run must win, not the first one recorded.
    assert result["Flush DNS"]["outcome"] == "error"
    assert result["Reset Winsock"]["outcome"] == "error"


def test_last_by_action_on_no_history_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "nope.json"))
    assert qfh.last_by_action() == {}


def test_recurring_actions_flags_an_action_run_three_times_in_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "h.json"))
    qfh.record("Flush DNS", "ok")
    qfh.record("Flush DNS", "ok")
    qfh.record("Flush DNS", "error")
    assert qfh.recurring_actions() == {"Flush DNS": 3}


def test_recurring_actions_ignores_an_action_below_the_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "h.json"))
    qfh.record("Flush DNS", "ok")
    qfh.record("Flush DNS", "ok")
    assert qfh.recurring_actions() == {}


def test_recurring_actions_ignores_runs_outside_the_day_window(tmp_path, monkeypatch):
    path = tmp_path / "h.json"
    monkeypatch.setattr(qfh, "_history_path", lambda: str(path))
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    entries = [
        {"at": old, "action": "Flush DNS", "outcome": "ok"},
        {"at": old, "action": "Flush DNS", "outcome": "ok"},
        {"at": old, "action": "Flush DNS", "outcome": "ok"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f)
    assert qfh.recurring_actions() == {}


def test_recurring_actions_on_no_history_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "nope.json"))
    assert qfh.recurring_actions() == {}


def test_recurring_actions_skips_a_malformed_timestamp_rather_than_raising(tmp_path, monkeypatch):
    path = tmp_path / "h.json"
    monkeypatch.setattr(qfh, "_history_path", lambda: str(path))
    entries = [
        {"at": "not-a-timestamp", "action": "Flush DNS", "outcome": "ok"},
        {"at": "", "action": "Flush DNS", "outcome": "ok"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f)
    assert qfh.recurring_actions() == {}


@pytest.mark.skipif(not is_admin(), reason=(
    "ipconfig /flushdns refuses unelevated on current Windows builds "
    "('The requested operation requires elevation.'); Quick Fix itself is "
    "requires_admin, so the button never runs it unelevated"))
def test_recurring_actions_against_a_real_flush_dns_run_on_this_machine(tmp_path, monkeypatch):
    """Real-machine assertion: run the actual catalog action (`ipconfig
    /flushdns`, via `fix_actions.flush_dns`) against a scratch history
    file three times, the same safe/idempotent command Quick Fix's own
    "Flush DNS" button runs, and confirm the trend detector picks up the
    real recorded runs rather than only ever being exercised against
    hand-built dicts."""
    from modules.quick_fix import fix_actions

    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "h.json"))
    lines = []
    for _ in range(3):
        fix_actions.flush_dns(lines.append)
        qfh.record("Flush DNS", "ok")
    assert any("flushdns" in line.lower() or "cache" in line.lower() for line in lines), (
        "ipconfig /flushdns produced no recognizable output on this machine: "
        + repr(lines))
    assert qfh.recurring_actions() == {"Flush DNS": 3}
