"""health_checks: pure evaluators against fakes, plus real-machine sanity. No Qt."""
from datetime import datetime

from modules.system_health import health_checks as h
from modules.system_health.findings import Finding, full_findings


def test_pending_reboot_names_its_reasons():
    out = h.check_pending_reboot(lambda: (["CBS needs a reboot"], []))
    assert out[0].id == "pending_reboot" and "CBS" in out[0].evidence


def test_pending_reboot_unreadable_is_unknown_not_clean():
    out = h.check_pending_reboot(lambda: ([], ["RebootPending"]))
    assert out and out[0].id.endswith(":unknown")


def test_pending_reboot_clean_is_empty():
    assert h.check_pending_reboot(lambda: ([], [])) == []


def test_reasons_use_injected_readers():
    reasons, bad = h.pending_reboot_reasons(lambda p: "RebootPending" in p, lambda: ["a", "b"])
    assert len(reasons) == 2 and bad == []
    reasons, bad = h.pending_reboot_reasons(lambda p: None, lambda: None)
    assert reasons == [] and len(bad) == 3


STATUS_BAD = """Leap Indicator: 3(not synchronized)
Stratum: 0 (unspecified)
Last Successful Sync Time: unspecified
Source: Local CMOS Clock
"""
STATUS_GOOD = """Leap Indicator: 0(no warning)
Stratum: 3 (secondary reference - syncd by (S)NTP)
Last Successful Sync Time: 9/26/2026 10:00:00 AM
Source: time.windows.com
"""


def test_unsynchronised_clock_is_a_warning_with_evidence():
    out = h.evaluate_time_sync(0, STATUS_BAD)
    assert out[0].severity == "warning" and "CMOS" in out[0].evidence


def test_synchronised_clock_is_clean_and_stale_sync_is_not():
    now = datetime(2026, 9, 26, 12, 0, 0)
    assert h.evaluate_time_sync(0, STATUS_GOOD, now=now) == []
    assert h.evaluate_time_sync(0, STATUS_GOOD, now=datetime(2026, 10, 30)) != []


def test_time_service_down_and_unrunnable():
    down = h.evaluate_time_sync(1, "The following error occurred: The service has not been started. (0x80070426)")
    assert "not answering" in down[0].title
    assert h.evaluate_time_sync(None, "boom")[0].id.endswith(":unknown")


def test_disk_findings():
    good = [{"FriendlyName": "A", "HealthStatus": "Healthy", "OperationalStatus": "OK"}]
    bad = [{"FriendlyName": "B", "HealthStatus": "Warning", "OperationalStatus": "Predictive Failure"}]
    assert h.evaluate_physical_disks(good) == []
    assert h.evaluate_physical_disks(bad)[0].severity == "warning"


def test_commit_thresholds():
    gb = 1024 ** 3
    assert h.evaluate_commit((50 * gb, 100 * gb)) == []
    assert h.evaluate_commit((90 * gb, 100 * gb))[0].severity == "warning"
    assert h.evaluate_commit(None)[0].id.endswith(":unknown")
    assert h.evaluate_commit((1, 0))[0].id.endswith(":unknown")


def test_cbs_hints():
    text = "ok line\n2026-01-01 CSI Payload Corrupt file x\n[SR] Cannot repair member file [l:10]y\nfine"
    assert len(h.scan_cbs_text(text)) == 2
    assert h.scan_cbs_text("all good") == []


def test_service_findings():
    rows = [{"Name": "A", "DisplayName": "Alpha", "PathName": r"C:\Program Files\A\a.exe",
             "StartMode": "Auto", "State": "Stopped"},
            {"Name": "B", "DisplayName": "Beta", "PathName": r'"C:\Program Files\B\b.exe"',
             "StartMode": "Auto", "State": "Running"}]
    out = h.evaluate_services(rows, {"alpha": 2})
    ids = {f.id for f in out}
    assert ids == {"unquoted_service_paths", "failed_services"}
    unknown = h.evaluate_services(rows, None)
    assert any(f.id == "service_failures:unknown" for f in unknown)


def test_run_all_turns_a_crashing_check_into_an_unknown_finding():
    def boom():
        raise RuntimeError("x")
    out = h.run_all(checks=(("bad check", boom),))
    assert len(out) == 1 and out[0].id.endswith(":unknown") and "RuntimeError" in out[0].evidence


def test_finding_copy_text_has_evidence():
    f = Finding("i", "T", "D", "warning", evidence="E")
    assert "Evidence:\nE" in f.copy_text()


def test_real_machine_checks_return_findings_and_do_not_raise():
    out = h.run_all()
    assert all(isinstance(f, Finding) for f in out)
    # a machine that answers everything has few unknowns; a wall of them means a broken reader
    assert sum(1 for f in out if f.id.endswith(":unknown")) <= 3
    assert full_findings()   # includes at least the upgrade-headroom line
