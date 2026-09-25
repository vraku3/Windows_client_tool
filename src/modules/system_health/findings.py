"""Read-only System Health findings. No Qt, no writes -- every function
here is safe to call unelevated and safe to call from --unattended
--stages health.
"""
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from core.windows_utils import system_root

logger = logging.getLogger(__name__)


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
        if row and row[0].strip():
            task_names.append(row[0])
    # schtasks lists a task once per trigger, so one with several triggers
    # (USO_UxBroker) came back -- and was reported -- more than once.
    task_names = list(dict.fromkeys(task_names))

    for name in task_names:
        from_file = _task_xml_from_file(name)
        if from_file is not None:
            program = _extract_command_path(from_file)
            if program and not _program_exists(program):
                findings.append(_orphan_finding(name, program))
            continue
        try:
            xml_result = subprocess.run(
                ["schtasks", "/query", "/tn", name, "/xml"],
                capture_output=True, text=True, timeout=15,
                creationflags=0x08000000)
        except (OSError, subprocess.TimeoutExpired) as e:
            findings.append(Finding(
                id=f"orphaned_task_check_refused:{name}",
                title=f"Could not check scheduled task {name!r}",
                detail=str(e), severity="warning"))
            continue
        if xml_result.returncode != 0:
            findings.append(Finding(
                id=f"orphaned_task_check_refused:{name}",
                title=f"Could not check scheduled task {name!r}",
                detail=(xml_result.stderr or xml_result.stdout
                        or "schtasks refused").strip(),
                severity="warning"))
            continue
        program = _extract_command_path(xml_result.stdout)
        if program and not _program_exists(program):
            findings.append(_orphan_finding(name, program))
    return findings


def _orphan_finding(name: str, program: str) -> Finding:
    # Plain quotes, not repr(): repr doubled every backslash in the path.
    return Finding(
        id=f"orphaned_task:{name}",
        title="Scheduled task points at a missing program",
        detail=f"Task '{name}' runs '{program}', which does not exist.",
        severity="info",
    )


def _task_xml_from_file(name: str) -> Optional[str]:
    """The task's definition read straight from the System32 Tasks folder, or None.

    `schtasks /query /xml` writes through the console's legacy codepage, so a
    character it cannot represent came back as `?` ("Aplica?ii" for "Aplicatii"
    with a comma-below t) -- and the mangled path does not exist, so a task that
    points at a real program was reported as pointing at a missing one. The
    files Windows keeps are UTF-16 and lose nothing. Unreadable (they are
    admin-only) is simply None, and the caller falls back to schtasks."""
    root = os.path.join(system_root(), "System32", "Tasks")
    parts = [part for part in name.split("\\") if part]
    if not parts:
        return None
    try:
        with open(os.path.join(root, *parts), "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        logger.debug("Task file %s not readable (%s); using schtasks", name, exc)
        return None
    for encoding in ("utf-16", "utf-8-sig"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            logger.debug("Task file %s is not %s", name, encoding)
    return None


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
