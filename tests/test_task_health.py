"""task_health.py — the Settings tab's read of the unattended-maintenance
scheduled task's ACTUAL last-run outcome, not just whether it was created.

Most cases here are synthetic (`subprocess.run` replaced with a fake), mirroring
this repo's own `schtasks` output shapes captured from a real machine. One test
runs the real `schtasks` binary against this real machine and against a task
name that cannot exist, per the sysadmin-pass requirement for a real-machine
assertion.
"""
import subprocess

import pytest

from modules.updates.task_health import (
    TaskHealth, _decode_task_result, _parse_fields, check_task_health,
)

# ---------------------------------------------------------------------------
# Real `schtasks /query /tn <name> /fo list /v` output, captured from this
# machine 2026-09-30 for WinClientTool_UnattendedMaintenance. Its last run
# genuinely failed with -2147020576 (0x800710E0) — this is not invented data.
# ---------------------------------------------------------------------------
REAL_HEALTHY_ENABLED_OUTPUT = """Folder: \\
HostName:                             VRAKU
TaskName:                             \\WinClientTool_UnattendedMaintenance
Next Run Time:                        10/6/2026 3:00:00 AM
Status:                               Ready
Logon Mode:                           Interactive only
Last Run Time:                        9/29/2026 12:08:24 PM
Last Result:                          -2147020576
Author:                               VRAKU\\iorda
Task To Run:                          "C:\\Users\\iorda\\OneDrive\\1 Personal\\Aplicații\\WinClientTool-Portable.exe" --unattended --stages wu,winget,cleanup
Start In:                             N/A
Comment:                              N/A
Scheduled Task State:                 Enabled
Idle Time:                            Disabled
Power Management:                     Stop On Battery Mode, No Start On Batteries
Run As User:                          iorda
Delete Task If Not Rescheduled:       Disabled
Stop Task If Runs X Hours and X Mins: 72:00:00
Schedule:                             Scheduling data is not available in this format.
Schedule Type:                        Daily
Start Time:                           3:00:00 AM
Start Date:                           9/15/2026
End Date:                             N/A
Days:                                 Every 7 day(s)
Months:                               N/A
Repeat: Every:                        Disabled
Repeat: Until: Time:                  Disabled
Repeat: Until: Duration:              Disabled
Repeat: Stop If Still Running:        Disabled
"""

# A SYSTEM-owned task (Microsoft's own SR restore-point task) queried
# unelevated on this same real machine — confirms schtasks query needs no
# elevation even for another principal's task, and that a clean run reports
# "Last Result: 0".
REAL_SYSTEM_TASK_SUCCESS_OUTPUT = """Folder: \\Microsoft\\Windows\\SystemRestore
HostName:                             VRAKU
TaskName:                             \\Microsoft\\Windows\\SystemRestore\\SR
Next Run Time:                        N/A
Status:                               Ready
Logon Mode:                           Interactive/Background
Last Run Time:                        9/29/2026 4:18:14 PM
Last Result:                          0
Author:                               Microsoft Corporation
Task To Run:                          %windir%\\system32\\srtasks.exe ExecuteScheduledSPPCreation
Start In:                             N/A
Comment:                              This task creates regular system protection points.
Scheduled Task State:                 Enabled
Idle Time:                            Only Start If Idle for  minutes, If Not Idle Retry For  minutes Stop the task if Idle State end
Power Management:                     No Start On Batteries
Run As User:                          SYSTEM
Delete Task If Not Rescheduled:       Disabled
Stop Task If Runs X Hours and X Mins: 72:00:00
Schedule:                             Scheduling data is not available in this format.
Schedule Type:                        On demand only
Start Time:                           N/A
Start Date:                           N/A
End Date:                             N/A
Days:                                 N/A
Months:                               N/A
Repeat: Every:                        N/A
Repeat: Until: Time:                  N/A
Repeat: Until: Duration:              N/A
Repeat: Stop If Still Running:        N/A
"""

NOT_FOUND_STDERR = "ERROR: The system cannot find the file specified.\r\n"


class _FakeResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_parses_a_real_failed_run_without_collapsing_it_into_success(monkeypatch):
    """The exact bug this feature exists to fix: a task can be Enabled, with
    a correct next-run time, and STILL have failed its last real execution.
    That must come through as a failure, not get lost behind "Enabled"."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _FakeResult(0, stdout=REAL_HEALTHY_ENABLED_OUTPUT),
    )
    health = check_task_health("WinClientTool_UnattendedMaintenance")
    assert health.exists is True
    assert health.enabled is True
    assert health.last_run == "9/29/2026 12:08:24 PM"
    assert health.next_run == "10/6/2026 3:00:00 AM"
    assert health.last_result_code == -2147020576
    # The decoded text must say something a human can act on, not just repeat
    # the hex code back.
    assert "refused" in health.last_result_text.lower()
    assert "0x800710E0" in health.last_result_text


def test_a_clean_run_decodes_as_success(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _FakeResult(0, stdout=REAL_SYSTEM_TASK_SUCCESS_OUTPUT),
    )
    health = check_task_health("SR")
    assert health.exists is True
    assert health.last_result_code == 0
    assert health.last_result_text == "success"


def test_task_not_found_is_reported_as_not_existing(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _FakeResult(1, stdout="", stderr=NOT_FOUND_STDERR),
    )
    health = check_task_health("SomeTaskThatIsNotRegistered")
    assert health.exists is False
    assert health.error is None


def test_a_refusal_is_never_collapsed_into_not_existing(monkeypatch):
    """A non-'cannot find' failure (e.g. a permissions refusal on some other
    machine/config) must NOT be reported the same way as 'no such task' —
    those tell an admin two very different next actions."""
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _FakeResult(1, stdout="", stderr="ERROR: Access is denied.\r\n"),
    )
    health = check_task_health("SomeTask")
    assert health.exists is None
    assert health.error is not None
    assert "denied" in health.error.lower()


def test_a_timeout_or_missing_binary_is_reported_not_swallowed(monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError("schtasks not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    health = check_task_health("Whatever")
    assert health.exists is None
    assert health.error is not None


def test_has_never_run_reads_the_special_sched_code_not_a_hex_dump():
    # 267011 == 0x00041303 SCHED_S_TASK_HAS_NOT_RUN
    assert _decode_task_result(267011) == "has never run"


def test_unknown_win32_hresult_falls_back_to_the_system_message():
    text = _decode_task_result(-2147024891)  # 0x80070005 ACCESS_DENIED
    assert "0x80070005" in text
    assert text != "0x80070005"  # must have decoded something after the hex


def test_a_key_that_contains_a_colon_does_not_desync_the_fields_we_read():
    """'Repeat: Every:' has a colon inside the key itself. A naive
    split(':', 1) parser would mis-attribute its value to a fabricated
    'Repeat' key and, worse, could shift what a later real key like
    'Last Result:' resolves to if the parser tracked state. This parser
    matches known keys by exact prefix instead, so it must still get the
    real fields right with that line present."""
    fields = _parse_fields(REAL_HEALTHY_ENABLED_OUTPUT)
    assert fields["Last Result"] == "-2147020576"
    assert fields["Scheduled Task State"] == "Enabled"
    assert "Repeat" not in fields


# ---------------------------------------------------------------------------
# Real machine assertions — no mocking. This machine (2026-09-30) genuinely
# has WinClientTool_UnattendedMaintenance registered (Enabled, with a
# correct future Next Run Time) and genuinely has no task named the random
# string below.
# ---------------------------------------------------------------------------

def test_real_machine_has_the_unattended_maintenance_task_registered():
    health = check_task_health("WinClientTool_UnattendedMaintenance")
    assert health.exists is True
    assert health.enabled is True
    assert health.next_run is not None


def test_real_machine_has_no_task_with_this_made_up_name():
    health = check_task_health("WinClientTool_Nonexistent_Sentinel_Task_29d61c")
    assert health.exists is False
    assert health.error is None
