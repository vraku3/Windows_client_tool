import json
from unittest.mock import Mock

from modules.game_mode.engine import session
from modules.game_mode.engine.journal import Journal
from modules.game_mode.game_mode_module import GameModeModule


def test_game_mode_logging_to_disk(qapp, tmp_path):
    m = GameModeModule()
    m._journal = Journal(folder=str(tmp_path))
    m.create_widget()

    test_line = f"{session.OK}\tTesting persistent file logging for Game Mode"
    m._append(test_line)

    log_file = tmp_path / "game_mode.log"
    assert log_file.exists(), "game_mode.log should be created on disk"
    content = log_file.read_text(encoding="utf-8")
    assert "Testing persistent file logging for Game Mode" in content
    assert "[OK]" in content


def test_game_mode_state_json_written_to_disk(qapp, tmp_path):
    m = GameModeModule()
    m._journal = Journal(folder=str(tmp_path))
    m.create_widget()

    state_file = tmp_path / "game_mode_state.json"
    assert state_file.exists(), "game_mode_state.json should be created on disk"

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "active" in data
    assert "busy" in data
    assert "state_text" in data
    assert data["active"] is False


def test_force_off_button_visibility_and_execution(qapp, tmp_path, monkeypatch):
    m = GameModeModule()
    m._journal = Journal(folder=str(tmp_path))
    m.create_widget()

    # Initial state: Game Mode is OFF
    assert m._force_off_btn.isHidden()

    # Activate journal
    m._journal.begin()
    m._show_state()
    assert not m._force_off_btn.isHidden()
    assert m._force_off_btn.isEnabled()

    # Test Force OFF execution
    calls = []
    monkeypatch.setattr(m, "_start_off", lambda force=False: calls.append(force))
    m._turn_off(force=True)
    assert calls == [True]


def test_turn_off_force_clears_failed_entries_and_finishes_journal(tmp_path):
    system = Mock()
    journal = Journal(folder=str(tmp_path))
    journal.begin()
    # Record an action that will fail restore
    valid_guid = "381b4222-f694-41f0-9685-ff5bb260df2e"
    system.plans = Mock(return_value={valid_guid: "Balanced"})
    journal.record({"kind": "plan", "before": valid_guid, "after": "guid", "done": True})
    journal.save()
    assert journal.active is True

    system.active_plan = Mock(return_value="8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
    system.set_active_plan = Mock(return_value="refused by OS")

    messages = []
    def log(level, text):
        messages.append((level, text))

    # Without force: restore fails and journal stays active
    report = session.turn_off(system, journal, log, settle=0.1, force=False)
    assert journal.active is True
    assert any("Recovery incomplete" in r["text"] for r in report)

    # With force=True: restore discards failed entries and finishes journal cleanly
    report = session.turn_off(system, journal, log, settle=0.1, force=True)
    assert journal.active is False
    assert any("Forced OFF" in r["text"] for r in report)


def test_game_mode_button_roles_toggle_with_state(qapp, tmp_path):
    m = GameModeModule()
    m._journal = Journal(folder=str(tmp_path))
    m.create_widget()

    # Initial state: Game Mode is OFF
    assert m._on_btn.objectName() == "accentButton"
    assert m._on_btn.isEnabled() is True
    assert m._off_btn.objectName() == ""
    assert m._off_btn.isEnabled() is False

    # When active: Game Mode is ON
    m._journal.begin()
    m._show_state()
    assert m._on_btn.objectName() == ""
    assert m._on_btn.isEnabled() is False
    assert m._off_btn.objectName() == "accentButton"
    assert m._off_btn.isEnabled() is True

