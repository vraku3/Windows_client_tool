# src/modules/process_explorer/process_actions.py
from __future__ import annotations
import ctypes
import logging
import sys
from typing import Optional, List, Tuple

import psutil

logger = logging.getLogger(__name__)

_PROCESS_SUSPEND_RESUME = 0x0800

# psutil priority constants (Windows-only)
if sys.platform == "win32":
    PRIORITY_LEVELS = {
        "idle":         psutil.IDLE_PRIORITY_CLASS,
        "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
        "normal":       psutil.NORMAL_PRIORITY_CLASS,
        "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
        "high":         psutil.HIGH_PRIORITY_CLASS,
        "realtime":     psutil.REALTIME_PRIORITY_CLASS,
    }
    _ntdll = ctypes.windll.ntdll
    _ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
    _ntdll.NtSuspendProcess.restype  = ctypes.c_long
    _ntdll.NtResumeProcess.argtypes  = [ctypes.c_void_p]
    _ntdll.NtResumeProcess.restype   = ctypes.c_long
else:
    PRIORITY_LEVELS = {}
    _ntdll = None


def created_at(pid: int) -> Optional[float]:
    """The process creation time (psutil, epoch seconds) -- its identity
    together with the PID. None when it cannot be read."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def _same_process(proc, created: Optional[float]) -> bool:
    return created is None or abs(proc.create_time() - created) < 0.5


def kill_process(pid: int, created: Optional[float] = None) -> Tuple[bool, str]:
    """End `pid`. With `created`, only if it is still the process that was
    confirmed: Windows reuses PIDs, and a dialog can stay open for minutes."""
    try:
        proc = psutil.Process(pid)
        if not _same_process(proc, created):
            return False, (f"Process {pid} exited; its PID now belongs to {proc.name()} "
                           "-- not ended.")
        proc.kill()
        return True, ""
    except psutil.NoSuchProcess:
        return False, f"Process {pid} is no longer running."
    except psutil.AccessDenied:
        return False, "Access denied — run as administrator."
    except Exception as e:
        return False, str(e)


def kill_tree(pid: int, created: Optional[float] = None) -> Tuple[bool, List[str]]:
    """Kill process and all descendants. Returns (all_ok, list_of_errors).
    With `created`, nothing is ended unless the root is still the confirmed
    process."""
    errors = []
    try:
        proc = psutil.Process(pid)
        if not _same_process(proc, created):
            return False, [f"PID {pid} exited and now belongs to {proc.name()} -- nothing ended"]
        children = proc.children(recursive=True)
        for child in children:
            ok, err = kill_process(child.pid)
            if not ok:
                logger.warning("kill_tree: child PID %d failed: %s", child.pid, err)
                errors.append(f"PID {child.pid}: {err}")
        ok, err = kill_process(pid)
        if not ok:
            logger.warning("kill_tree: root PID %d failed: %s", pid, err)
            errors.append(f"PID {pid}: {err}")
    except psutil.NoSuchProcess:
        errors.append(f"PID {pid} is no longer running.")
    return len(errors) == 0, errors


def suspend_process(pid: int) -> Tuple[bool, str]:
    if _ntdll is None:
        return False, "Suspend is only supported on Windows."
    try:
        handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, pid)
        if not handle:
            return False, f"Could not open process {pid}."
        try:
            status = _ntdll.NtSuspendProcess(handle)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        if status != 0:
            return False, f"NtSuspendProcess returned 0x{status:08X}"
        return True, ""
    except Exception as e:
        return False, str(e)


def resume_process(pid: int) -> Tuple[bool, str]:
    if _ntdll is None:
        return False, "Resume is only supported on Windows."
    try:
        handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, pid)
        if not handle:
            return False, f"Could not open process {pid}."
        try:
            status = _ntdll.NtResumeProcess(handle)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        if status != 0:
            return False, f"NtResumeProcess returned 0x{status:08X}"
        return True, ""
    except Exception as e:
        return False, str(e)


def set_priority(pid: int, level: str) -> Tuple[bool, str]:
    if level not in PRIORITY_LEVELS:
        return False, f"Unknown priority '{level}'. Valid: {list(PRIORITY_LEVELS)}"
    try:
        psutil.Process(pid).nice(PRIORITY_LEVELS[level])
        return True, ""
    except psutil.NoSuchProcess:
        return False, f"Process {pid} is no longer running."
    except psutil.AccessDenied:
        return False, "Access denied — run as administrator."
    except Exception as e:
        return False, str(e)


def set_affinity(pid: int, cores: List[int]) -> Tuple[bool, str]:
    if not cores:
        return False, "Affinity mask must contain at least one CPU core."
    try:
        psutil.Process(pid).cpu_affinity(cores)
        return True, ""
    except psutil.NoSuchProcess:
        return False, f"Process {pid} is no longer running."
    except psutil.AccessDenied:
        return False, "Access denied — run as administrator."
    except Exception as e:
        return False, str(e)
