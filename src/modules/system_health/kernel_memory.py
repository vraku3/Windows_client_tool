"""Kernel Memory & Pool Leak Hunter (PagedPool / NonPagedPool).

Uses in-box Microsoft-signed psapi.dll (GetPerformanceInfo) via ctypes.
Instant (< 1 ms), zero subprocesses, immune to SAC blocks and ConstrainedLanguage limits.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
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
        ("HandleCount", wintypes.DWORD),
        ("ProcessCount", wintypes.DWORD),
        ("ThreadCount", wintypes.DWORD),
    ]


@dataclass
class KernelMemorySnapshot:
    paged_pool_mb: float = 0.0
    nonpaged_pool_mb: float = 0.0
    kernel_total_mb: float = 0.0
    system_cache_mb: float = 0.0
    commit_total_mb: float = 0.0
    commit_limit_mb: float = 0.0
    commit_peak_mb: float = 0.0
    commit_ratio_pct: float = 0.0
    physical_total_mb: float = 0.0
    physical_avail_mb: float = 0.0
    handles_count: int = 0
    processes_count: int = 0
    threads_count: int = 0
    is_nonpaged_leak: bool = False
    is_commit_saturated: bool = False
    status_summary: str = "Healthy"


def get_kernel_memory_snapshot() -> Optional[KernelMemorySnapshot]:
    """Capture precise kernel pool allocations, handle counts, and commit charge."""
    try:
        perf = PERFORMANCE_INFORMATION()
        perf.cb = ctypes.sizeof(PERFORMANCE_INFORMATION)
        res = ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(perf), perf.cb)
        if not res:
            logger.warning("GetPerformanceInfo failed with error %d", ctypes.GetLastError())
            return None

        page_size = perf.PageSize
        mb_factor = 1024.0 * 1024.0

        paged_mb = (perf.KernelPaged * page_size) / mb_factor
        nonpaged_mb = (perf.KernelNonpaged * page_size) / mb_factor
        kernel_mb = (perf.KernelTotal * page_size) / mb_factor
        cache_mb = (perf.SystemCache * page_size) / mb_factor

        commit_tot_mb = (perf.CommitTotal * page_size) / mb_factor
        commit_lim_mb = (perf.CommitLimit * page_size) / mb_factor
        commit_peak_mb = (perf.CommitPeak * page_size) / mb_factor
        commit_ratio = (commit_tot_mb / commit_lim_mb * 100.0) if commit_lim_mb > 0 else 0.0

        phys_tot_mb = (perf.PhysicalTotal * page_size) / mb_factor
        phys_avail_mb = (perf.PhysicalAvailable * page_size) / mb_factor

        # Detect pool leaks or commit exhaustion
        # NonPagedPool > 2500 MB is abnormal on desktop Windows and indicates a kernel driver leak
        is_leak = nonpaged_mb >= 2500.0
        is_saturated = commit_ratio >= 90.0

        if is_leak and is_saturated:
            status = f"Critical: High NonPagedPool ({nonpaged_mb:.0f} MB) and high Commit Charge ({commit_ratio:.0f}%)"
        elif is_leak:
            status = f"Warning: High NonPagedPool ({nonpaged_mb:.0f} MB) — potential driver pool leak"
        elif is_saturated:
            status = f"Warning: Commit Charge near exhaustion ({commit_ratio:.0f}% of {commit_lim_mb:.0f} MB)"
        else:
            status = f"Healthy: NonPagedPool={nonpaged_mb:.0f} MB, PagedPool={paged_mb:.0f} MB, Commit={commit_ratio:.0f}%"

        return KernelMemorySnapshot(
            paged_pool_mb=paged_mb,
            nonpaged_pool_mb=nonpaged_mb,
            kernel_total_mb=kernel_mb,
            system_cache_mb=cache_mb,
            commit_total_mb=commit_tot_mb,
            commit_limit_mb=commit_lim_mb,
            commit_peak_mb=commit_peak_mb,
            commit_ratio_pct=commit_ratio,
            physical_total_mb=phys_tot_mb,
            physical_avail_mb=phys_avail_mb,
            handles_count=perf.HandleCount,
            processes_count=perf.ProcessCount,
            threads_count=perf.ThreadCount,
            is_nonpaged_leak=is_leak,
            is_commit_saturated=is_saturated,
            status_summary=status,
        )
    except Exception as exc:
        logger.error("Error reading kernel memory: %s", exc)
        return None
