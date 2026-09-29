"""task_history: reading and enabling Microsoft-Windows-TaskScheduler/Operational."""
from types import SimpleNamespace

from modules.scheduled_tasks import task_history as h


def _run(returncode=0, stdout="", stderr=""):
    return lambda *a, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_parse_enabled_reads_the_wevtutil_gl_line():
    assert h._parse_enabled("name: X\nenabled: true\ntype: Operational\n") is True
    assert h._parse_enabled("enabled: false\n") is False
    assert h._parse_enabled("no such line here") is None
    assert h._parse_enabled("enabled: sideways\n") is None


def test_log_enabled_reflects_the_command(monkeypatch):
    assert h.log_enabled(run=_run(0, "enabled: true\n")) is True
    assert h.log_enabled(run=_run(0, "enabled: false\n")) is False


def test_log_enabled_is_none_on_refusal_not_false():
    """Access-denied or a missing wevtutil must never read the same as OFF."""
    assert h.log_enabled(run=_run(5, "", "Access is denied.")) is None

    def raises(*a, **kw):
        raise OSError("no such file")
    assert h.log_enabled(run=raises) is None


def test_enable_log_succeeds_only_when_the_readback_agrees():
    calls = []

    def run(args, **kw):
        calls.append(args)
        if args[1] == "sl":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="enabled: true\n", stderr="")

    ok, msg = h.enable_log(run=run)
    assert ok and "now on" in msg
    assert calls[0][:2] == ["wevtutil", "sl"] and calls[1][:2] == ["wevtutil", "gl"]


def test_enable_log_reports_the_set_call_being_refused():
    ok, msg = h.enable_log(run=_run(5, "", "Access is denied."))
    assert not ok and "Access is denied" in msg and "elevated" in msg


def test_enable_log_reports_when_windows_ignored_the_set_call():
    """`sl` can exit 0 without the channel actually flipping on -- accepted
    but not applied is a real distinct outcome from a clean refusal."""
    def run(args, **kw):
        if args[1] == "sl":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="enabled: false\n", stderr="")
    ok, msg = h.enable_log(run=run)
    assert not ok and "still reads as off" in msg


# ---- real machine ------------------------------------------------------------------

def test_real_machine_history_log_reads_a_real_answer():
    """Confirmed live 2026-09-29: `wevtutil gl
    Microsoft-Windows-TaskScheduler/Operational` answers `enabled: false` on
    this machine -- Windows ships the channel off. Asserting the exact value
    would break the moment someone actually clicks "Enable task history" (in
    this app or in taskschd.msc itself), so this pins the shape of a real
    answer -- a definite True/False, never a silent None -- rather than the
    one value measured at the time this was written."""
    assert h.log_enabled() in (True, False)
