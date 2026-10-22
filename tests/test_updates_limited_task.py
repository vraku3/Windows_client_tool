from types import SimpleNamespace as NS

import pytest


@pytest.mark.parametrize("state,result,expected", [(3, 0, 0), (3, 5, 5), (4, 267009, None)])
def test_limited_task_result_wait_and_deletion(monkeypatch, state, result, expected):
    import win32com.client
    from core import limited_task
    deleted, registered = [], []
    definition = NS(Principal=NS(), Settings=NS(), RegistrationInfo=NS(), Actions=NS(Create=lambda kind: NS()))
    task = NS(State=state, LastTaskResult=result, Run=lambda _: None)
    folder = NS(GetTasks=lambda flags: [], RegisterTaskDefinition=lambda *a: registered.append(a) or task,
                DeleteTask=lambda *a: deleted.append(a))
    service = NS(Connect=lambda: None, NewTask=lambda _: definition, GetFolder=lambda _: folder)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _: service)
    monkeypatch.setattr(limited_task, "_current_user_sid", lambda: "S-1-5-21-test")
    ticks = iter([0, 0, 601, 602])
    monkeypatch.setattr(limited_task.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(limited_task.time, "sleep", lambda _: None)
    rc, reason = limited_task.run_limited("winget.exe", ["upgrade", "--id", "Acme.App"])
    assert rc == expected
    assert definition.Principal.RunLevel == 0 and definition.Principal.LogonType == 3
    assert registered[0][3] == "S-1-5-21-test" and len(deleted) == 1
    if expected is None:
        assert "timed out" in reason


def test_user_upgrade_rejects_command_text_before_task(monkeypatch):
    from modules.updates import winget_updater
    rc, reason, output = winget_updater._run_user_upgrade('Acme.App" & calc.exe')
    assert rc is None and "identifier" in reason
