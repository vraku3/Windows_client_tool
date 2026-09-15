"""System Health's own history log -- mirrors updates/history_writer.py's
shape (a capped JSON array), a separate file since this module's entries
don't fit Update Center's own {ts, freed, updates, wg} shape.
"""
import json
import os

from modules.system_health.history import append_run, load_history, MAX_ENTRIES


def test_load_history_on_a_fresh_install_returns_empty_list(tmp_path):
    assert load_history(str(tmp_path)) == []


def test_append_run_then_load_round_trips(tmp_path):
    append_run(str(tmp_path), action="scan_health", command="dism /Online /Cleanup-Image /ScanHealth",
              returncode=0, findings_count=0)

    history = load_history(str(tmp_path))

    assert len(history) == 1
    assert history[0]["action"] == "scan_health"
    assert history[0]["returncode"] == 0
    assert "ts" in history[0]


def test_history_is_capped_at_max_entries(tmp_path):
    for i in range(MAX_ENTRIES + 10):
        append_run(str(tmp_path), action="findings_refresh", command="", returncode=0, findings_count=i)

    history = load_history(str(tmp_path))

    assert len(history) == MAX_ENTRIES
    # Oldest entries are dropped, newest kept.
    assert history[-1]["findings_count"] == MAX_ENTRIES + 9


def test_load_history_on_corrupt_json_returns_empty_list_not_a_crash(tmp_path):
    health_dir = os.path.join(str(tmp_path), "system_health")
    os.makedirs(health_dir)
    with open(os.path.join(health_dir, "history.json"), "w") as f:
        f.write("{not valid json")

    assert load_history(str(tmp_path)) == []
