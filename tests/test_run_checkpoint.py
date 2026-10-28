"""Durable maintenance run checkpoints."""
import json

from modules.updates import run_checkpoint as rc


def test_begin_records_intent_and_items_then_finishes(tmp_path):
    run_id = rc.begin(tmp_path, ["wu", "winget"], "run_all")
    assert run_id
    data = rc.load(tmp_path)
    assert data["status"] == "running" and data["stages"] == ["wu", "winget"]
    assert rc.item(tmp_path, "winget:Acme.App", "started", "1.0 -> 2.0")
    assert rc.item(tmp_path, "winget:Acme.App", "installed", "now 2.0")
    assert rc.load(tmp_path)["items"]["winget:Acme.App"]["phase"] == "installed"
    assert rc.finish(tmp_path, "completed")
    assert rc.load(tmp_path)["status"] == "completed"
    assert rc.load(tmp_path)["finished"]


def test_an_item_without_a_run_is_not_recorded(tmp_path):
    assert rc.item(tmp_path, "winget:Acme.App", "started") is False
    assert rc.load(tmp_path) is None


def test_items_after_finish_are_ignored(tmp_path):
    rc.begin(tmp_path, ["wu"])
    rc.finish(tmp_path, "completed")
    assert rc.item(tmp_path, "wu:KB1", "late") is False


def test_items_are_capped_and_details_truncated(tmp_path):
    rc.begin(tmp_path, ["winget"])
    for i in range(rc.MAX_ITEMS + 20):
        rc.item(tmp_path, f"winget:{i}", "started", "x" * 1000)
    data = rc.load(tmp_path)
    assert len(data["items"]) == rc.MAX_ITEMS
    assert len(next(iter(data["items"].values()))["detail"]) == rc.MAX_DETAIL
    assert "winget:0" not in data["items"]


def test_a_corrupt_checkpoint_is_ignored(tmp_path):
    rc.checkpoint_path(tmp_path).parent.mkdir(parents=True)
    rc.checkpoint_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert rc.load(tmp_path) is None
    assert rc.item(tmp_path, "x", "started") is False


def test_unfinished_only_for_a_running_run_whose_owner_is_gone(tmp_path, monkeypatch):
    rc.begin(tmp_path, ["wu"])
    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: True)
    assert rc.unfinished(tmp_path) is None
    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: False)
    assert rc.unfinished(tmp_path)["status"] == "running"
    rc.finish(tmp_path, "completed")
    assert rc.unfinished(tmp_path) is None


def test_a_finished_checkpoint_is_readable_after_a_crash(tmp_path):
    rc.begin(tmp_path, ["winget"])
    rc.item(tmp_path, "winget:Acme.App", "failed", "installer exited 1")
    rc.finish(tmp_path, "completed_with_errors")
    data = json.loads(rc.checkpoint_path(tmp_path).read_text(encoding="utf-8"))
    assert data["items"]["winget:Acme.App"]["phase"] == "failed"
    assert rc.item_summary(data) == ["winget:Acme.App: failed — installer exited 1"]

def test_run_all_tab_shows_an_unfinished_run(qapp, tmp_path, monkeypatch):
    import pytest
    from types import SimpleNamespace
    from modules.updates.run_all_tab import _RunAllTab
    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: True)
    rc.begin(tmp_path, ["winget"], "run_all")
    rc.item(tmp_path, "winget:Acme.App", "started", "1.0 -> 2.0")
    tab = _RunAllTab()
    tab.set_app(SimpleNamespace(app_data_dir=str(tmp_path)))
    tab.refresh_checkpoint_note()
    assert tab._checkpoint_lbl.isHidden()      # owner still alive: still running

    monkeypatch.setattr(rc, "_owner_alive", lambda pid, started: False)
    tab.refresh_checkpoint_note()
    assert not tab._checkpoint_lbl.isHidden()
    assert "did not finish" in tab._checkpoint_lbl.text()
    assert "winget:Acme.App: started" in tab._checkpoint_lbl.text()
