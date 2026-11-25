"""Physical RAM / Standby List inspector and safe purge helper (Qt-free).

Uses native Win32 APIs (`GlobalMemoryStatusEx`, `GetPerformanceInfo`, and
`NtSetSystemInformation(SystemMemoryListInformation)`) with zero external
tools and full ConstrainedLanguage / SAC safety.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
from typing import Tuple

_SYSTEM_MEMORY_LIST_INFORMATION = 80
_MEMORY_PURGE_STANDBY_LIST = 4

_SE_PROFILE_SINGLE_PROCESS_NAME = "SeProfileSingleProcessPrivilege"
_TOKEN_ADJUST_PRIVILEGES = 0x0020
_TOKEN_QUERY = 0x0008
_SE_PRIVILEGE_ENABLED = 0x00000002


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", w.DWORD),
        ("dwMemoryLoad", w.DWORD),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class _PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", w.DWORD),
        ("ProcessCount", w.DWORD),
        ("ThreadCount", w.DWORD),
    ]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", w.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", w.DWORD), ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


@dataclass(frozen=True)
class MemorySnapshot:
    total_mb: float
    available_mb: float
    in_use_mb: float
    load_pct: int
    system_cache_mb: float
    kernel_paged_mb: float
    kernel_nonpaged_mb: float
    commit_total_mb: float
    commit_limit_mb: float


def get_memory_snapshot() -> MemorySnapshot:
    """Read current physical, cache, kernel, and commit memory counters."""
    ms = _MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        raise OSError("GlobalMemoryStatusEx failed")

    pi = _PERFORMANCE_INFORMATION()
    pi.cb = ctypes.sizeof(_PERFORMANCE_INFORMATION)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb):
        raise OSError("GetPerformanceInfo failed")

    mb = 1024.0 * 1024.0
    page_mb = pi.PageSize / mb
    total_mb = ms.ullTotalPhys / mb
    avail_mb = ms.ullAvailPhys / mb
    return MemorySnapshot(
        total_mb=total_mb,
        available_mb=avail_mb,
        in_use_mb=max(0.0, total_mb - avail_mb),
        load_pct=int(ms.dwMemoryLoad),
        system_cache_mb=pi.SystemCache * page_mb,
        kernel_paged_mb=pi.KernelPaged * page_mb,
        kernel_nonpaged_mb=pi.KernelNonpaged * page_mb,
        commit_total_mb=pi.CommitTotal * page_mb,
        commit_limit_mb=pi.CommitLimit * page_mb,
    )


def _enable_privilege(name: str) -> bool:
    adv = ctypes.windll.advapi32
    k32 = ctypes.windll.kernel32
    token = w.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), _TOKEN_ADJUST_PRIVILEGES | _TOKEN_QUERY, ctypes.byref(token)):
        return False
    try:
        luid = _LUID()
        if not adv.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = _TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = _SE_PRIVILEGE_ENABLED
        if not adv.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None):
            return False
        return k32.GetLastError() == 0
    finally:
        k32.CloseHandle(token)


def purge_standby_list() -> Tuple[bool, str, float]:
    """Purge Windows Standby Memory List (requires Administrator).

    Returns (ok, message, reclaimed_mb).
    """
    before = get_memory_snapshot()
    if not _enable_privilege(_SE_PROFILE_SINGLE_PROCESS_NAME):
        return False, "SeProfileSingleProcessPrivilege could not be enabled (run as Administrator).", 0.0

    nt = ctypes.windll.ntdll
    cmd = ctypes.c_int(_MEMORY_PURGE_STANDBY_LIST)
    status = nt.NtSetSystemInformation(
        _SYSTEM_MEMORY_LIST_INFORMATION,
        ctypes.byref(cmd),
        ctypes.sizeof(cmd),
    )
    if status != 0:
        return False, f"NtSetSystemInformation failed with NTSTATUS 0x{status & 0xFFFFFFFF:08X}.", 0.0

    after = get_memory_snapshot()
    reclaimed = max(0.0, after.available_mb - before.available_mb)
    msg = (
        f"Standby list purged. Available RAM: {before.available_mb:,.0f} MB -> "
        f"{after.available_mb:,.0f} MB (+{reclaimed:,.0f} MB reclaimed)."
    )
    return True, msg, reclaimed
