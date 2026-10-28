from datetime import datetime, timedelta
from types import SimpleNamespace

from core.timeline import load_timeline, record


def test_cleanup_measured_outcome_and_quick_fix_share_timeline(tmp_path, monkeypatch):
    from modules.cleanup import cleanup_history
    from modules.cleanup.cleanup_scanner.deleter import DeleteReport
    from modules.quick_fix import quick_fix_history
    monkeypatch.setattr(cleanup_history, "_deleted_path", lambda: str(tmp_path / "cleanup_deleted.json"))
    monkeypatch.setattr(quick_fix_history, "_history_path", lambda: str(tmp_path / "quick_fix_history.json"))
    cleanup_history.record_report(DeleteReport(files=2, freed=123), [], preset="light")
    quick_fix_history.record("Flush DNS", "error")
    events = load_timeline(tmp_path)
    assert [(e["source"], e["event"]) for e in events] == [("quick_fix", "fix_run"), ("cleanup", "preset_clean")]
    assert events[0]["severity"] == "error"
    assert events[1]["detail"]["freed_bytes"] == 123


def test_store_stage_records_trigger_without_claiming_install(tmp_path, monkeypatch):
    from core import mdm_store_trigger
    from modules.updates.stage_runners import run_store_stage
    monkeypatch.setattr(mdm_store_trigger, "trigger_store_scan", lambda **kwargs: 2)
    result = run_store_stage.__wrapped__(SimpleNamespace(app_data_dir=tmp_path), lambda line: None, lambda: False)
    assert result == {"triggered": 2}
    event = load_timeline(tmp_path)[0]
    assert event["event"] == "scan_complete"
    assert event["detail"]["triggered"] == 2


def test_timeline_view_filters_and_detail(qapp, tmp_path):
    from modules.dashboard.timeline_tab import TimelineTab
    record(tmp_path, "cleanup", "clean_complete", "Freed cache", {"freed_bytes": 123})
    record(tmp_path, "game_mode", "off_failed", "Recovery incomplete", severity="error")
    events = load_timeline(tmp_path)
    old = dict(events[0], ts=(datetime.now() - timedelta(days=40)).isoformat(), summary="Old event")
    tab = TimelineTab()
    tab._received(events + [old])
    assert tab._table.rowCount() == 2
    assert tab._table.item(0, 2).foreground().color().name() == "#ff6b6b"
    tab._select_source("cleanup")
    assert tab._table.rowCount() == 1
    tab._table.selectRow(0)
    assert '"freed_bytes": 123' in tab._detail.toPlainText()
    tab._search.setText("missing")
    assert tab._table.rowCount() == 0
    tab._search.clear()
    tab._select_source(None)
    tab._dates.setCurrentIndex(3)
    assert tab._table.rowCount() == 3
    tab.stop()
    tab.deleteLater()


def test_failed_timeline_write_does_not_fail_quick_fix_history(tmp_path, monkeypatch):
    from modules.quick_fix import quick_fix_history
    monkeypatch.setattr(quick_fix_history, "_history_path", lambda: str(tmp_path / "quick_fix_history.json"))
    (tmp_path / "timeline.jsonl").mkdir()
    assert quick_fix_history.record("Repair", "ok")["outcome"] == "ok"
    assert quick_fix_history.recent()[0]["action"] == "Repair"


def test_game_mode_round_trip_emits_on_recovery_and_off(tmp_path, monkeypatch):
    from test_game_mode import FakeSystem
    from modules.game_mode.engine import session
    from modules.game_mode.engine.journal import Journal
    monkeypatch.setenv("APPDATA", str(tmp_path / "profile"))
    monkeypatch.setattr(session.time, "sleep", lambda seconds: None)
    journal = Journal(str(tmp_path / "game_mode"))
    system = FakeSystem()
    choices = session.Choices(services=set(), close=set(), tasks=False, maintenance=False)
    session.turn_on(system, choices, journal, lambda *args: None, 9999, 1)
    session.turn_off(system, journal, lambda *args: None, settle=0)
    assert [e["event"] for e in load_timeline(tmp_path)] == ["off", "recovery", "on"]


def test_optimise_apply_and_revert_emit_changed_ids(tmp_path, monkeypatch):
    from test_optimise_os import FakeSystem
    from modules.game_mode.engine.optimise import Optimiser
    monkeypatch.setenv("APPDATA", str(tmp_path / "profile"))
    engine = Optimiser(FakeSystem(), str(tmp_path / "game_mode"))
    engine.apply({"menu_delay"})
    engine.revert()
    events = load_timeline(tmp_path)
    assert [e["event"] for e in events] == ["revert", "apply"]
    assert events[1]["detail"]["changed"] == ["menu_delay"]
