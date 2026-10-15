from datetime import datetime, timezone
from modules.security_dashboard import log_readiness as lr

GL = """name: System
enabled: true
type: Admin
owningPublisher:
isolation: Application
channelAccess: O:BAG:SYD:(A;;0xf0007;;;SY)
logging:
  logFileName: %SystemRoot%\\System32\\Winevt\\Logs\\System.evtx
  retention: false
  autoBackup: false
  maxSize: 20971520
publishing:
  fileMax: 1
"""
XML = "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><TimeCreated SystemTime='2026-09-04T18:23:11.5310000Z'/></System></Event>"


def fake_run(gl=GL, qe=XML, gl_rc=0, qe_rc=0):
    def run(args):
        if args[1] == "gl":
            return gl_rc, gl if gl_rc == 0 else "", "" if gl_rc == 0 else gl
        return qe_rc, qe if qe_rc == 0 else "", "" if qe_rc == 0 else qe
    return run


def test_parse_gl_reads_the_logging_section():
    d = lr.parse_gl(GL)
    assert (d["enabled"], d["maxSize"], d["retention"], d["autoBackup"]) == ("true", "20971520", "false", "false")


def test_a_circular_20mb_log_with_its_oldest_event():
    s = lr.read_log("System", run=fake_run())
    assert (s.present, s.enabled, s.max_bytes, s.mode) == (True, True, 20971520, "circular")
    assert s.oldest == datetime(2026, 9, 4, 18, 23, 11, 531000, tzinfo=timezone.utc)
    assert round(s.coverage_days(datetime(2026, 10, 15, 18, 23, 11, 531000, tzinfo=timezone.utc))) == 41


def test_a_missing_channel_is_absent_not_refused():
    s = lr.read_log("Microsoft-Windows-Sysmon/Operational",
                    run=fake_run(gl="Failed to read configuration for log Microsoft-Windows-Sysmon/Operational. The specified channel could not be found.", gl_rc=15007))
    assert s.present is False and s.refused is None


def test_access_denied_is_refused_never_empty():
    s = lr.read_log("Security", run=fake_run(qe="Access is denied.", qe_rc=5))
    assert s.refused and s.oldest is None and s.coverage_days(datetime.now(timezone.utc)) is None


def test_archive_and_retain_modes():
    assert lr.read_log("X", run=fake_run(gl=GL.replace("autoBackup: false", "autoBackup: true"))).mode == "archive"
    assert lr.read_log("X", run=fake_run(gl=GL.replace("retention: false", "retention: true"))).mode == "retain"


def test_gl_access_denied_is_refused_with_unknown_presence():
    s = lr.read_log("Security", run=fake_run(gl="Access is denied.", gl_rc=5))
    assert s.present is None and s.refused == "needs administrator rights"


def test_empty_log_has_no_oldest_and_no_refusal():
    s = lr.read_log("X", run=fake_run(qe=""))
    assert s.oldest is None and s.refused is None and s.present is True


def test_timeout_and_crash_never_raise():
    def boom(args):
        raise TimeoutError("x")

    s = lr.read_log("X", run=boom)
    assert s.refused == "wevtutil timed out" and s.present is None

    def bad(args):
        raise OSError("no wevtutil")

    assert lr.read_log("X", run=bad).refused


def test_read_all_covers_important_logs():
    assert [s.name for s in lr.read_all(run=fake_run())] == list(lr.IMPORTANT_LOGS)


def test_real_system_log_is_readable_unelevated():
    s = lr.read_log("System")
    assert s.present is True and s.max_bytes and s.oldest is not None
