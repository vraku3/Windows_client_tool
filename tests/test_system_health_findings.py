"""System Health's 3 read-only findings. No Qt, no writes -- every
check here must be safe to run unelevated and from --unattended
--stages health.
"""
import os
import subprocess

import pytest

from modules.system_health.findings import (
    Finding, check_pending_servicing, check_orphaned_scheduled_tasks,
    check_orphaned_services, check_hardware_errors, check_upgrade_headroom,
    check_crash_control, check_print_queue, check_driver_blocklist, all_findings,
)


def test_pending_servicing_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("windir", str(tmp_path))  # no WinSxS\pending.xml here
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    assert check_pending_servicing() is None


def test_pending_servicing_present_returns_a_warning(tmp_path, monkeypatch):
    winsxs = tmp_path / "WinSxS"
    winsxs.mkdir()
    (winsxs / "pending.xml").write_text("<root/>")
    monkeypatch.setenv("windir", str(tmp_path))
    monkeypatch.setenv("SystemRoot", str(tmp_path))

    finding = check_pending_servicing()

    assert finding is not None
    assert finding.severity == "warning"
    assert "pending.xml" in finding.detail or str(winsxs) in finding.detail


def test_orphaned_task_pointing_at_a_missing_program_is_flagged(tmp_path, monkeypatch):
    csv_output = '"RealTask","Ready","N/A"\r\n'
    xml_output = "<Task><Actions><Exec><Command>C:\\does\\not\\exist.exe</Command></Exec></Actions></Task>"

    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class _R:
            pass
        r = _R()
        r.returncode = 0
        if "/xml" in cmd:
            r.stdout = xml_output
        else:
            r.stdout = csv_output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    findings = check_orphaned_scheduled_tasks()

    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert "RealTask" in findings[0].detail


def test_task_pointing_at_a_real_program_is_not_flagged(tmp_path, monkeypatch):
    real_exe = tmp_path / "real.exe"
    real_exe.write_text("x")
    csv_output = '"RealTask","Ready","N/A"\r\n'
    xml_output = f"<Task><Actions><Exec><Command>{real_exe}</Command></Exec></Actions></Task>"

    def fake_run(cmd, **kwargs):
        class _R:
            pass
        r = _R()
        r.returncode = 0
        r.stdout = xml_output if "/xml" in cmd else csv_output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert check_orphaned_scheduled_tasks() == []


def test_task_pointing_at_a_bare_command_resolved_via_path_is_not_flagged(monkeypatch):
    csv_output = '"NotepadTask","Ready","N/A"\r\n'
    xml_output = "<Task><Actions><Exec><Command>notepad.exe</Command></Exec></Actions></Task>"

    def fake_run(cmd, **kwargs):
        class _R:
            pass
        r = _R()
        r.returncode = 0
        r.stdout = xml_output if "/xml" in cmd else csv_output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda name: r"C:\Windows\notepad.exe")

    assert check_orphaned_scheduled_tasks() == []


def test_a_refused_per_task_xml_query_is_reported_not_swallowed(tmp_path, monkeypatch):
    # Two tasks in the listing: one whose per-task XML query is refused
    # (Access denied), one that succeeds and points at a real program. The
    # refusal must surface as its own Finding naming that task -- not be
    # silently dropped, and must not suppress the other task's own result.
    real_exe = tmp_path / "real.exe"
    real_exe.write_text("x")
    csv_output = '"BadTask","Ready","N/A"\r\n"GoodTask","Ready","N/A"\r\n'
    good_xml = f"<Task><Actions><Exec><Command>{real_exe}</Command></Exec></Actions></Task>"

    def fake_run(cmd, **kwargs):
        class _R:
            pass
        r = _R()
        if "/xml" not in cmd:
            r.returncode = 0
            r.stdout = csv_output
            r.stderr = ""
            return r
        if "BadTask" in cmd:
            r.returncode = 1
            r.stdout = ""
            r.stderr = "Access is denied."
            return r
        # GoodTask
        r.returncode = 0
        r.stdout = good_xml
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    findings = check_orphaned_scheduled_tasks()

    assert len(findings) == 1
    assert findings[0].id == "orphaned_task_check_refused:BadTask"
    assert "BadTask" in findings[0].title
    assert "denied" in findings[0].detail.lower()
    assert findings[0].severity == "warning"


def test_a_refused_schtasks_call_is_reported_not_swallowed(monkeypatch):
    def fake_run(cmd, **kwargs):
        class _R:
            pass
        r = _R()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "Access is denied."
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    findings = check_orphaned_scheduled_tasks()

    assert len(findings) == 1
    assert findings[0].id == "orphaned_tasks_refused"
    assert "denied" in findings[0].detail.lower()


def test_a_failed_schtasks_call_is_reported_not_swallowed(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("schtasks not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    findings = check_orphaned_scheduled_tasks()

    assert len(findings) == 1
    assert findings[0].id == "orphaned_tasks_refused"


class _KeyCtx:
    """A fake context-manager registry key, same shape as
    test_service_startup_flags.py's -- `winreg.OpenKey(...)` returns one and
    the module only ever uses it via `with` or `.Close()`."""

    def __init__(self, name=""):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def Close(self):
        pass


def test_orphaned_service_with_missing_absolute_path_is_flagged(monkeypatch, tmp_path):
    import winreg
    missing = str(tmp_path / "gone.sys")
    values = {"ImagePath": (missing, None), "Start": (0, None)}
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "EnumKey",
                        lambda root, index: "OrphanDrv" if index == 0 else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: values[name])

    findings = check_orphaned_services()

    assert len(findings) == 1
    assert findings[0].id == "orphaned_service:OrphanDrv"
    assert findings[0].severity == "warning"  # boot-start (0)
    assert missing in findings[0].detail
    assert findings[0].jump == "Services"


def test_orphaned_service_manual_start_is_info_not_warning(monkeypatch, tmp_path):
    import winreg
    missing = str(tmp_path / "gone.exe")
    values = {"ImagePath": (missing, None), "Start": (3, None)}
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "EnumKey",
                        lambda root, index: "OrphanSvc" if index == 0 else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: values[name])

    findings = check_orphaned_services()

    assert len(findings) == 1 and findings[0].severity == "info"


def test_relative_imagepath_resolves_against_systemroot_not_flagged_when_present(monkeypatch, tmp_path):
    import winreg
    system_root_dir = tmp_path / "Windows"
    (system_root_dir / "System32" / "drivers").mkdir(parents=True)
    real = system_root_dir / "System32" / "drivers" / "acpi.sys"
    real.write_text("x")
    values = {"ImagePath": (r"System32\drivers\acpi.sys", None), "Start": (0, None)}
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "EnumKey",
                        lambda root, index: "ACPI" if index == 0 else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: values[name])
    monkeypatch.setattr("modules.system_health.findings.system_root", lambda: str(system_root_dir))

    assert check_orphaned_services() == []


def test_an_unopenable_services_key_is_reported_not_swallowed(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))

    findings = check_orphaned_services()

    assert len(findings) == 1 and findings[0].id == "orphaned_services_refused"


def test_one_unreadable_service_key_is_skipped_not_fatal(monkeypatch, tmp_path):
    import winreg
    missing = str(tmp_path / "gone.exe")

    def fake_open_key(root, name=None, *a, **k):
        if name == "Bad":
            raise OSError("access denied")
        return _KeyCtx(name or "")

    def fake_query(key, name):
        if key.name == "Good":
            return {"ImagePath": missing, "Start": 3}[name], None
        raise FileNotFoundError()

    monkeypatch.setattr(winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(winreg, "EnumKey",
                        lambda root, index: ["Bad", "Good"][index] if index < 2
                        else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    findings = check_orphaned_services()

    assert len(findings) == 1 and findings[0].id == "orphaned_service:Good"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only registry read")
def test_real_orphaned_services_check_runs_without_raising():
    findings = check_orphaned_services()
    assert isinstance(findings, list)
    # A handful of real, explicable orphans is normal (a monitoring tool's
    # driver extracted to %TEMP%, a leftover uninstalled-driver registration)
    # -- hundreds would mean the resolution logic broke again.
    assert len(findings) < 50


def test_no_hardware_errors_is_an_empty_list_not_a_finding(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: type(
        "R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})())
    assert check_hardware_errors() == []


def test_corrected_only_hardware_errors_are_info(monkeypatch):
    # Synthetic, documented WHEA-Logger event 1 message shape ("A corrected
    # hardware error has occurred.") -- this real machine logs none to
    # capture a genuine sample from, confirmed via a live probe.
    payload = ('[{"Level":"Warning","Time":"2026-09-20 03:00:00",'
              '"Message":"A corrected hardware error has occurred."}]')
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: type(
        "R", (), {"returncode": 0, "stdout": payload, "stderr": ""})())

    findings = check_hardware_errors()

    assert len(findings) == 1
    assert findings[0].severity == "info" and "1 hardware error" in findings[0].title


def test_a_fatal_hardware_error_is_a_warning(monkeypatch):
    payload = ('[{"Level":"Warning","Time":"2026-09-20 03:00:00",'
              '"Message":"A corrected hardware error has occurred."},'
              '{"Level":"Error","Time":"2026-09-21 04:00:00",'
              '"Message":"A fatal hardware error has occurred."}]')
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: type(
        "R", (), {"returncode": 0, "stdout": payload, "stderr": ""})())

    findings = check_hardware_errors()

    assert len(findings) == 1
    assert findings[0].severity == "warning" and "2 hardware error" in findings[0].title
    assert "fatal" in findings[0].detail.lower()


def test_a_refused_hardware_error_read_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: type(
        "R", (), {"returncode": 1, "stdout": "", "stderr": "Access is denied."})())

    findings = check_hardware_errors()

    assert len(findings) == 1
    assert findings[0].id == "hardware_errors_refused"
    assert "denied" in findings[0].detail.lower()


def test_a_failed_hardware_error_call_is_reported_not_swallowed(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("powershell not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    findings = check_hardware_errors()

    assert len(findings) == 1 and findings[0].id == "hardware_errors_refused"


def test_real_hardware_errors_check_runs_without_raising():
    # Confirmed live 2026-09-27: this machine logs zero WHEA events in the
    # last 30 days -- a real, clean read, not an untested one.
    findings = check_hardware_errors()
    assert findings == [] or findings[0].id in ("hardware_errors", "hardware_errors_refused")


def test_upgrade_headroom_below_threshold_is_a_warning(monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: (100 * 1024**3, 90 * 1024**3, 10 * 1024**3))
    finding = check_upgrade_headroom()
    assert finding.severity == "warning"
    assert "10.0 GB" in finding.title


def test_upgrade_headroom_above_threshold_is_info(monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: (500 * 1024**3, 100 * 1024**3, 400 * 1024**3))
    finding = check_upgrade_headroom()
    assert finding.severity == "info"


def test_crash_control_real_machine():
    findings = check_crash_control()
    assert len(findings) >= 1
    assert any(f.id in ("crash_dump_config", "crash_dumps_disabled", "crash_control_refused") for f in findings)


def test_crash_control_disabled_reported_as_warning(monkeypatch):
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    
    def fake_query(key, name):
        if name == "CrashDumpEnabled": return (0, 4)
        if name == "AutoReboot": return (0, 4)
        if name == "LogEvent": return (1, 4)
        raise FileNotFoundError(name)
        
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    findings = check_crash_control()
    ids = [f.id for f in findings]
    assert "crash_dumps_disabled" in ids
    assert "auto_reboot_disabled" in ids
    assert any(f.severity == "warning" for f in findings)


def test_print_queue_stuck_jobs(tmp_path, monkeypatch):
    spool = tmp_path / "System32" / "spool" / "PRINTERS"
    spool.mkdir(parents=True)
    (spool / "FP00001.SHD").write_text("x")
    (spool / "FP00001.SPL").write_text("y")
    monkeypatch.setenv("windir", str(tmp_path))
    monkeypatch.setenv("SystemRoot", str(tmp_path))

    findings = check_print_queue()
    assert len(findings) == 1
    assert findings[0].id == "stuck_print_jobs"
    assert findings[0].severity == "warning"
    assert "2 stuck print spool file(s)" in findings[0].title


def test_print_queue_empty_when_clean(tmp_path, monkeypatch):
    spool = tmp_path / "System32" / "spool" / "PRINTERS"
    spool.mkdir(parents=True)
    monkeypatch.setenv("windir", str(tmp_path))
    monkeypatch.setenv("SystemRoot", str(tmp_path))

    assert check_print_queue() == []


def test_driver_blocklist_enabled(monkeypatch):
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda k, name: (1, 4))

    finding = check_driver_blocklist()
    assert finding.id == "vulnerable_driver_blocklist_enabled"
    assert finding.severity == "info"


def test_driver_blocklist_disabled(monkeypatch):
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda k, name: (0, 4))

    finding = check_driver_blocklist()
    assert finding.id == "vulnerable_driver_blocklist_disabled"
    assert finding.severity == "warning"


def test_all_findings_combines_all_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("windir", str(tmp_path))  # no pending.xml
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr("shutil.disk_usage", lambda path: (500 * 1024**3, 100 * 1024**3, 400 * 1024**3))
    monkeypatch.setattr("modules.system_health.findings.check_orphaned_services", lambda: [])
    monkeypatch.setattr("modules.system_health.findings.check_crash_control", lambda: [Finding(id="fake_crash", title="Crash", detail="", severity="info")])
    monkeypatch.setattr("modules.system_health.findings.check_print_queue", lambda: [])
    monkeypatch.setattr("modules.system_health.findings.check_driver_blocklist", lambda: Finding(id="fake_driver", title="Driver", detail="", severity="info"))

    findings = all_findings()
    ids = [f.id for f in findings]
    assert "fake_crash" in ids
    assert "fake_driver" in ids
    assert "upgrade_headroom" in ids
    assert len(findings) >= 3


def test_vss_services_disabled(monkeypatch):
    from modules.system_health.findings import check_vss_services
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda k, name: (4, 4))  # 4 = Disabled

    findings = check_vss_services()
    assert len(findings) == 2
    assert findings[0].id == "vss_service_disabled"
    assert findings[0].severity == "warning"
    assert findings[1].id == "swprv_service_disabled"
    assert findings[1].severity == "warning"


def test_fast_startup_uptime_flagged(monkeypatch):
    from modules.system_health.findings import check_fast_startup_uptime
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    import winreg
    import ctypes
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda k, name: (1, 4))
    # Mock 20 days of uptime in milliseconds
    ms_20_days = 20 * 24 * 60 * 60 * 1000
    monkeypatch.setattr(ctypes.windll.kernel32, "GetTickCount64", lambda: ms_20_days)

    finding = check_fast_startup_uptime()
    assert finding is not None
    assert finding.id == "fast_startup_prolonged_uptime"
    assert "20 days" in finding.title
    assert finding.severity == "info"


def test_scheduled_chkdsk_detected(monkeypatch):
    from modules.system_health.findings import check_scheduled_chkdsk
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(
        winreg,
        "QueryValueEx",
        lambda k, name: (["autocheck autochk *", r"autocheck autochk /p \??\C:"], 7)
    )

    finding = check_scheduled_chkdsk()
    assert finding is not None
    assert finding.id == "scheduled_chkdsk_pending"
    assert finding.severity == "warning"
    assert "chkdsk" in finding.title.lower()


def test_safeboot_active_detected(monkeypatch):
    from modules.system_health.findings import check_safeboot_active
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda k, name: (1, 4))

    finding = check_safeboot_active()
    assert finding is not None
    assert finding.id == "safeboot_active"
    assert finding.severity == "warning"
    assert "Minimal" in finding.title


def test_wer_queue_buildup(tmp_path, monkeypatch):
    from modules.system_health.findings import check_wer_queue
    monkeypatch.setenv("ProgramData", str(tmp_path))
    wer_dir = tmp_path / "Microsoft" / "Windows" / "WER" / "ReportQueue"
    wer_dir.mkdir(parents=True)

    # Empty queue
    assert check_wer_queue() is None

    # Small queue
    rpt1 = wer_dir / "Report1"
    rpt1.mkdir()
    (rpt1 / "Report.wer").write_text("crash data")
    f_info = check_wer_queue()
    assert f_info is not None
    assert f_info.severity == "info"

    # Queue exceeding max_reports threshold
    f_warn = check_wer_queue(max_reports=1)
    assert f_warn is not None
    assert f_warn.severity == "warning"


def test_check_vss_writers(monkeypatch):
    from modules.system_health.findings import check_vss_writers
    import subprocess

    sample_output = (
        "Writer name: 'System Writer'\n"
        "   Writer Id: {e81069a3-c424-4971-8c43-7f61ca658f33}\n"
        "   State: [1] Stable\n"
        "   Last error: No error\n"
        "\n"
        "Writer name: 'SqlServerWriter'\n"
        "   Writer Id: {a65faa63-5ea8-4ebc-9dbd-a0c4db26912a}\n"
        "   State: [5] Waiting for completion\n"
        "   Last error: Timed out\n"
    )

    class MockProc:
        returncode = 0
        stdout = sample_output
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MockProc())
    findings = check_vss_writers()
    assert len(findings) == 1
    assert "SqlServerWriter" in findings[0].title
    assert findings[0].severity == "error"
    assert "Timed out" in findings[0].detail


def test_check_trim_status(monkeypatch):
    from modules.system_health.findings import check_trim_status
    import subprocess

    class MockProcEnabled:
        returncode = 0
        stdout = "NTFS DisableDeleteNotify = 0\nReFS DisableDeleteNotify = 0\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MockProcEnabled())
    assert check_trim_status() is None

    class MockProcDisabled:
        returncode = 0
        stdout = "NTFS DisableDeleteNotify = 1\nReFS DisableDeleteNotify = 0\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MockProcDisabled())
    f = check_trim_status()
    assert f is not None
    assert f.id == "ssd_trim_disabled"
    assert "NTFS" in f.title
    assert f.severity == "warning"


def test_check_nvme_wear(monkeypatch):
    from modules.system_health.findings import check_nvme_wear
    from modules.disk_health import physical_disks as pd

    class MockScan:
        available = True
        disks = [
            pd.PhysicalDiskInfo(
                device_id="0", friendly_name="Samsung SSD 990 PRO 2TB", serial="S123",
                media_type="SSD", bus_type="NVMe", health_status="Healthy",
                operational_status="OK", size_gb=1863.0, pool_name=None,
                wear_pct=92, read_errors_uncorrected=0,
            ),
            pd.PhysicalDiskInfo(
                device_id="1", friendly_name="ADATA LEGEND 960", serial="A456",
                media_type="SSD", bus_type="NVMe", health_status="Healthy",
                operational_status="OK", size_gb=1863.0, pool_name=None,
                wear_pct=15, read_errors_uncorrected=3,
            ),
        ]

    monkeypatch.setattr(pd, "list_physical_disks", lambda: MockScan())
    findings = check_nvme_wear()
    assert len(findings) == 2
    assert "Critical SSD wear" in findings[0].title
    assert "92%" in findings[0].title
    assert "Uncorrected read errors" in findings[1].title
    assert findings[0].jump == "Disk Health"


def test_check_winre_status(tmp_path):
    from modules.system_health.findings import check_winre_status, parse_reagent_xml

    xml_enabled = """<?xml version='1.0' encoding='utf-8'?>
<WindowsRE version="2.0">
  <WinreBCD id="{72ca7ff0-dc88-11f1-b38c-8fca09aa7346}"/>
  <WinreLocation path="\\Recovery\\WindowsRE" id="0" offset="1999431008256" guid="{288382be-0949-4e03-ae41-5566ffd8f031}"/>
  <InstallState state="1"/>
</WindowsRE>"""
    facts = parse_reagent_xml(xml_enabled)
    assert facts["install_state"] == "1"
    assert facts["winre_location"] == "\\Recovery\\WindowsRE"
    assert facts["winre_bcd"] == "{72ca7ff0-dc88-11f1-b38c-8fca09aa7346}"

    enabled_file = tmp_path / "ReAgent_enabled.xml"
    enabled_file.write_text(xml_enabled, encoding="utf-8")
    assert check_winre_status(str(enabled_file)) is None

    xml_disabled = """<?xml version='1.0' encoding='utf-8'?>
<WindowsRE version="2.0">
  <WinreBCD id="{00000000-0000-0000-0000-000000000000}"/>
  <WinreLocation path="" id="0" offset="0" guid="{00000000-0000-0000-0000-000000000000}"/>
  <InstallState state="0"/>
</WindowsRE>"""
    disabled_file = tmp_path / "ReAgent_disabled.xml"
    disabled_file.write_text(xml_disabled, encoding="utf-8")
    f_dis = check_winre_status(str(disabled_file))
    assert f_dis is not None
    assert f_dis.id == "winre_disabled"
    assert f_dis.severity == "warning"
    assert f_dis.jump == "Quick Fix"

    missing_file = tmp_path / "nonexistent.xml"
    f_mis = check_winre_status(str(missing_file))
    assert f_mis is not None
    assert f_mis.id == "winre_missing"


def test_check_pagefile_config_real_machine():
    from modules.system_health.findings import check_pagefile_config
    # On this healthy real machine, pagefile is configured
    assert check_pagefile_config() is None


def test_check_pagefile_config_disabled(monkeypatch):
    import winreg
    from modules.system_health.findings import check_pagefile_config

    class FakeKey:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def fake_open(key, subkey, reserved, access):
        return FakeKey()

    def fake_query(key, val_name):
        if val_name == "PagingFiles":
            return ([], winreg.REG_MULTI_SZ)
        if val_name == "ExistingPageFiles":
            raise FileNotFoundError("ExistingPageFiles not found")
        raise FileNotFoundError(val_name)

    monkeypatch.setattr(winreg, "OpenKey", fake_open)
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    finding = check_pagefile_config()
    assert finding is not None
    assert finding.id == "pagefile_disabled"
    assert finding.severity == "warning"
    assert finding.jump == "Tweaks"


def test_check_crash_dump_files_absent(tmp_path):
    from modules.system_health.findings import check_crash_dump_files
    mem_p = str(tmp_path / "MEMORY.DMP")
    mini_p = str(tmp_path / "Minidump")
    assert check_crash_dump_files(memory_dmp_path=mem_p, minidump_dir=mini_p) == []


def test_check_crash_dump_files_memory_dmp(tmp_path):
    from modules.system_health.findings import check_crash_dump_files
    mem_file = tmp_path / "MEMORY.DMP"
    mem_file.write_bytes(b"x" * 1024)
    # Warning threshold 0.0001 MB so 1KB triggers warning
    findings = check_crash_dump_files(min_warning_mb=0.0001, memory_dmp_path=str(mem_file))
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "memory_dump_file_present"
    assert f.severity == "warning"
    assert f.jump == "Diagnose"
    assert "MEMORY.DMP" in f.title

    # Higher threshold triggers info
    f_info = check_crash_dump_files(min_warning_mb=500.0, memory_dmp_path=str(mem_file))[0]
    assert f_info.severity == "info"


def test_check_crash_dump_files_minidumps(tmp_path):
    from modules.system_health.findings import check_crash_dump_files
    mini_dir = tmp_path / "Minidump"
    mini_dir.mkdir()
    for i in range(4):
        (mini_dir / f"mini_{i}.dmp").write_bytes(b"DUMP" * 256)
    (mini_dir / "not_a_dump.txt").write_text("ignore me")

    findings = check_crash_dump_files(minidump_dir=str(mini_dir))
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "minidump_files_present"
    assert f.severity == "warning"
    assert "4 kernel minidump" in f.title
    assert f.jump == "Diagnose"


def test_check_hosts_file_clean(tmp_path):
    from modules.system_health.findings import check_hosts_file
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "# Sample hosts file\n"
        "# 127.0.0.1 localhost\n"
        "127.0.0.1 localhost\n"
        "::1 localhost\n",
        encoding="utf-8"
    )
    assert check_hosts_file(str(hosts)) is None


def test_check_hosts_file_custom_entries(tmp_path):
    from modules.system_health.findings import check_hosts_file
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "# Dev entries\n"
        "192.168.1.50 myserver.lan # local dev\n"
        "10.0.0.2 database.local\n",
        encoding="utf-8"
    )
    finding = check_hosts_file(str(hosts))
    assert finding is not None
    assert finding.id == "hosts_file_custom_entries"
    assert finding.severity == "info"
    assert finding.jump == "Network Diagnostics"
    assert "2 custom domain mapping(s)" in finding.title
    assert "myserver.lan" in finding.evidence


def test_check_hosts_file_critical_redirect(tmp_path):
    from modules.system_health.findings import check_hosts_file
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "# Block updates\n"
        "0.0.0.0 windowsupdate.microsoft.com\n"
        "127.0.0.1 login.live.com\n",
        encoding="utf-8"
    )
    finding = check_hosts_file(str(hosts))
    assert finding is not None
    assert finding.id == "hosts_file_critical_redirect"
    assert finding.severity == "warning"
    assert finding.jump == "Network Diagnostics"
    assert "windowsupdate.microsoft.com" in finding.evidence


def test_orphaned_startup_entries_clean(tmp_path):
    from modules.system_health.findings import check_orphaned_startup_entries
    real_exe = tmp_path / "app.exe"
    real_exe.write_text("binary", encoding="utf-8")

    from modules.startup_manager.persistence import Item
    items = [
        Item(name="GoodApp", command=str(real_exe), source="Run", scope="User",
             location=r"HKCU\Run", enabled=True)
    ]
    findings = check_orphaned_startup_entries(run_items=items, folders=[])
    assert findings == []


def test_orphaned_startup_entries_missing_run_key(tmp_path):
    from modules.system_health.findings import check_orphaned_startup_entries
    missing = tmp_path / "deleted.exe"

    from modules.startup_manager.persistence import Item
    items = [
        Item(name="GhostApp", command=f'"{missing}" --minimized', source="Run", scope="User",
             location=r"HKCU\Run", enabled=True),
        Item(name="DisabledGhost", command=f'"{missing}"', source="Run", scope="Machine",
             location=r"HKLM\Run", enabled=False),
    ]
    findings = check_orphaned_startup_entries(run_items=items, folders=[])
    assert len(findings) == 2

    # Active orphan -> warning
    assert findings[0].id == "orphaned_startup:Run:User:GhostApp"
    assert findings[0].severity == "warning"
    assert findings[0].jump == "Startup & Boot"
    assert "GhostApp" in findings[0].title
    assert str(missing) in findings[0].detail

    # Disabled orphan -> info
    assert findings[1].id == "orphaned_startup:Run:Machine:DisabledGhost"
    assert findings[1].severity == "info"
    assert findings[1].jump == "Startup & Boot"
    assert "StartupApproved" in findings[1].detail


def test_orphaned_startup_entries_startup_folder(tmp_path):
    from modules.system_health.findings import check_orphaned_startup_entries
    folder = tmp_path / "Startup"
    folder.mkdir()
    # Direct script/batch file that doesn't exist on disk vs one that does
    orphan_cmd = folder / "nonexistent_target.bat"
    # Create the file in Startup folder pointing to a deleted exe
    orphan_cmd.write_text("exit 0", encoding="utf-8")

    # If the file in startup folder is an exe that is present
    findings = check_orphaned_startup_entries(run_items=[], folders=[("User", str(folder))])
    # nonexistent_target.bat exists in the folder, so resolve_command finds it
    assert findings == []

    # Now simulate a batch script referencing a non-existent binary or an orphaned file
    # If the file in the folder is removed but still enumerated, or resolves to missing
    # Let's test a non-existent executable directly
    ghost_folder = tmp_path / "MissingFolder"
    findings_ghost = check_orphaned_startup_entries(run_items=[], folders=[("User", str(ghost_folder))])
    assert findings_ghost == []


@pytest.mark.skipif(os.name != "nt", reason="Windows-only check")
def test_real_orphaned_startup_entries_runs_without_raising():
    from modules.system_health.findings import check_orphaned_startup_entries
    findings = check_orphaned_startup_entries()
    assert isinstance(findings, list)
    for f in findings:
        assert f.jump == "Startup & Boot"


def test_evaluate_measured_boot_json_clean_attestable():
    from modules.system_health.findings import evaluate_measured_boot_json
    data = {
        "HealthStatus": "Attestable",
        "Required": [
            {"Field": "TpmPresent", "Value": True},
            {"Field": "TpmMeetsMinimumVersion", "Value": True},
            {"Field": "TpmIsResponsive", "Value": True},
            {"Field": "AttestationCertIsAvailable", "Value": True},
        ],
        "Expected": [
            {"Field": "PcrsMatchTcgLog", "Value": True},
        ],
    }
    assert evaluate_measured_boot_json(data, "test.json") == []


def test_evaluate_measured_boot_json_attestation_failure():
    from modules.system_health.findings import evaluate_measured_boot_json
    data = {
        "HealthStatus": "Cannot be attested",
        "Required": [
            {"Field": "TpmPresent", "Value": True},
            {"Field": "TpmMeetsMinimumVersion", "Value": True},
            {"Field": "TpmIsResponsive", "Value": True},
            {"Field": "AttestationCertIsAvailable", "Value": False},
        ],
        "Expected": [
            {"Field": "PcrsMatchTcgLog", "Value": True},
        ],
    }
    findings = evaluate_measured_boot_json(data, "0000000004.json")
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "measured_boot_attestation_failed"
    assert "Cannot be attested" in f.title
    assert "AttestationCertIsAvailable=False" in f.detail
    assert f.severity == "warning"
    assert f.jump == "Security Dashboard"


def test_evaluate_measured_boot_json_no_tpm():
    from modules.system_health.findings import evaluate_measured_boot_json
    data = {
        "HealthStatus": "No TPM",
        "Required": [
            {"Field": "TpmPresent", "Value": False},
        ],
    }
    findings = evaluate_measured_boot_json(data, "test.json")
    assert len(findings) == 1
    assert findings[0].id == "measured_boot_no_tpm"
    assert findings[0].severity == "warning"


def test_check_measured_boot_empty_or_missing_dir(tmp_path):
    from modules.system_health.findings import check_measured_boot
    # Missing directory
    assert check_measured_boot(str(tmp_path / "Nonexistent")) == []
    # Empty directory
    empty_dir = tmp_path / "Empty"
    empty_dir.mkdir()
    assert check_measured_boot(str(empty_dir)) == []

