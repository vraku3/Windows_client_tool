"""One place that knows how to shell out.

Written because 42 files each declared their own CREATE_NO_WINDOW, and 31 of
82 blocking run/check_output/call sites had no timeout at all — a hung sc,
netsh or dism pinned a thread-pool slot for the life of the process while
the pane that started it showed a spinner forever.
"""
import subprocess

import pytest

from core import run as run_mod


def test_a_command_that_works_comes_back_with_its_output():
    result = run_mod.run(["cmd", "/c", "echo hello"])
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_a_timeout_is_reported_not_raised():
    """A caller wanting to show "that took too long" should not have to wrap
    every call in try/except TimeoutExpired — that is exactly the discipline
    31 call sites failed to keep."""
    import time
    start = time.monotonic()
    result = run_mod.run(["cmd", "/c", "ping -n 10 127.0.0.1"], timeout=0.4)
    assert result.returncode == run_mod.TIMED_OUT
    assert run_mod.timed_out(result)
    assert "timed out" in result.stderr.lower()
    # The grandchild (ping) holds the pipe: without killing the whole tree this
    # took ping's full ~9 s. "Never hangs" means the timeout plus a short reap.
    assert time.monotonic() - start < 5


def test_input_reaches_the_process():
    result = run_mod.run(["findstr", "x"], input_text="axb\nnope\n")
    assert result.returncode == 0 and result.stdout.strip() == "axb"


def test_a_nonzero_exit_is_not_a_timeout():
    result = run_mod.run(["cmd", "/c", "exit 3"])
    assert result.returncode == 3
    assert not run_mod.timed_out(result)


def test_check_still_raises_when_the_caller_asks_for_it():
    with pytest.raises(subprocess.CalledProcessError):
        run_mod.run(["cmd", "/c", "exit 3"], check=True)


def test_there_is_a_default_timeout():
    """The whole point. A caller who forgets still cannot hang."""
    import inspect
    default = inspect.signature(run_mod.run).parameters["timeout"].default
    assert isinstance(default, (int, float))
    assert default > 0


def test_a_command_string_is_refused():
    """Passing a string would silently mean something different from
    passing a list, and this function deliberately has no shell."""
    with pytest.raises(TypeError):
        run_mod.run("cmd /c echo hello")


def test_undecodable_output_does_not_lose_the_whole_reading():
    """errors="replace": one replacement character beats a
    UnicodeDecodeError that discards everything the tool said."""
    result = run_mod.run(["cmd", "/c", "echo caf\xe9"])
    assert result.returncode == 0
    assert isinstance(result.stdout, str)


def test_powershell_runs_without_a_profile():
    """A user's profile can print banners, change the culture, or fail
    outright — none of which belongs in a reading this app takes."""
    result = run_mod.run_ps("Write-Output 'ok'")
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_powershell_reports_a_failure_rather_than_hanging_on_a_prompt():
    """-NonInteractive: a cmdlet that wants confirmation must fail, not wait
    for a keypress nobody can give it."""
    result = run_mod.run_ps("exit 4", timeout=30)
    assert result.returncode == 4
