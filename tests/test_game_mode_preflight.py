import copy

import pytest

from modules.game_mode.engine import catalog, session
from modules.game_mode.engine.system import ServiceState
from test_game_mode import FakeSystem, _choices


def preview(system, **kwargs):
    return session.preflight(system, _choices(preset="custom", **kwargs), own_pid=999, session_id=1)


def test_preflight_is_read_only_and_lists_selected_absent_actions():
    system = FakeSystem(admin=False)
    before = copy.deepcopy(system.__dict__)
    items = preview(system, services={"WSearch", "Spooler", "BITS", "SysMain"},
                    close={"onedrive.exe", "discord.exe", "explorer.exe"}, visuals=True)
    assert system.__dict__ == before
    actions = {i.action: i for i in items}
    assert actions["Stop WSearch"].status == "needs_admin"
    assert actions["Stop WSearch"].requires_admin
    assert actions["Stop Spooler"].status == "already_done"
    assert actions["Stop SysMain"].status == "not_found"
    assert actions["Close onedrive.exe"].status == "ready"
    assert actions["Close discord.exe"].status == "already_done"
    assert actions["Close explorer.exe"].status == "protected"
    assert any(i.category == "power" and i.status == "ready" for i in items)
    assert any(i.category == "priority" for i in items)
    assert any(i.name == "Window animations" and i.status == "ready" for i in items)
    assert any(i.category == "registry" and i.status == "already_done" for i in items)


def test_unreadable_service_registry_and_task_inventory_are_unknown(monkeypatch):
    system = FakeSystem()
    monkeypatch.setattr(system, "service", lambda _: ServiceState(None, error="Access denied"))
    monkeypatch.setattr(system, "reg_get", lambda *args: (None, None, 0))
    def refused():
        raise OSError("Access denied")
    monkeypatch.setattr(system, "tasks", refused)
    items = preview(system, services={"WSearch"})
    assert next(i for i in items if i.action == "Stop WSearch").status == "unknown"
    assert all(i.status == "unknown" for i in items if i.category in {"task", "registry"})


def test_services_with_dependents_and_games_are_protected(monkeypatch):
    system = FakeSystem()
    system.fullscreen = {14}
    monkeypatch.setattr(system, "running_dependents", lambda _: ["OtherService"])
    items = preview(system)
    assert next(i for i in items if i.action == "Stop WSearch").status == "protected"
    assert next(i for i in items if i.action == "Close deluge.exe").status == "protected"


def test_tasks_follow_preset_and_show_disabled_before_admin_requirement():
    items = session.preflight(FakeSystem(admin=False), session.preset(catalog.MAX, []),
                              own_pid=999, session_id=1)
    assert any(i.category == "task" and i.status == "needs_admin" for i in items)
    assert any(i.category == "task" and i.status == "already_done" for i in items)


def test_preflight_dialog_is_scrollable_narrow_and_cancel_default(qapp):
    from PyQt6.QtWidgets import QDialogButtonBox
    from modules.game_mode.confirm_dialog import PreflightDialog
    items = preview(FakeSystem(admin=False))
    dialog = PreflightDialog(None, items)
    dialog.resize(360, 600)
    dialog.show()
    qapp.processEvents()
    assert dialog.windowTitle() == "Game Mode Preflight"
    assert dialog.width() == 360
    assert dialog.tree.topLevelItemCount() >= 5
    assert "need admin" in dialog.summary.text()
    assert "8 seconds" in dialog.warning.text()
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).isDefault()
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isDefault()
    dialog.reject()


def test_turn_on_reads_in_worker_and_cancel_never_executes(qapp, monkeypatch, tmp_path):
    from modules.game_mode import game_mode_module as ui
    from modules.game_mode.engine.journal import Journal
    from types import SimpleNamespace
    module = ui.GameModeModule()
    module._system = FakeSystem()
    module._journal = Journal(str(tmp_path))
    module.app = SimpleNamespace()
    module.create_widget()
    workers = []
    monkeypatch.setattr(module, "_start", workers.append)
    monkeypatch.setattr(ui, "_session_id", lambda: 1)
    monkeypatch.setattr(module, "_confirm_on", lambda items: False)
    calls = []
    monkeypatch.setattr(session, "turn_on", lambda *args: calls.append(args))
    module._turn_on()
    assert module._busy
    assert len(workers) == 1
    workers[0].run()
    assert not module._busy
    assert not calls
    assert not module._journal.active


def test_unelevated_task_execution_matches_preflight_skip(tmp_path):
    from modules.game_mode.engine.journal import Journal
    journal = Journal(str(tmp_path))
    system = FakeSystem(admin=False)
    before = dict(system.task_state)
    session._On(system, journal, lambda *args: None).tasks(False, _choices())
    assert system.task_state == before
    assert not journal.actions


@pytest.mark.parametrize("confirm", [True, False])
def test_preflight_uses_snapshot_and_ignores_results_after_navigation(qapp, monkeypatch, tmp_path, confirm):
    from types import SimpleNamespace
    from modules.game_mode import game_mode_module as ui
    from modules.game_mode.engine.journal import Journal
    module = ui.GameModeModule()
    module._journal = Journal(str(tmp_path))
    module._system = FakeSystem()
    module.app = SimpleNamespace()
    module.create_widget()
    workers, executions, previews = [], [], []
    monkeypatch.setattr(module, "_start", workers.append)
    monkeypatch.setattr(ui, "_session_id", lambda: 1)
    monkeypatch.setattr(module, "_confirm_on", lambda items: previews.append(items) or True)
    monkeypatch.setattr(module, "_run", lambda work, done: executions.append(work))
    expected = module._choices.to_config()
    module._turn_on()
    module._choices.close.clear()
    if not confirm:
        module.on_deactivate()
        module._preflight_ready(workers[0], [], module._choices, 999, 1)
        assert not previews and not executions and not module._busy
    else:
        workers[0].run()
        assert previews and executions
        calls = []
        monkeypatch.setattr(session, "turn_on", lambda *args: calls.append(args))
        executions[0](SimpleNamespace(signals=None))
        assert calls[0][1].to_config() == expected


def test_preflight_failure_leaves_off_and_reports_error(qapp, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from modules.game_mode import game_mode_module as ui
    from modules.game_mode.engine.journal import Journal
    module = ui.GameModeModule()
    module._journal = Journal(str(tmp_path))
    module.app = SimpleNamespace()
    module.create_widget()
    workers = []
    monkeypatch.setattr(module, "_start", workers.append)
    monkeypatch.setattr(ui, "_session_id", lambda: 1)
    def fail(*args, **kwargs):
        raise OSError("Read refused")
    monkeypatch.setattr(session, "preflight", fail)
    module._turn_on()
    workers[0].run()
    assert not module._busy and not module._journal.active
    assert "Read refused" in module._recovery_message or not module._banner.isHidden()
