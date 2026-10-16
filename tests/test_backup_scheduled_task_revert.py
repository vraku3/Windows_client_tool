"""A disabled scheduled task is really re-enabled by a revert.

revert_step had no branch for "scheduled_task": the step fell through to
"reverted" while the task stayed disabled."""
import subprocess

import pytest

from core.backup_service import BackupService


def _run_recorder(calls, rc=0):
    def fake(args, **kw):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, rc, "", "" if rc == 0 else "ERROR: denied")
    return fake


def test_a_task_that_was_enabled_is_enabled_again(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _run_recorder(calls))
    BackupService._revert_scheduled_task(r"\Microsoft\Windows\Foo", "Ready")
    assert calls == [["schtasks", "/change", "/tn", r"\Microsoft\Windows\Foo", "/enable"]]


def test_a_task_that_was_already_disabled_is_left_alone(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _run_recorder(calls))
    BackupService._revert_scheduled_task(r"\Foo", "Disabled")
    assert calls == []


@pytest.mark.parametrize("before", ["Unknown", None, ""])
def test_an_unrecorded_state_is_an_error_not_a_silent_success(monkeypatch, before):
    monkeypatch.setattr(subprocess, "run", _run_recorder([]))
    with pytest.raises(RuntimeError):
        BackupService._revert_scheduled_task(r"\Foo", before)


def test_a_refused_enable_is_an_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _run_recorder([], rc=1))
    with pytest.raises(RuntimeError):
        BackupService._revert_scheduled_task(r"\Foo", "Ready")
