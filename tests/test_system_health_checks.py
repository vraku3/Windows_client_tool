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


def test_evaluate_cbs_text_unresolved_and_resolved():
    corrupt_text = "2026-01-01 CSI Payload Corrupt file x\n[SR] Cannot repair member file [l:10]y"
    findings = h.evaluate_cbs_text(corrupt_text, tail_bytes_mb=4, path="test.log")
    assert len(findings) == 1
    assert findings[0].id == "cbs_corruption"
    assert findings[0].severity == "warning"
    assert "2 line(s)" in findings[0].title

    resolved_text = (
        "2026-01-01 CSI Payload Corrupt file x\n"
        "[SR] Cannot repair member file [l:10]y\n"
        "2026-01-02 [SR] Verify and Repair Transaction completed. All files and registry keys listed in this transaction have been successfully repaired\n"
    )
    findings_resolved = h.evaluate_cbs_text(resolved_text, tail_bytes_mb=4, path="test.log")
    assert len(findings_resolved) == 1
    assert findings_resolved[0].id == "cbs_resolved"
    assert findings_resolved[0].severity == "info"
    assert "repairs succeeded" in findings_resolved[0].title

    clean_text = "2026-01-01 All clear\n2026-01-02 Nothing bad"
    assert h.evaluate_cbs_text(clean_text) == []


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


def test_evaluate_problem_devices_clean():
    assert h.evaluate_problem_devices([]) == []
    assert h.evaluate_problem_devices([{"Name": "Good", "ConfigManagerErrorCode": 0}]) == []


def test_evaluate_problem_devices_warning():
    rows = [
        {"Name": "Audio Controller", "DeviceID": "PCI\\VEN_1234", "ConfigManagerErrorCode": 28},
        {"Name": "GPU", "DeviceID": "PCI\\VEN_5678", "ConfigManagerErrorCode": 43},
    ]
    out = h.evaluate_problem_devices(rows)
    assert len(out) == 1
    assert out[0].id == "problem_devices"
    assert out[0].severity == "warning"
    assert "2 devices" in out[0].title
    assert "Audio Controller" in out[0].evidence
    assert "Code 28" in out[0].evidence
    assert "Code 43" in out[0].evidence
    assert out[0].jump == "Driver Manager"


def test_evaluate_problem_devices_single():
    rows = [{"Name": "Bad Device", "ConfigManagerErrorCode": 10}]
    out = h.evaluate_problem_devices(rows)
    assert len(out) == 1
    assert "Problem device: Bad Device (Code 10)" in out[0].title
    assert out[0].severity == "warning"


def test_evaluate_problem_devices_all_disabled():
    rows = [{"Name": "Disabled Cam", "ConfigManagerErrorCode": 22}]
    out = h.evaluate_problem_devices(rows)
    assert len(out) == 1
    assert out[0].severity == "info"
    assert "disabled" in out[0].detail.lower()


def test_run_all_turns_a_crashing_check_into_an_unknown_finding():
    def boom():
        raise RuntimeError("x")
    out = h.run_all(checks=(("bad check", boom),))
    assert len(out) == 1 and out[0].id.endswith(":unknown") and "RuntimeError" in out[0].evidence


def test_finding_copy_text_has_evidence():
    f = Finding("i", "T", "D", "warning", evidence="E")
    assert "Evidence:\nE" in f.copy_text()


def test_evaluate_certificates():
    now = datetime(2026, 11, 14, 12, 0, 0)
    rows = [
        {
            "Subject": "CN=Expired VPN Cert",
            "Store": "CurrentUser\\My",
            "NotAfter": "2026-11-01T10:00:00",
            "Thumbprint": "A1B2C3D4E5F611223344",
        },
        {
            "Subject": "CN=Expiring Server Cert",
            "Store": "LocalMachine\\My",
            "NotAfter": "2026-11-20T10:00:00",
            "Thumbprint": "B2C3D4E5F6A155667788",
        },
        {
            "Subject": "CN=Good Cert",
            "Store": "LocalMachine\\My",
            "NotAfter": "2027-11-14T10:00:00",
            "Thumbprint": "C3D4E5F6A1B299001122",
        },
    ]
    findings = h.evaluate_certificates(rows, now=now)
    assert len(findings) == 2

    expired = [f for f in findings if "expired" in f.id]
    assert len(expired) == 1
    assert expired[0].severity == "warning"
    assert "Expired certificate in CurrentUser\\My" in expired[0].title
    assert "Expired VPN Cert" in expired[0].title
    assert expired[0].jump == "Certificates"

    expiring = [f for f in findings if "expiring" in f.id]
    assert len(expiring) == 1
    assert expiring[0].severity == "warning"
    assert "Certificate expiring in 5 day(s)" in expiring[0].title
    assert expiring[0].jump == "Certificates"


def test_evaluate_windows_update_failures_empty_is_healthy():
    assert h.evaluate_windows_update_failures([]) == []


def test_evaluate_windows_update_failures_os_update_grouping():
    rows = [
        {
            "Time": "2026-11-14 12:00:19",
            "Message": "Installation Failure: Windows failed to install the following update with error 0x800F0991: 2026-11 Security Update (KB5132061) (26300.9974).",
        },
        {
            "Time": "2026-11-13 17:49:38",
            "Message": "Installation Failure: Windows failed to install the following update with error 0x800F0993: 2026-11 Security Update (KB5132061) (26300.9974).",
        },
    ]
    findings = h.evaluate_windows_update_failures(rows)
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "wu_failed_kb5132061"
    assert "failed 2 time(s)" in f.title
    assert "KB5132061" in f.title
    assert f.severity == "warning"
    assert f.jump == "Updates"
    assert "0x800F0991" in f.evidence
    assert "0x800F0993" in f.evidence
    assert "PSFX_E_APPLY_FORWARD_DELTA_FAILED" in f.evidence


def test_evaluate_windows_update_failures_store_apps():
    rows = [
        {
            "Time": "2026-11-09 14:45:15",
            "Message": "Installation Failure: Windows failed to install the following update with error 0x80073D02: 9PC1H9VN18CM-Microsoft.StartExperiencesApp.",
        },
        {
            "Time": "2026-11-09 14:24:57",
            "Message": "Installation Failure: Windows failed to install the following update with error 0x80073D02: 9MSSGKG348SP-MicrosoftWindows.Client.WebExperience.",
        },
    ]
    findings = h.evaluate_windows_update_failures(rows)
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "wu_failed_store_apps"
    assert "2 Store package update(s) failed" in f.title
    assert f.severity == "info"
    assert f.jump == "Updates"
    assert "StartExperiencesApp" in f.detail
    assert "Client.WebExperience" in f.detail


def test_check_windows_update_failures_refused_is_unknown(monkeypatch):
    monkeypatch.setattr(h, "_ps_json", lambda script, timeout: (None, "access denied"))
    findings = h.check_windows_update_failures()
    assert len(findings) == 1
    assert findings[0].id == "windows_update_failures:unknown"
    assert findings[0].severity == "info"
    assert findings[0].jump == "Updates"
    assert "access denied" in findings[0].evidence


def test_evaluate_driver_load_failures_empty_is_healthy():
    assert h.evaluate_driver_load_failures([]) == []


def test_evaluate_driver_load_failures_generates_warning_finding():
    rows = [
        {
            "Time": "2026-11-14 11:53:37",
            "Message": "The driver \\Driver\\WUDFRd failed to load.\r\nDevice: HID\\VID_1395&PID_005C&MI_03&Col03\\c&2385ed86&0&0002\r\nStatus: 0xC0000365",
        },
        {
            "Time": "2026-11-13 09:09:55",
            "Message": "The driver \\Driver\\WUDFRd failed to load.\r\nDevice: HID\\VID_1395&PID_005C&MI_03&Col03\\c&2385ed86&0&0002\r\nStatus: 0xC0000365",
        },
    ]
    findings = h.evaluate_driver_load_failures(rows)
    assert len(findings) == 1
    f = findings[0]
    assert "driver_load_failed" in f.id
    assert f.severity == "warning"
    assert f.jump == "Driver Manager"
    assert "WUDFRd" in f.title
    assert "failed to load" in f.title
    assert "2 time(s)" in f.title
    assert "0xC0000365" in f.evidence
    assert "STATUS_DEVICE_NOT_READY" in f.evidence


def test_check_driver_load_failures_refused_is_unknown(monkeypatch):
    monkeypatch.setattr(h, "_ps_json", lambda script, timeout: (None, "access denied"))
    findings = h.check_driver_load_failures()
    assert len(findings) == 1
    assert findings[0].id == "driver_load_failures:unknown"
    assert findings[0].severity == "info"
    assert findings[0].jump == "Driver Manager"
    assert "access denied" in findings[0].evidence


def test_evaluate_tpm_attestation_events():
    assert h.evaluate_tpm_attestation_events([]) == []
    rows = [
        {
            "Time": "2026-11-09 14:52:58",
            "Message": "Pre-attestation health checks confirm a critical component has failed...",
        },
        {
            "Time": "2026-11-09 14:01:21",
            "Message": "Pre-attestation health checks confirm a critical component has failed...",
        },
    ]
    findings = h.evaluate_tpm_attestation_events(rows)
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "tpm_wmi_attestation_failed"
    assert "2 time(s)" in f.title
    assert f.severity == "warning"
    assert f.jump == "Security Dashboard"
    assert "Event 1040" in f.evidence


def test_check_tpm_attestation_events_refused_is_unknown(monkeypatch):
    monkeypatch.setattr(h, "_ps_json", lambda script, timeout: (None, "access denied"))
    findings = h.check_tpm_attestation_events()
    assert len(findings) == 1
    assert findings[0].id == "tpm_attestation_events:unknown"
    assert findings[0].severity == "info"
    assert findings[0].jump == "Security Dashboard"
    assert "access denied" in findings[0].evidence


def test_evaluate_storage_events_empty_is_healthy():
    assert h.evaluate_storage_events([]) == []


def test_evaluate_storage_events_duplicate_disk_identifiers():
    groups = [
        {
            "event_id": 158,
            "disk": "6",
            "count": 6,
            "latest": "2026-11-09 23:59:11",
            "meaning": "Windows sees the same disk identifiers on more than one disk",
            "is_error": False,
        },
        {
            "event_id": 158,
            "disk": "5",
            "count": 4,
            "latest": "2026-11-14 11:53:36",
            "meaning": "Windows sees the same disk identifiers on more than one disk",
            "is_error": False,
        },
    ]
    findings = h.evaluate_storage_events(groups)
    assert len(findings) == 2
    f0 = findings[0]
    assert f0.id == "storage_event:158:6"
    assert "Disk 6 has duplicate disk identifier collision (Event 158)" in f0.title
    assert f0.severity == "warning"
    assert f0.jump == "Disk Health"
    assert "KB2983588" in f0.detail
    assert "6 event(s)" in f0.evidence


def test_evaluate_storage_events_hardware_and_corruption_errors():
    groups = [
        {
            "event_id": 154,
            "disk": "1",
            "count": 3,
            "latest": "2026-11-14 10:00:00",
            "meaning": "An I/O operation failed due to a hardware error.",
            "is_error": True,
        },
        {
            "event_id": 55,
            "disk": "0",
            "count": 1,
            "latest": "2026-11-14 09:00:00",
            "meaning": "The file system structure on the disk is corrupt and unusable.",
            "is_error": True,
        },
        {
            "event_id": 98,
            "disk": "0",
            "count": 5,
            "latest": "2026-11-14 08:00:00",
            "meaning": "Volume was scanned by NTFS/ReFS self-healing.",
            "is_error": False,
        },
    ]
    findings = h.evaluate_storage_events(groups)
    assert len(findings) == 2
    f154 = findings[0]
    assert f154.id == "storage_event:154:1"
    assert f154.severity == "danger"
    assert "hardware error" in f154.detail
    assert f154.jump == "Disk Health"

    f55 = findings[1]
    assert f55.id == "storage_event:55:0"
    assert f55.severity == "danger"
    assert "chkdsk" in f55.detail


def test_check_storage_events_refused_is_unknown(monkeypatch):
    from modules.disk_health import disk_events
    monkeypatch.setattr(disk_events, "read_disk_events", lambda days=14: None)
    findings = h.check_storage_events()
    assert len(findings) == 1
    assert findings[0].id == "storage_events:unknown"
    assert findings[0].severity == "info"
    assert findings[0].jump == "Disk Health"


def test_evaluate_firewall_exposure_clean():
    class DummySnap:
        error = ""
        findings = []
    assert h.evaluate_firewall_exposure(DummySnap()) == []


def test_evaluate_firewall_exposure_profile_off_and_default_allow():
    from modules.firewall_rules.firewall_audit import Finding as AuditFinding
    class DummySnap:
        error = ""
        findings = [
            AuditFinding(severity="high", key="profile_off", title="Firewall is OFF on the Public profile",
                         detail="No rules enforced.", fix="Set-NetFirewallProfile -Profile Public -Enabled True"),
            AuditFinding(severity="high", key="default_allow", title="Public profile allows inbound by default",
                         detail="Unsolicited traffic permitted."),
        ]
    findings = h.evaluate_firewall_exposure(DummySnap())
    assert len(findings) == 2
    assert findings[0].id == "firewall:profile_off"
    assert findings[0].severity == "warning"
    assert findings[0].jump == "Firewall Rules"
    assert "Set-NetFirewallProfile" in findings[0].detail

    assert findings[1].id == "firewall:default_allow"
    assert findings[1].severity == "warning"
    assert findings[1].jump == "Firewall Rules"


def test_evaluate_firewall_exposure_open_and_missing():
    from modules.firewall_rules.firewall_audit import Finding as AuditFinding
    class DummySnap:
        error = ""
        findings = [
            AuditFinding(severity="high", key="open_public", title="1 open rule on Public",
                         detail="Open to all ports.", rule_names=["TestRule"]),
            AuditFinding(severity="low", key="prog_missing", title="1 rule points to deleted exe",
                         detail="Stale rule.", rule_names=["OldApp"]),
        ]
    findings = h.evaluate_firewall_exposure(DummySnap())
    assert len(findings) == 2
    assert findings[0].id == "firewall:open_public"
    assert findings[0].severity == "warning"
    assert "TestRule" in findings[0].evidence

    assert findings[1].id == "firewall:prog_missing"
    assert findings[1].severity == "info"
    assert "OldApp" in findings[1].evidence


def test_evaluate_firewall_exposure_error_is_unknown():
    class DummySnap:
        error = "Access denied reading registry"
        findings = []
    findings = h.evaluate_firewall_exposure(DummySnap())
    assert len(findings) == 1
    assert findings[0].id == "firewall_exposure:unknown"
    assert findings[0].severity == "info"
    assert findings[0].jump == "Firewall Rules"


def test_check_firewall_exposure_real_machine():
    findings = h.check_firewall_exposure()
    assert isinstance(findings, list)
    for f in findings:
        assert isinstance(f, Finding)
        assert f.jump == "Firewall Rules"


def test_evaluate_scheduled_task_failures_clean():
    class DummyTask:
        enabled = True
        last_result_code = 0
        path = r"\Vendor\Task"
    assert h.evaluate_scheduled_task_failures([DummyTask()]) == []


def test_evaluate_scheduled_task_failures_categorization():
    from types import SimpleNamespace
    tasks = [
        SimpleNamespace(enabled=True, last_result_code=1, path=r"\Vendor\CustomBackup"),
        SimpleNamespace(enabled=True, last_result_code=0x8007042B, path=r"\Microsoft\Windows\SystemRestore\SR"),
        SimpleNamespace(enabled=False, last_result_code=1, path=r"\Disabled\Task"),  # ignored because disabled
    ]
    findings = h.evaluate_scheduled_task_failures(tasks)
    assert len(findings) == 2
    f_third = [f for f in findings if f.id == "scheduled_tasks:failed_third_party"][0]
    assert f_third.severity == "warning"
    assert "CustomBackup" in f_third.evidence
    assert f_third.jump == "Scheduled Tasks"

    f_win = [f for f in findings if f.id == "scheduled_tasks:failed_windows"][0]
    assert f_win.severity == "info"
    assert "SystemRestore" in f_win.evidence
    assert f_win.jump == "Scheduled Tasks"


def test_check_scheduled_task_failures_real_machine():
    findings = h.check_scheduled_task_failures()
    assert isinstance(findings, list)
    for f in findings:
        assert isinstance(f, Finding)
        assert f.jump == "Scheduled Tasks"


def test_evaluate_local_accounts():
    from types import SimpleNamespace
    raw = [
        SimpleNamespace(severity="warning", account="Local Security Policy", message="Password history is 0"),
        SimpleNamespace(severity="info", account="iorda", message="Administrator whose password never expires"),
    ]
    findings = h.evaluate_local_accounts(raw)
    assert len(findings) == 2
    assert findings[0].severity == "warning"
    assert findings[0].jump == "Local Users & Groups"
    assert "Password history" in findings[0].detail

    assert findings[1].severity == "info"
    assert findings[1].jump == "Local Users & Groups"


def test_check_local_accounts_real_machine():
    findings = h.check_local_accounts()
    assert isinstance(findings, list)
    for f in findings:
        assert isinstance(f, Finding)
        assert f.jump == "Local Users & Groups"


def test_evaluate_path_environment():
    clean = h.evaluate_path_environment([], [])
    assert clean == []

    f_writable = h.evaluate_path_environment([], ["C:\\Users\\Public\\bin is writable"])
    assert len(f_writable) == 1
    assert f_writable[0].id == "path:writable"
    assert f_writable[0].severity == "warning"
    assert f_writable[0].jump == "Environment Variables"

    f_missing = h.evaluate_path_environment(["Folder does not exist: C:\\old\\bin"], [])
    assert len(f_missing) == 1
    assert f_missing[0].id == "path:missing"
    assert f_missing[0].severity == "info"
    assert f_missing[0].jump == "Environment Variables"


def test_check_path_environment_real_machine():
    findings = h.check_path_environment()
    assert isinstance(findings, list)
    for f in findings:
        assert isinstance(f, Finding)
        assert f.jump == "Environment Variables"


def test_evaluate_software_health():
    from types import SimpleNamespace
    raw = [
        SimpleNamespace(severity="warning", title="1 uninstall entry points to missing program", detail="App gone"),
        SimpleNamespace(severity="info", title="Duplicates found", detail="Two versions"),
    ]
    findings = h.evaluate_software_health(raw)
    assert len(findings) == 1
    assert findings[0].id == "software:broken_uninstaller"
    assert findings[0].severity == "warning"
    assert findings[0].jump == "Software Inventory"


def test_check_software_health_real_machine():
    findings = h.check_software_health()
    assert isinstance(findings, list)
    for f in findings:
        assert isinstance(f, Finding)
        assert f.jump == "Software Inventory"


def test_evaluate_firmware_security():
    # Healthy UEFI + Secure Boot
    assert h.evaluate_firmware_security("UEFI", True, "") == []

    # UEFI + Secure Boot disabled
    sb_off = h.evaluate_firmware_security("UEFI", False, "UEFISecureBootEnabled=0")
    assert len(sb_off) == 1
    assert sb_off[0].id == "firmware:secure_boot_disabled"
    assert sb_off[0].severity == "warning"
    assert sb_off[0].jump == "Security Dashboard"

    # Legacy BIOS
    legacy = h.evaluate_firmware_security("Legacy BIOS", None, "Not available in Legacy BIOS")
    assert len(legacy) == 2
    ids = {f.id for f in legacy}
    assert "firmware:legacy_bios" in ids
    assert any(":unknown" in i for i in ids)


def test_check_firmware_security_real_machine():
    findings = h.check_firmware_security()
    assert isinstance(findings, list)
    for f in findings:
        assert isinstance(f, Finding)


def test_real_machine_checks_return_findings_and_do_not_raise():
    out = h.run_all()
    assert all(isinstance(f, Finding) for f in out)
    # a machine that answers everything has few unknowns; a wall of them means a broken reader
    assert sum(1 for f in out if f.id.endswith(":unknown")) <= 3
    assert full_findings()   # includes at least the upgrade-headroom line
