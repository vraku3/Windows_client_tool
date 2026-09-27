"""System Health's 3 read-only findings. No Qt, no writes -- every
check here must be safe to run unelevated and from --unattended
--stages health.
"""
import os
import subprocess

import pytest

from modules.system_health.findings import (
    Finding, check_pending_servicing, check_orphaned_scheduled_tasks,
    check_orphaned_services, check_hardware_errors, check_upgrade_headroom, all_findings,
)


def test_pending_servicing_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("windir", str(tmp_path))  # no WinSxS\pending.xml here
    assert check_pending_servicing() is None


def test_pending_servicing_present_returns_a_warning(tmp_path, monkeypatch):
    winsxs = tmp_path / "WinSxS"
    winsxs.mkdir()
    (winsxs / "pending.xml").write_text("<root/>")
    monkeypatch.setenv("windir", str(tmp_path))

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


def test_all_findings_combines_all_five_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("windir", str(tmp_path))  # no pending.xml
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr("shutil.disk_usage", lambda path: (500 * 1024**3, 100 * 1024**3, 400 * 1024**3))
    monkeypatch.setattr("modules.system_health.findings.check_orphaned_services", lambda: [])

    findings = all_findings()

    # No pending servicing, no orphaned tasks (empty schtasks output), no
    # orphaned services (mocked empty above), no hardware errors (empty
    # subprocess output above parses as []), one headroom finding.
    assert len(findings) == 1
    assert findings[0].id == "upgrade_headroom"
