import json
from contextlib import nullcontext

import pytest

from modules.game_mode.engine import session_history as history


@pytest.fixture(autouse=True)
def isolate_machine_operation_lock(monkeypatch, tmp_path):
    from modules.game_mode.engine import session

    monkeypatch.setenv("APPDATA", str(tmp_path))
    if hasattr(session, "operation_lock"):
        monkeypatch.setattr(session, "operation_lock", lambda *args, **kwargs: nullcontext())


def test_start_end_persist_counts_and_details(tmp_path, monkeypatch):
    monkeypatch.setattr(history.time, "time", lambda: 1000.0)
    history.record_session_start(tmp_path, "Maximum", 3, 100, 20)
    assert history.load_history(tmp_path)[0]["ended"] is None
    monkeypatch.setattr(history.time, "time", lambda: 4600.0)
    history.record_session_end(tmp_path, [{"level": "ok", "text": "Restored"}], 60, 12)
    entry = history.load_history(tmp_path)[0]
    assert entry["duration_s"] == 3600
    assert entry["restore_ok"] is True
    assert entry["handles_before"] - entry["handles_after"] == 40
    assert entry["processes_after"] == 12
    assert entry["preset"] == "Maximum"
    assert json.loads((tmp_path / "game_mode/session_history.json").read_text()) == [entry]


def test_partial_restore_retry_updates_same_session(tmp_path):
    history.record_session_start(tmp_path, "Recommended", 2, 100, 20, started=1000)
    history.record_session_end(tmp_path, [{"level": "bad", "text": "Service refused"}],
                               70, 15, started=1000, complete=False, restored=1)
    partial = history.load_history(tmp_path)[0]
    assert partial["restore_ok"] is False
    assert partial["ended"] is None
    assert partial["restore_issues"] == ["Service refused"]
    history.record_session_end(tmp_path, [{"level": "ok", "text": "Service restored"}],
                               70, 15, started=1000)
    entries = history.load_history(tmp_path)
    assert len(entries) == 1
    assert entries[0]["restore_ok"] is True
    assert entries[0]["restore_issues"] == []
    assert entries[0]["ended"] is not None


def test_cap_and_stats_exclude_unfinished_sessions(tmp_path):
    for index in range(102):
        history.record_session_start(tmp_path, "Recommended", 0, 0, 0, started=index + 1)
    entries = history.load_history(tmp_path)
    assert len(entries) == 100
    assert entries[0]["started"] == 3
    assert history.session_stats(entries)["average_duration_s"] is None
    entries[0].update(ended=3603, duration_s=3600, restore_ok=True)
    entries[1].update(ended=7204, duration_s=7200, restore_ok=False)
    stats = history.session_stats(entries)
    assert stats["total_sessions"] == 100
    assert stats["average_duration_s"] == 5400
    assert stats["success_rate"] == 50


def test_failed_atomic_replace_keeps_previous_history(tmp_path, monkeypatch):
    history.record_session_start(tmp_path, "Recommended", 0, 0, 0)
    before = history.load_history(tmp_path)
    def refused(*args):
        raise PermissionError("blocked")
    monkeypatch.setattr(history.os, "replace", refused)
    with pytest.raises(PermissionError):
        history.record_session_start(tmp_path, "Maximum", 0, 0, 0)
    assert history.load_history(tmp_path) == before
    assert not list((tmp_path / "game_mode").glob("*.tmp"))


def test_corrupt_history_is_reported_and_not_overwritten(tmp_path):
    folder = tmp_path / "game_mode"
    folder.mkdir()
    path = folder / "session_history.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        history.record_session_start(tmp_path, "Maximum", 0, 0, 0)
    assert path.read_text() == "broken"


def test_on_off_and_retry_survive_restart(tmp_path, monkeypatch):
    from test_game_mode import FakeSystem, _choices, _quiet
    from modules.game_mode.engine import session
    from modules.game_mode.engine.journal import Journal

    monkeypatch.setattr(session.time, "sleep", lambda _: None)
    journal = Journal(str(tmp_path / "game_mode"))
    system = FakeSystem()
    data = session.turn_on(system, _choices(), journal, _quiet, 999, 1)
    entries = history.load_history(tmp_path)
    assert len(entries) == 1
    assert entries[0]["handles_after"] == data["handles_after"]
    assert entries[0]["services_stopped"] > 0
    assert entries[0]["actions"]
    restart = Journal(journal.folder)
    assert restart.load()
    start_service = system.start_service
    system.start_service = lambda _: "refused"
    session.turn_off(system, restart, _quiet, settle=0)
    assert history.load_history(tmp_path)[0]["restore_ok"] is False
    assert history.load_history(tmp_path)[0]["ended"] is None
    system.start_service = start_service
    session.turn_off(system, restart, _quiet, settle=0)
    entries = history.load_history(tmp_path)
    assert len(entries) == 1
    assert entries[0]["restore_ok"] is True
    assert entries[0]["ended"] is not None
    assert entries[0]["handles_after"] == data["handles_after"]
    assert not restart.active


def test_history_tab_lazy_load_and_details(tmp_path, qapp):
    from modules.game_mode.history_tab import HistoryTab

    history.record_session_start(tmp_path, "Maximum", 1, 100, 20,
                                 actions=[{"kind": "service_stop", "name": "WSearch", "done": True}])
    history.record_session_end(tmp_path, [{"level": "bad", "text": "WSearch refused"}], 50, 10,
                               complete=False, restored=1)
    tab = HistoryTab(str(tmp_path / "game_mode"))
    assert not tab._loaded
    assert tab._table.rowCount() == 0
    tab.activate()
    assert tab._loaded
    assert tab._table.rowCount() == 1
    tab._table.selectRow(0)
    assert "WSearch refused" in tab._detail.toPlainText()
    assert "Maximum" in tab._detail.toPlainText()
    assert "1 sessions" in tab._summary.text()
    assert tab._table.item(0, 5).text() == "Partial restore"
    tab.close()


def test_history_tab_reports_unreadable_data(tmp_path, qapp):
    from modules.game_mode.history_tab import HistoryTab

    folder = tmp_path / "game_mode"
    folder.mkdir()
    (folder / "session_history.json").write_text("broken")
    tab = HistoryTab(str(folder))
    tab.activate()
    assert not tab._loaded
    assert "could not be loaded" in tab._error.text()
    tab.close()


def test_journal_finish_also_records_history(tmp_path):
    from modules.game_mode.engine.journal import Journal

    journal = Journal(str(tmp_path / "game_mode"))
    journal.begin()
    history.record_session_start(tmp_path, "Recommended", 0, 100, 20,
                                 started=journal.data["started"])
    journal.finish([{"level": "ok", "text": "All restored"}])
    assert history.load_history(tmp_path)[0]["restore_ok"] is True


def test_history_failure_does_not_block_restoration(tmp_path, monkeypatch):
    from test_game_mode import FakeSystem, _choices, _quiet
    from modules.game_mode.engine import session
    from modules.game_mode.engine.journal import Journal

    monkeypatch.setattr(session.time, "sleep", lambda _: None)
    journal = Journal(str(tmp_path / "game_mode"))
    system = FakeSystem()
    session.turn_on(system, _choices(), journal, _quiet, 999, 1)
    def refused(*args, **kwargs):
        raise PermissionError("history blocked")
    monkeypatch.setattr(history, "_write", refused)
    report = session.turn_off(system, journal, _quiet, settle=0.1)
    assert not journal.active
    assert any("history could not be saved" in r["text"] for r in report)
    assert (tmp_path / "game_mode/last_restore.json").is_file()


@pytest.mark.parametrize("field,value", [("handles_before", "broken"), ("actions", [None]),
                                          ("restore_issues", "broken"), ("duration_s", float("inf"))])
def test_invalid_history_fields_are_reported(tmp_path, field, value):
    history.record_session_start(tmp_path, "Maximum", 0, 0, 0)
    path = tmp_path / "game_mode/session_history.json"
    entries = json.loads(path.read_text())
    entries[0][field] = value
    path.write_text(json.dumps(entries))
    with pytest.raises(ValueError):
        history.load_history(tmp_path)


def test_corrupt_recovery_journal_records_failed_restore(tmp_path):
    from test_game_mode import FakeSystem, _quiet
    from modules.game_mode.engine import session
    from modules.game_mode.engine.journal import Journal

    history.record_session_start(tmp_path, "Maximum", 2, 100, 20)
    journal = Journal(str(tmp_path / "game_mode"))
    (tmp_path / "game_mode/session.json").write_text("broken")
    journal.load()
    session.turn_off(FakeSystem(), journal, _quiet, settle=0)
    entry = history.load_history(tmp_path)[0]
    assert entry["restore_ok"] is False
    assert entry["ended"] is None
    assert "could not be read" in entry["restore_issues"][0]
