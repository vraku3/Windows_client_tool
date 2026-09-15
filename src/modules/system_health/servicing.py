"""DISM component-store servicing actions. No Qt -- callers (the module's
UI, and the --unattended stage) both drive this the same way."""
import subprocess
from dataclasses import dataclass
from typing import Optional


CREATE_NO_WINDOW = 0x08000000


@dataclass
class DismResult:
    command: str
    returncode: int
    output: str


def run_scan_health(timeout: int = 600) -> DismResult:
    """DISM /Online /Cleanup-Image /ScanHealth -- checks the component
    store for corruption. Does not repair anything itself; see
    `run_restore_health` for the repair action."""
    cmd = ["dism", "/Online", "/Cleanup-Image", "/ScanHealth"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))


def run_sfc_scan(timeout: int = 900) -> DismResult:
    """sfc /scannow -- scans and repairs protected Windows system files.
    Moved from Quick Fix (fix_actions.run_sfc) -- same command, same
    shape, relocated alongside the other servicing/repair operations."""
    cmd = ["sfc", "/scannow"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))


def run_restore_health(timeout: int = 1800) -> DismResult:
    """DISM /Online /Cleanup-Image /RestoreHealth -- repairs component
    store corruption ScanHealth detects. Moved from Quick Fix
    (fix_actions.run_dism); this module's ScanHealth previously
    explicitly deferred offering a repair action -- this is that
    deferred action, now added."""
    cmd = ["dism", "/online", "/cleanup-image", "/restorehealth"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))


def run_chkdsk_schedule(timeout: int = 60) -> DismResult:
    """Schedules CHKDSK C: /f /r /x for next reboot by answering the
    Y/N prompt. Moved from Quick Fix (fix_actions.run_chkdsk) -- same
    command and same answered-prompt trick, relocated."""
    cmd = ["chkdsk", "C:", "/f", "/r", "/x"]
    proc = subprocess.run(cmd, input="Y\n", capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))


def run_component_cleanup(timeout: int = 1800) -> DismResult:
    """Moved verbatim from _large_items_tab.py's _run_dism -- same
    command, same shape, just relocated (a servicing operation, not a
    file to select-and-delete)."""
    cmd = ["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))


def run_reset_base(timeout: int = 1800) -> DismResult:
    """DISM /Online /Cleanup-Image /StartComponentCleanup /ResetBase --
    removes the ABILITY to uninstall superseded Windows updates. This is
    the single most irreversible action in this module. The UI layer
    (system_health_module.py) is responsible for every gate before this
    is ever called: a fresh ScanHealth showing no corruption, a typed
    "RESETBASE" confirmation, and a forced restore point -- this function
    itself does none of that gating, it only runs the command, so the
    gating logic is testable independent of ever actually invoking DISM."""
    cmd = ["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup", "/ResetBase"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))
