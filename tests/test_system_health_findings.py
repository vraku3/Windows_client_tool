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
