"""DPC / ISR Latency & Platform Timer Investigator Engine.

Provides real-time diagnostic insight into:
1. Windows kernel timer resolution (NtQueryTimerResolution): Current resolution (0.5ms - 15.625ms).
2. Hardware clock source (QueryPerformanceFrequency): Invariant TSC vs HPET vs ACPI PM.
3. Kernel interrupt & DPC distribution (NtQuerySystemInformation SystemInterruptInformation):
   Per-core DPC counts, rates, and core imbalances.
4. Kernel execution latency & micro-stutter sampling (QPC high-resolution jitter analysis):
   Detects micro-stutter spikes (>1000us) that cause game frame pacing hitches and audio crackling.

Qt-free, testable on any machine.
"""
from __future__ import annotations

import ctypes
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TimerResolutionInfo:
    min_resolution_ms: float
    max_resolution_ms: float
    current_resolution_ms: float
    is_high_resolution: bool
    verdict: str


@dataclass
class QpcClockInfo:
    frequency_hz: int
    frequency_mhz: float
    clock_source: str
    is_hpet: bool
    verdict: str


@dataclass
class CoreInterruptStat:
    core_index: int
    dpc_count: int
    dpc_rate: int
    dpc_bypass_count: int


@dataclass
class SystemInterruptStats:
    total_dpc_count: int
    total_dpc_rate: int
    core_count: int
    cores: List[CoreInterruptStat] = field(default_factory=list)
    imbalanced_core: Optional[int] = None
    distribution_note: str = ""


@dataclass
class LatencySampleResult:
    duration_seconds: float
    samples_count: int
    max_latency_us: float
    avg_latency_us: float
    stutter_spikes_1000us: int
    severe_spikes_2000us: int
    verdict: str
    status: str  # "clean", "warning", "critical"


@dataclass
class LatencyHealthReport:
    timer: TimerResolutionInfo
    clock: QpcClockInfo
    interrupts: SystemInterruptStats
    sample: Optional[LatencySampleResult] = None
    summary: str = ""


class _SystemInterruptInformation(ctypes.Structure):
    _fields_ = [
        ("ContextSwitches", ctypes.c_ulong),
        ("DpcCount", ctypes.c_ulong),
        ("DpcRate", ctypes.c_ulong),
        ("TimeIncrement", ctypes.c_ulong),
        ("DpcBypassCount", ctypes.c_ulong),
        ("ApcBypassCount", ctypes.c_ulong),
    ]


def get_timer_resolution() -> TimerResolutionInfo:
    """Query current Windows kernel timer resolution via NtQueryTimerResolution."""
    if os.name != "nt":
        return TimerResolutionInfo(15.625, 0.5, 1.0, True, "Synthetic Non-Windows")

    min_res = ctypes.c_ulong()
    max_res = ctypes.c_ulong()
    cur_res = ctypes.c_ulong()

    try:
        ntdll = ctypes.WinDLL("ntdll")
        status = ntdll.NtQueryTimerResolution(
            ctypes.byref(min_res),
            ctypes.byref(max_res),
            ctypes.byref(cur_res),
        )
        if status != 0:
            raise OSError(f"NtQueryTimerResolution failed with status 0x{status:X}")

        # Values are in 100-nanosecond units (10,000 units = 1 ms)
        min_ms = min_res.value / 10000.0
        max_ms = max_res.value / 10000.0
        cur_ms = cur_res.value / 10000.0
    except Exception as exc:
        logger.warning("Could not query NtQueryTimerResolution: %s", exc)
        return TimerResolutionInfo(15.625, 0.5, 15.625, False, f"Could not query: {exc}")

    is_high = (cur_ms <= 1.05)
    if cur_ms >= 15.0:
        verdict = f"{cur_ms:.3f} ms — Standard Windows Default (Power-saving mode)"
    elif cur_ms <= 0.55:
        verdict = f"{cur_ms:.3f} ms — Maximum High-Resolution (Pinned to 0.5 ms)"
    elif cur_ms <= 1.05:
        verdict = f"{cur_ms:.3f} ms — High-Resolution (1.0 ms requested by active app/game)"
    else:
        verdict = f"{cur_ms:.3f} ms — Custom Resolution"

    return TimerResolutionInfo(
        min_resolution_ms=round(min_ms, 3),
        max_resolution_ms=round(max_ms, 3),
        current_resolution_ms=round(cur_ms, 3),
        is_high_resolution=is_high,
        verdict=verdict,
    )


def get_qpc_clock_info() -> QpcClockInfo:
    """Query QueryPerformanceFrequency and identify the underlying platform clock source."""
    if os.name != "nt":
        return QpcClockInfo(10000000, 10.0, "Invariant TSC (Optimal)", False, "10.0 MHz - Optimal Invariant TSC")

    freq = ctypes.c_int64()
    try:
        ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
        f_hz = freq.value
    except Exception as exc:
        logger.warning("Could not query QueryPerformanceFrequency: %s", exc)
        return QpcClockInfo(0, 0.0, "Unknown", False, f"Could not query: {exc}")

    f_mhz = round(f_hz / 1_000_000.0, 3)

    # 10.0 MHz is modern Invariant TSC
    # ~14.318 MHz is HPET
    # ~3.579 MHz is ACPI PM Timer
    if 9.9 <= f_mhz <= 10.1:
        src = "Invariant TSC (Optimal)"
        is_hpet = False
        verdict = f"{f_mhz:.3f} MHz — Invariant TSC (Low overhead, lowest jitter)"
    elif 14.0 <= f_mhz <= 14.5:
        src = "HPET (High Precision Event Timer)"
        is_hpet = True
        verdict = (
            f"{f_mhz:.3f} MHz — HPET Active. Warning: Forced HPET increases QPC call "
            "overhead and can cause frame pacing micro-stutter in games."
        )
    elif 3.5 <= f_mhz <= 3.7:
        src = "ACPI Power Management Timer"
        is_hpet = False
        verdict = f"{f_mhz:.3f} MHz — ACPI PM Timer (Legacy timer, slow read overhead)"
    else:
        src = f"Custom Hardware Timer ({f_mhz:.3f} MHz)"
        is_hpet = False
        verdict = f"{f_mhz:.3f} MHz — Custom platform timer"

    return QpcClockInfo(
        frequency_hz=f_hz,
        frequency_mhz=f_mhz,
        clock_source=src,
        is_hpet=is_hpet,
        verdict=verdict,
    )


def get_system_interrupt_stats() -> SystemInterruptStats:
    """Query per-core DPC counts and rates via NtQuerySystemInformation (SystemInterruptInformation = 23)."""
    n_cpus = os.cpu_count() or 1
    if os.name != "nt":
        return SystemInterruptStats(0, 0, n_cpus, [], None, "Synthetic non-Windows environment")

    buf_type = _SystemInterruptInformation * n_cpus
    buf = buf_type()
    ret_len = ctypes.c_ulong()

    try:
        ntdll = ctypes.WinDLL("ntdll")
        status = ntdll.NtQuerySystemInformation(
            23,  # SystemInterruptInformation
            ctypes.byref(buf),
            ctypes.sizeof(buf),
            ctypes.byref(ret_len),
        )
        if status != 0:
            raise OSError(f"NtQuerySystemInformation(23) failed with 0x{status:X}")
    except Exception as exc:
        logger.warning("Could not query SystemInterruptInformation: %s", exc)
        return SystemInterruptStats(0, 0, n_cpus, [], None, f"Could not read: {exc}")

    cores: List[CoreInterruptStat] = []
    tot_dpcs = 0
    tot_rate = 0

    for i in range(n_cpus):
        info = buf[i]
        tot_dpcs += info.DpcCount
        tot_rate += info.DpcRate
        cores.append(CoreInterruptStat(
            core_index=i,
            dpc_count=info.DpcCount,
            dpc_rate=info.DpcRate,
            dpc_bypass_count=info.DpcBypassCount,
        ))

    imbalanced_core = None
    note = "Evenly distributed across cores"
    if tot_dpcs > 100000:
        for c in cores:
            if c.dpc_count > (tot_dpcs * 0.75):
                imbalanced_core = c.core_index
                note = f"Core {c.core_index} handles {int(c.dpc_count / tot_dpcs * 100)}% of all system DPCs"
                break

    return SystemInterruptStats(
        total_dpc_count=tot_dpcs,
        total_dpc_rate=tot_rate,
        core_count=n_cpus,
        cores=cores,
        imbalanced_core=imbalanced_core,
        distribution_note=note,
    )


def sample_kernel_latency(
    duration_seconds: float = 1.0,
    callback: Optional[Callable[[int], None]] = None,
) -> LatencySampleResult:
    """Measure real-time kernel scheduling jitter and DPC execution interruptions.

    Samples microsecond elapsed time deltas. An executing DPC routine in an elevated
    driver pauses the user-mode thread, causing a measurable spike in elapsed delta.
    Spikes > 1000 us (1 ms) directly correlate with desktop/game micro-stutter and audio crackling.
    """
    if os.name != "nt":
        return LatencySampleResult(
            duration_seconds=duration_seconds,
            samples_count=100,
            max_latency_us=120.0,
            avg_latency_us=45.0,
            stutter_spikes_1000us=0,
            severe_spikes_2000us=0,
            verdict="Clean — synthetic non-Windows environment",
            status="clean",
        )

    kernel32 = ctypes.windll.kernel32
    qpc = kernel32.QueryPerformanceCounter
    qpf = kernel32.QueryPerformanceFrequency
    freq = ctypes.c_int64()
    qpf(ctypes.byref(freq))
    f_hz = freq.value
    if f_hz <= 0:
        return LatencySampleResult(0, 0, 0, 0, 0, 0, "QueryPerformanceFrequency unavailable", "warning")

    t_end = time.perf_counter() + duration_seconds
    c_prev = ctypes.c_int64()
    c_cur = ctypes.c_int64()

    qpc(ctypes.byref(c_prev))
    max_latency_us = 0.0
    total_jitter_us = 0.0
    samples = 0
    spikes_1000 = 0
    spikes_2000 = 0

    target_sleep_s = 0.001  # 1 ms sleep interval
    target_sleep_us = 1000.0

    last_pct = 0
    start_t = time.perf_counter()

    while time.perf_counter() < t_end:
        time.sleep(target_sleep_s)
        qpc(ctypes.byref(c_cur))
        diff_us = ((c_cur.value - c_prev.value) * 1_000_000.0) / f_hz
        jitter_us = max(0.0, diff_us - target_sleep_us)

        if jitter_us > max_latency_us:
            max_latency_us = jitter_us
        if jitter_us >= 2000.0:
            spikes_2000 += 1
            spikes_1000 += 1
        elif jitter_us >= 1000.0:
            spikes_1000 += 1

        total_jitter_us += jitter_us
        samples += 1
        c_prev.value = c_cur.value

        if callback:
            pct = int(((time.perf_counter() - start_t) / duration_seconds) * 100)
            if pct != last_pct and pct <= 100:
                callback(pct)
                last_pct = pct

    avg_us = (total_jitter_us / samples) if samples > 0 else 0.0

    if max_latency_us < 500.0:
        verdict = f"Excellent ({max_latency_us:.1f} µs max) — real-time audio and game frame pacing stable."
        status = "clean"
    elif max_latency_us < 1000.0:
        verdict = f"Good ({max_latency_us:.1f} µs max) — suitable for normal desktop and gaming use."
        status = "clean"
    elif max_latency_us < 2000.0:
        verdict = (
            f"Warning ({max_latency_us:.1f} µs max, {spikes_1000} spike(s)) — DPC latency delays "
            "detected (>1000 µs). May cause occasional frame drops or audio stutter."
        )
        status = "warning"
    else:
        verdict = (
            f"Critical ({max_latency_us:.1f} µs max, {spikes_2000} severe spike(s)) — driver DPC execution "
            "exceeds 2000 µs! Frame pacing hitching and audio dropouts expected."
        )
        status = "critical"

    return LatencySampleResult(
        duration_seconds=duration_seconds,
        samples_count=samples,
        max_latency_us=round(max_latency_us, 1),
        avg_latency_us=round(avg_us, 1),
        stutter_spikes_1000us=spikes_1000,
        severe_spikes_2000us=spikes_2000,
        verdict=verdict,
        status=status,
    )


def diagnose_latency_health() -> LatencyHealthReport:
    """Collect instant snapshot of timers and interrupt distribution."""
    timer = get_timer_resolution()
    clock = get_qpc_clock_info()
    interrupts = get_system_interrupt_stats()

    parts = [f"Timer: {timer.current_resolution_ms:.3f} ms", f"Clock: {clock.clock_source}"]
    if interrupts.imbalanced_core is not None:
        parts.append(interrupts.distribution_note)

    summary = " | ".join(parts)
    return LatencyHealthReport(
        timer=timer,
        clock=clock,
        interrupts=interrupts,
        summary=summary,
    )

