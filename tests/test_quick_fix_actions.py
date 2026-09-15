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
    (fix_actions.run_sfc, ["sfc", "/scannow"]),
    (fix_actions.flush_dns, ["ipconfig", "/flushdns"]),
    (fix_actions.reset_winsock, ["netsh", "winsock", "reset"]),
])
def test_the_repair_runs_the_command_it_says_it_does(action, expected, ran, output):
    lines, cb = output
    action(cb)
    assert expected in ran, f"ran {ran} instead"


def test_dism_asks_for_restorehealth_not_just_a_scan():
    """CheckHealth and ScanHealth only report; RestoreHealth is the one that
    repairs, and the action's own description promises a repair."""
    calls = []
    import modules.quick_fix.fix_actions as fa
    original = fa._run_cmd
    fa._run_cmd = lambda cmd, cb, input_bytes=None: (calls.append(list(cmd)), 0)[1]
    try:
        fa.run_dism(lambda _line: None)
    finally:
        fa._run_cmd = original
    flat = " ".join(calls[0]).lower()
    assert "/restorehealth" in flat


def test_chkdsk_is_scheduled_not_run_now(ran, output):
    """chkdsk on the system volume cannot run live; it answers a Y/N prompt
    and schedules itself. Sending that answer is the whole trick, and
    without it the action hangs on a prompt nobody can see."""
    lines, cb = output
    captured = {}

    def fake_run_cmd(cmd, output_cb, input_bytes=None):
        captured["cmd"] = list(cmd)
        captured["input"] = input_bytes
        return 0

    import modules.quick_fix.fix_actions as fa
    original = fa._run_cmd
    fa._run_cmd = fake_run_cmd
    try:
        fa.run_chkdsk(cb)
    finally:
        fa._run_cmd = original

    assert "chkdsk" in captured["cmd"]
    assert captured["input"], "no answer sent to chkdsk's Y/N prompt"


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
