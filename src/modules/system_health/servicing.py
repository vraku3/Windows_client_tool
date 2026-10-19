"""DISM component-store servicing actions. No Qt -- callers (the module's
UI, and the --unattended stage) both drive this the same way."""
import os
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


def system_drive() -> str:
    """The Windows volume ("C:"), from %SystemDrive% -- never assumed."""
    drive = (os.environ.get("SystemDrive") or "C:").rstrip("\\")
    return drive if len(drive) == 2 and drive[1] == ":" else "C:"


def chkdsk_scheduled(drive: str) -> Optional[bool]:
    """Is a boot-time check queued for `drive`? Read back from Session
    Manager's BootExecute, where scheduling one adds an `autocheck autochk
    ... \\??\\C:` entry. None when the value could not be read."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager") as key:
            entries, _kind = winreg.QueryValueEx(key, "BootExecute")
    except OSError:
        return None
    if isinstance(entries, str):
        entries = [entries]
    target = ("\\??\\" + drive).lower()
    return any(target in (e or "").lower() for e in entries)


def run_chkdsk_schedule(timeout: int = 60) -> DismResult:
    """Schedules CHKDSK /f /r /x on the system drive for next reboot by
    answering its Y/N prompt. The answer is English, so on another display
    language the prompt may refuse it: success is decided by reading
    BootExecute back (`chkdsk_scheduled`), never by the exit code or text.
    Moved from Quick Fix (fix_actions.run_chkdsk)."""
    drive = system_drive()
    cmd = ["chkdsk", drive, "/f", "/r", "/x"]
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
