"""Read-only System Health findings. No Qt, no writes -- every function
here is safe to call unelevated and safe to call from --unattended
--stages health.
"""
import glob
import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Finding:
    id: str
    title: str
    detail: str
    severity: str  # "info" | "warning" -- no "danger": nothing here deletes anything


def check_pending_servicing() -> Optional[Finding]:
    """WinSxS\\pending.xml existing means a servicing transaction is
    mid-flight (usually resolved by the next reboot). Detecting it is
    exactly what it sounds like -- a file-existence check -- but it
    answers a real question ("why won't DISM let me run ResetBase right
    now") that this module's own ResetBase gate (2.3) needs to ask too."""
    windir = os.environ.get("windir", r"C:\Windows")
    path = os.path.join(windir, "WinSxS", "pending.xml")
    if not os.path.exists(path):
        return None
    return Finding(
        id="pending_servicing",
        title="A servicing transaction is pending",
        detail=(
            f"{path} exists, meaning Windows has an in-progress "
            "component update. This usually clears on the next reboot. "
            "DISM component-store operations may refuse to run until it does."
        ),
        severity="warning",
    )


def check_orphaned_scheduled_tasks() -> List[Finding]:
    """A scheduled task whose Action names a program path that no longer
    exists on disk. Uses schtasks /query /xml (per-task), which -- like
    every other schtasks call in this codebase -- can be read
    unelevated; a refused read is reported as a Finding of its own
    rather than silently producing zero results (a refusal is never
    reported as "nothing found" -- CLAUDE.md's own recurring rule)."""
    findings: List[Finding] = []
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=30,
            creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [Finding(
            id="orphaned_tasks_refused",
            title="Could not enumerate scheduled tasks",
            detail=str(e), severity="warning")]
    if result.returncode != 0:
        return [Finding(
            id="orphaned_tasks_refused",
            title="Could not enumerate scheduled tasks",
            detail=(result.stderr or result.stdout or "schtasks refused").strip(),
            severity="warning")]

    import csv
    import io
    task_names = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if row:
            task_names.append(row[0])

    for name in task_names:
        try:
            xml_result = subprocess.run(
                ["schtasks", "/query", "/tn", name, "/xml"],
                capture_output=True, text=True, timeout=15,
                creationflags=0x08000000)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if xml_result.returncode != 0:
            continue
        program = _extract_command_path(xml_result.stdout)
        if program and not _program_exists(program):
            findings.append(Finding(
                id=f"orphaned_task:{name}",
                title=f"Scheduled task points at a missing program",
                detail=f"Task {name!r} runs {program!r}, which does not exist.",
                severity="info",
            ))
    return findings


def _extract_command_path(task_xml: str) -> Optional[str]:
    import re
    match = re.search(r"<Command>(.*?)</Command>", task_xml, re.IGNORECASE)
    if not match:
        return None
    return os.path.expandvars(match.group(1).strip().strip('"'))


def _program_exists(path: str) -> bool:
    if os.path.isabs(path) and os.path.exists(path):
        return True
    # A bare exe name (e.g. "notepad.exe") resolves via PATH -- shutil.which
    # is the correct check for that shape, os.path.exists alone would
    # always say "missing" for anything not given as a full path.
    import shutil
    return shutil.which(path) is not None


def check_upgrade_headroom() -> Finding:
    """Free space on the system drive against a practical minimum for a
    feature update -- NOT an official Microsoft figure (Microsoft
    publishes a 64 GB *install* minimum for Windows 11 itself, not a
    per-upgrade free-space number), stated as an approximation in the
    UI text itself so nobody mistakes this for an authoritative number."""
    import shutil as _shutil
    system_drive = os.environ.get("SystemDrive", "C:") + "\\"
    total, used, free = _shutil.disk_usage(system_drive)
    free_gb = free / (1024 ** 3)
    threshold_gb = 20  # a commonly-cited practical minimum, not an official one
    if free_gb < threshold_gb:
        return Finding(
            id="upgrade_headroom",
            title=f"Only {free_gb:.1f} GB free on {system_drive}",
            detail=(
                f"Feature updates commonly need roughly {threshold_gb} GB "
                "of free space to install (a practical rule of thumb, not "
                "an official Microsoft minimum) -- you're below that."
            ),
            severity="warning",
        )
    return Finding(
        id="upgrade_headroom",
        title=f"{free_gb:.1f} GB free on {system_drive}",
        detail=f"Above the ~{threshold_gb} GB practical minimum for a feature update.",
        severity="info",
    )


def all_findings() -> List[Finding]:
    findings = []
    pending = check_pending_servicing()
    if pending:
        findings.append(pending)
    findings.extend(check_orphaned_scheduled_tasks())
    findings.append(check_upgrade_headroom())
    return findings
