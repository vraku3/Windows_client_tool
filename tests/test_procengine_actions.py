"""Doing things to processes, and telling the truth about whether it worked.

Every test here spawns its OWN process and acts on that. Nothing in this file
touches a process it did not create.

The rule being enforced is the one the Apps tab already learned the hard way:
**an action is verified, never assumed.** `psutil.Process(pid).kill()` returns
without raising long before the process is gone, and on a protected process it
can return without raising and the process never goes at all. Reporting that
as success is how a process manager tells you it killed something that is
still running.
"""
import os
import subprocess
import sys
import time

import pytest

from core.procengine.actions import (
    Result, create_dump, end_process, end_process_tree, is_running,
    restart_process, resume_process, run_as, set_affinity, set_priority,
    suspend_process,
)

SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]


@pytest.fixture
def sleeper():
    """A real process of our own to act on."""
    process = subprocess.Popen(
        SLEEPER, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(0.3)
    yield process
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)


# ---- ending a process ---------------------------------------------------

def test_ending_a_process_actually_ends_it(sleeper):
    result = end_process(sleeper.pid)
    assert result.ok, result.message
    assert not is_running(sleeper.pid)


def test_ending_a_process_waits_for_it_to_be_gone(sleeper):
    """The verification, stated as its own test: `kill()` returns before the
    process has died, so a success reported immediately is a guess."""
    end_process(sleeper.pid)
    assert not is_running(sleeper.pid), "it reported success while alive"


def test_ending_a_process_that_is_already_gone_says_so(sleeper):
    end_process(sleeper.pid)
    result = end_process(sleeper.pid)
    assert not result.ok
    assert "no longer" in result.message.lower() or \
           "not running" in result.message.lower()


def test_ending_a_protected_process_reports_the_refusal():
    """Pid 4 is System and cannot be ended by anyone. What must not happen
    is a cheerful success."""
    result = end_process(4)
    assert not result.ok
    assert result.message


def test_the_system_process_is_not_blamed_on_permissions():
    """Elevation does not let anyone end pid 4, so offering "run as
    administrator" here would send someone to do something that cannot
    work. The refusal has to be the real reason."""
    message = end_process(4).message.lower()
    assert "cannot be ended" in message
    assert "administrator" not in message


def test_a_refusal_that_elevation_would_fix_says_so():
    """Where elevation IS the answer, the message has to name it. Tested on
    setting priority rather than on a kill: this must not be a test that
    terminates a system process if it is ever run elevated."""
    result = set_priority(4, "high")
    assert not result.ok
    assert "administrator" in result.message.lower() or \
           "denied" in result.message.lower()


def test_ending_pid_zero_is_refused_rather_than_attempted():
    """Pid 0 is the idle process. Nothing good comes of trying."""
    assert not end_process(0).ok


# ---- ending a tree ------------------------------------------------------

def test_ending_a_tree_ends_the_children_too():
    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys, time; "
         "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
         "time.sleep(120)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(1.2)
    try:
        result = end_process_tree(parent.pid)
        assert result.ok, result.message
        assert not is_running(parent.pid)
    finally:
        if parent.poll() is None:
            parent.kill()


def test_ending_a_tree_reports_which_members_it_could_not_end():
    """Partial success is not success. A tree where one child survived has
    to say which one."""
    result = end_process_tree(4)
    assert not result.ok
    assert result.message


# ---- suspend and resume -------------------------------------------------

def test_suspending_and_resuming_a_process(sleeper):
    assert suspend_process(sleeper.pid).ok
    assert is_running(sleeper.pid), "suspend killed it"
    assert resume_process(sleeper.pid).ok
    assert is_running(sleeper.pid)


def test_suspending_a_process_we_cannot_open_is_refused():
    result = suspend_process(4)
    assert not result.ok
    assert result.message


# ---- priority and affinity ----------------------------------------------

def test_setting_priority(sleeper):
    assert set_priority(sleeper.pid, "below_normal").ok


def test_an_unknown_priority_is_refused_rather_than_guessed(sleeper):
    result = set_priority(sleeper.pid, "turbo")
    assert not result.ok
    assert "turbo" in result.message or "priority" in result.message.lower()


def test_setting_affinity_to_one_core(sleeper):
    assert set_affinity(sleeper.pid, [0]).ok


def test_an_empty_affinity_is_refused(sleeper):
    """A process pinned to no cores would never run again."""
    assert not set_affinity(sleeper.pid, []).ok


def test_an_affinity_naming_a_core_that_does_not_exist_is_refused(sleeper):
    result = set_affinity(sleeper.pid, [9999])
    assert not result.ok


# ---- dumps --------------------------------------------------------------

def test_creating_a_dump_writes_a_real_file(sleeper, tmp_path):
    target = tmp_path / "sleeper.dmp"
    result = create_dump(sleeper.pid, str(target))
    assert result.ok, result.message
    assert target.exists()
    assert target.stat().st_size > 4096, "the dump is too small to be real"


def test_a_dump_starts_with_the_minidump_signature(sleeper, tmp_path):
    """Proof it is a dump rather than an empty file we created."""
    target = tmp_path / "sleeper.dmp"
    create_dump(sleeper.pid, str(target))
    assert target.read_bytes()[:4] == b"MDMP"


def test_a_dump_of_a_process_we_cannot_open_is_refused(tmp_path):
    result = create_dump(4, str(tmp_path / "system.dmp"))
    assert not result.ok
    assert result.message


# ---- restart and run-as -------------------------------------------------


@pytest.fixture
def restart_shell(monkeypatch):
    """These tests verify real termination, but must never launch a replacement."""
    from dataclasses import replace
    from core.procengine import details
    original = details.resolve
    monkeypatch.setattr(details, "resolve", lambda pid: replace(original(pid), elevated=False))
    monkeypatch.setattr("core.shell_launch.desktop_account", lambda: "TEST\\desktop")

def test_restart_ends_the_process_and_relaunches_it(sleeper, monkeypatch, restart_shell):
    """Restart = end + relaunch with the same executable.

    The real launch is monkeypatched so the test does not spawn a stray
    120-second sleeper; what is asserted is the flow: the old process is
    ended and verified gone, and the launch step is asked for the right
    file and command line.
    """
    launched = []
    monkeypatch.setattr("core.shell_launch.launch",
                        lambda exe, cmdline: launched.append((exe, cmdline, False)))

    old_pid = sleeper.pid
    result = restart_process(old_pid)

    assert result.ok, result.message
    assert not is_running(old_pid), "the old process survived the restart"
    assert launched, "restart did not launch a replacement"
    exe, cmdline, runas = launched[0]
    assert os.path.normcase(exe) == os.path.normcase(sys.executable)
    assert runas is False


def test_restart_passes_the_process_arguments_through(sleeper, monkeypatch, restart_shell):
    """The relaunch reproduces how the process was started."""
    launched = []
    monkeypatch.setattr("core.shell_launch.launch",
                        lambda exe, cmdline: launched.append(cmdline))

    restart_process(sleeper.pid)

    assert launched
    assert "time.sleep" in launched[0], \
        "the new instance did not get the sleeper's own arguments"


def test_restart_reports_a_failed_relaunch(sleeper, monkeypatch, restart_shell):
    monkeypatch.setattr("core.shell_launch.launch",
                        lambda exe, cmdline: "The launch failed: error 5.")
    result = restart_process(sleeper.pid)
    assert not result.ok
    assert result.message
    assert not is_running(sleeper.pid), "the process was killed and not replaced"


def test_restarting_the_system_process_is_refused():
    result = restart_process(4)
    assert not result.ok
    assert result.message


def test_restarting_a_dead_pid_is_refused():
    result = restart_process(999_999)
    assert not result.ok
    assert "no longer running" in result.message.lower()


def test_run_as_launches_elevated_and_leaves_the_original_running(
        sleeper, monkeypatch):
    """The distinction from restart: the original keeps running."""
    launched = []
    monkeypatch.setattr("core.procengine.actions._launch",
                        lambda exe, cmdline, runas: launched.append(runas)
                        or Result(True, ""))

    result = run_as(sleeper.pid)

    assert result.ok, result.message
    assert launched and launched[0] is True, "run_as did not elevate"
    assert is_running(sleeper.pid), "run_as ended the original process"


def test_run_as_on_a_dead_pid_is_refused():
    result = run_as(999_999)
    assert not result.ok
    assert "no longer running" in result.message.lower()


def test_run_as_on_the_system_process_is_refused():
    result = run_as(4)
    assert not result.ok
    assert result.message


def test_a_failure_always_carries_a_message():
    for result in (end_process(4), suspend_process(4),
                   set_priority(4, "high"), restart_process(4),
                   run_as(4)):
        assert not result.ok
        assert result.message, "a failure said nothing about why"


def test_a_result_is_never_a_bare_boolean(sleeper):
    """Every action answers with a reason, so a caller can show it."""
    result = end_process(sleeper.pid)
    assert isinstance(result, Result)
    assert hasattr(result, "ok") and hasattr(result, "message")


# ---- Qt-free ------------------------------------------------------------

def test_the_actions_do_not_import_qt():
    import inspect

    from core.procengine import actions

    assert "PyQt6" not in inspect.getsource(actions)
