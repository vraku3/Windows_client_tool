"""process_env_scan: which running processes have a given env var, and
whether their copy matches the registry. Fakes for the shape, one real
assertion against this actual machine's own process list.
"""
import os

import psutil

from modules.env_vars import process_env_scan as pes


class _FakeProc:
    def __init__(self, pid, name, username, env=None, raises=None):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "username": username}
        self._env = env
        self._raises = raises

    def environ(self):
        if self._raises is not None:
            raise self._raises
        return dict(self._env or {})


def test_readable_process_with_matching_value_is_current():
    procs = [_FakeProc(10, "a.exe", "me", {"FOO": "bar"})]
    rows = pes.scan_running_processes("FOO", "bar", process_iter=lambda: procs)
    assert len(rows) == 1
    assert rows[0].readable and rows[0].value == "bar" and not rows[0].is_stale


def test_readable_process_with_stale_value_is_flagged():
    procs = [_FakeProc(11, "b.exe", "me", {"FOO": "old"})]
    rows = pes.scan_running_processes("FOO", "new", process_iter=lambda: procs)
    assert rows[0].is_stale and rows[0].value == "old"


def test_readable_process_without_the_var_is_absent_not_stale():
    procs = [_FakeProc(12, "c.exe", "me", {"OTHER": "x"})]
    rows = pes.scan_running_processes("FOO", "new", process_iter=lambda: procs)
    assert rows[0].readable and rows[0].value is None and not rows[0].is_stale


def test_access_denied_is_its_own_state_never_absent():
    procs = [_FakeProc(13, "svc.exe", "SYSTEM", raises=psutil.AccessDenied(13))]
    rows = pes.scan_running_processes("FOO", "new", process_iter=lambda: procs)
    assert not rows[0].readable
    assert rows[0].value is None
    assert rows[0].refusal  # non-empty: a reason is recorded, not a blank refusal
    assert not rows[0].is_stale  # refused is never collapsed into "not stale"


def test_no_such_process_is_skipped_not_reported_as_a_refusal():
    procs = [_FakeProc(14, "gone.exe", "me", raises=psutil.NoSuchProcess(14))]
    rows = pes.scan_running_processes("FOO", "new", process_iter=lambda: procs)
    assert rows == []


def test_unexpected_exception_is_logged_and_reported_not_swallowed(caplog):
    class Boom(Exception):
        pass

    procs = [_FakeProc(15, "weird.exe", "me", raises=Boom("platform surprise"))]
    with caplog.at_level("WARNING"):
        rows = pes.scan_running_processes("FOO", "new", process_iter=lambda: procs)
    assert not rows[0].readable and "platform surprise" in rows[0].refusal
    assert any("environ() failed" in r.message for r in caplog.records)


def test_var_name_matched_case_insensitively():
    procs = [_FakeProc(16, "d.exe", "me", {"foo": "bar"})]
    rows = pes.scan_running_processes("FOO", "bar", process_iter=lambda: procs)
    assert rows[0].value == "bar"


def test_no_expected_value_means_present_but_never_stale():
    """The variable is not in the registry at all (`expected=None`) -- a process
    that still has an old value cannot be judged "stale" against nothing."""
    procs = [_FakeProc(17, "e.exe", "me", {"FOO": "leftover"})]
    rows = pes.scan_running_processes("FOO", None, process_iter=lambda: procs)
    assert rows[0].value == "leftover" and not rows[0].is_stale


def test_summarize_counts():
    rows = [
        pes.ProcessEnvRow(1, "a", "me", "v", True, is_stale=False),
        pes.ProcessEnvRow(2, "b", "me", "old", True, is_stale=True),
        pes.ProcessEnvRow(3, "c", "me", None, True),
        pes.ProcessEnvRow(4, "d", "SYSTEM", None, False, refusal="Access denied"),
    ]
    s = pes.summarize(rows)
    assert s.total == 4 and s.has_value == 2 and s.stale == 1 and s.refused == 1


def test_real_machine_this_process_is_readable_and_has_path():
    """This app's own interpreter process is always readable (same user), and
    it always has PATH -- a real, non-fake assertion against the live machine,
    verified independently of the module under test (`os.environ` directly)."""
    me = os.getpid()
    rows = pes.scan_running_processes(
        "PATH", os.environ.get("PATH"),
        process_iter=lambda: psutil.process_iter(["pid", "name", "username"]),
    )
    mine = next((r for r in rows if r.pid == me), None)
    assert mine is not None, "this process must appear in its own process list"
    assert mine.readable, "a process must always be able to read its own environment"
    assert mine.value == os.environ.get("PATH")


def test_real_machine_split_of_readable_vs_refused_processes():
    """Measured fact this feature depends on: on a real multi-user/SYSTEM
    Windows machine, SOME processes refuse (`AccessDenied`) and some don't --
    if this ever became all-or-nothing the refusal-vs-absent distinction
    would be untestable and the feature would have nothing to show."""
    rows = pes.scan_running_processes("PATH", os.environ.get("PATH"))
    readable = sum(1 for r in rows if r.readable)
    refused = sum(1 for r in rows if not r.readable)
    assert readable > 0
    assert refused > 0
