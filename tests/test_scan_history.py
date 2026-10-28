"""Scan snapshots and their change summary."""
from modules.updates import scan_history as sh


def offer(ident, name, installed="1.0", available="2.0"):
    return {"winget_id": ident, "name": name,
            "installed_version": installed, "available_version": available}


def test_first_scan_is_not_a_wall_of_new_offers(tmp_path):
    result = sh.record(tmp_path, "winget", [offer("A", "A"), offer("B", "B")])
    assert result["first"] is True and result["new"] == []
    assert sh.last_changes(tmp_path, "winget") is None


def test_new_changed_and_gone_are_reported(tmp_path):
    sh.record(tmp_path, "winget", [offer("A", "A", available="2.0"),
                                   offer("B", "B", available="2.0")])
    result = sh.record(tmp_path, "winget", [offer("A", "A", available="3.0"),
                                            offer("C", "C", available="1.0")])
    assert [row["id"] for row in result["new"]] == ["C"]
    assert result["changed"][0]["id"] == "A" and result["changed"][0]["was"] == "2.0"
    assert [row["id"] for row in result["gone"]] == ["B"]
    previous = sh.last_changes(tmp_path, "winget")
    assert previous and previous["changed"][0]["offered"] == "3.0"


def test_sources_are_independent(tmp_path):
    sh.record(tmp_path, "winget", [offer("A", "A")])
    sh.record(tmp_path, "wu", [offer("KB1", "Fix")])
    result = sh.record(tmp_path, "wu", [offer("KB2", "Other fix")])
    assert [row["id"] for row in result["new"]] == ["KB2"]
    assert sh.last_changes(tmp_path, "winget") is None


def test_snapshots_are_capped_and_corruption_is_ignored(tmp_path):
    for i in range(sh.MAX_SNAPSHOTS + 5):
        sh.record(tmp_path, "winget", [offer(f"A{i}", f"A{i}")])
    import json
    data = json.loads(sh.history_path(tmp_path).read_text(encoding="utf-8"))
    assert len(data["sources"]["winget"]) == sh.MAX_SNAPSHOTS
    sh.history_path(tmp_path).write_text("{bad json", encoding="utf-8")
    assert sh.last_changes(tmp_path, "winget") is None
    assert sh.record(tmp_path, "winget", [])["first"] is True


def test_change_lines_are_readable():
    result = {"first": False, "new": [{"name": "A", "offered": "2"}],
              "changed": [{"name": "B", "was": "1", "offered": "2"}],
              "gone": [{"name": "C", "offered": "1"}]}
    lines = sh.change_lines(result)
    assert any(line.startswith("NEW") and "A" in line for line in lines)
    assert any("1 → 2" in line for line in lines)
    assert any(line.startswith("GONE") for line in lines)
