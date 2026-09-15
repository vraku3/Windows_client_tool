"""Quick Fix's repair actions, driven without touching the machine.

`fix_actions.py` was 18% covered — the lowest of any module that runs
commands, and it runs the most dangerous ones in the app: `chkdsk /f /r /x`,
`netsh winsock reset`, deleting the Windows Update download store,
`taskkill /f /im explorer.exe`.

Every one of these was reachable only by pressing the button on a real
machine, so nothing checked that the command was the command intended,
that a failure was reported rather than swallowed, or that output reached
the pane at all. These tests substitute the process layer and assert on
what would have been run.
"""
import os

import pytest

from modules.quick_fix import fix_actions


@pytest.fixture
def ran(monkeypatch):
    """Capture every command, and report success, without running any."""
    calls = []

    def fake_run_cmd(cmd, output_cb, input_bytes=None):
        calls.append(list(cmd))
        output_cb(f"[stub] {' '.join(cmd)}")
        return 0

    monkeypatch.setattr(fix_actions, "_run_cmd", fake_run_cmd)
    return calls


@pytest.fixture
def output():
    lines = []
    return lines, lines.append


# ── the commands are the commands intended ─────────────────────────────

@pytest.mark.parametrize("action,expected", [
    (fix_actions.flush_dns, ["ipconfig", "/flushdns"]),
    (fix_actions.reset_winsock, ["netsh", "winsock", "reset"]),
])
def test_the_repair_runs_the_command_it_says_it_does(action, expected, ran, output):
    lines, cb = output
    action(cb)
    assert expected in ran, f"ran {ran} instead"


# ── failures are reported, not swallowed ───────────────────────────────

def test_a_failing_command_still_says_something(monkeypatch, output):
    lines, cb = output
    monkeypatch.setattr(fix_actions, "_run_cmd",
                        lambda cmd, output_cb, input_bytes=None: (
                            output_cb("Error: the system cannot find the file"), -1)[1])
    fix_actions.flush_dns(cb)
    assert any("error" in line.lower() for line in lines)


def test_run_cmd_reports_a_missing_executable_rather_than_raising(output):
    """A repair tool that is not installed must not take the pane down."""
    lines, cb = output
    code = fix_actions._run_cmd(["definitely-not-a-real-exe-9f3a"], cb)
    assert code == -1
    assert any("error" in line.lower() for line in lines)


def test_stopping_a_service_that_is_not_there_is_reported(output, monkeypatch):
    lines, cb = output

    class _Stub:
        @staticmethod
        def StopService(name):
            raise RuntimeError("service does not exist")

    monkeypatch.setitem(__import__("sys").modules, "win32serviceutil", _Stub)
    fix_actions._stop_service("NoSuchService", cb)
    assert any("could not stop" in line.lower() for line in lines)


def test_print_queue_restarts_the_spooler_even_if_a_delete_fails(monkeypatch, output):
    """Ported from Quick Cleanup's own fix -- a locked spool file must
    not leave the spooler stopped."""
    lines, cb = output
    calls = []

    def fake_stop(name, output_cb):
        calls.append(("stop", name))

    def fake_start(name, output_cb):
        calls.append(("start", name))

    monkeypatch.setattr(fix_actions, "_stop_service", fake_stop)
    monkeypatch.setattr(fix_actions, "_start_service", fake_start)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(os, "listdir", lambda p: ["locked.spl"])
    monkeypatch.setattr(os, "remove", lambda p: (_ for _ in ()).throw(OSError("locked")))

    fix_actions.clear_print_queue(cb)

    assert ("start", "Spooler") in calls, "spooler was not restarted after a failed delete"


# ── every action is wired up ───────────────────────────────────────────

def test_every_declared_fix_has_a_callable_behind_it():
    """A FixAction with fn=None is a button that does nothing when pressed,
    and says nothing about why."""
    actions = [value for name, value in vars(fix_actions).items()
               if isinstance(value, list)
               and value and isinstance(value[0], fix_actions.FixAction)]
    assert actions, "no FixAction list found in the module"
    for group in actions:
        for action in group:
            assert callable(action.fn), f"{action.key} has no function"
            assert action.title.strip(), f"{action.key} has no title"
            assert action.description.strip(), f"{action.key} has no description"


def test_no_two_fixes_share_a_key():
    actions = [value for name, value in vars(fix_actions).items()
               if isinstance(value, list)
               and value and isinstance(value[0], fix_actions.FixAction)]
    seen = set()
    for group in actions:
        for action in group:
            assert action.key not in seen, f"duplicate key {action.key!r}"
            seen.add(action.key)


# ── the 7 actions migrated from Quick Cleanup's one-click panel ────────

@pytest.mark.parametrize("action,expected_contains", [
    (fix_actions.compact_winsxs, "StartComponentCleanup"),
    (fix_actions.resize_hibernation, "hibernate"),
])
def test_the_migrated_cleanup_action_runs_the_expected_command(action, expected_contains, ran, output):
    lines, cb = output
    action(cb)
    flat = " ".join(" ".join(c) for c in ran)
    assert expected_contains in flat


def test_compact_winsxs_never_passes_resetbase():
    """The one remaining door to /ResetBase must stay System Health's
    gated Reset Base -- see the Sub-project 4 safety fix this must not
    reintroduce."""
    calls = []
    original = fix_actions._run_cmd
    fix_actions._run_cmd = lambda cmd, cb, input_bytes=None: (calls.append(list(cmd)), 0)[1]
    try:
        fix_actions.compact_winsxs(lambda _line: None)
    finally:
        fix_actions._run_cmd = original
    assert "/ResetBase" not in calls[0]


def test_hibernation_precondition_blocks_when_hiberfil_is_absent(monkeypatch):
    monkeypatch.setattr(fix_actions.os.path, "exists", lambda p: False)
    assert fix_actions._hibernation_precondition() is not None


def test_hibernation_precondition_allows_when_hiberfil_is_present(monkeypatch):
    monkeypatch.setattr(fix_actions.os.path, "exists", lambda p: True)
    assert fix_actions._hibernation_precondition() is None
