"""System Health's 3 read-only findings. No Qt, no writes -- every
check here must be safe to run unelevated and from --unattended
--stages health.
"""
import os
import subprocess

import pytest

from modules.system_health.findings import (
    Finding, check_pending_servicing, check_orphaned_scheduled_tasks,
    check_upgrade_headroom, all_findings,
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


def test_upgrade_headroom_below_threshold_is_a_warning(monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: (100 * 1024**3, 90 * 1024**3, 10 * 1024**3))
    finding = check_upgrade_headroom()
    assert finding.severity == "warning"
    assert "10.0 GB" in finding.title


def test_upgrade_headroom_above_threshold_is_info(monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: (500 * 1024**3, 100 * 1024**3, 400 * 1024**3))
    finding = check_upgrade_headroom()
    assert finding.severity == "info"


def test_all_findings_combines_all_three_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("windir", str(tmp_path))  # no pending.xml
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr("shutil.disk_usage", lambda path: (500 * 1024**3, 100 * 1024**3, 400 * 1024**3))

    findings = all_findings()

    # No pending servicing, no orphaned tasks (empty schtasks output), one
    # headroom finding -- exactly 1 item.
    assert len(findings) == 1
    assert findings[0].id == "upgrade_headroom"
