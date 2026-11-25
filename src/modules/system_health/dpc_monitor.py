"""DPC (Deferred Procedure Call) & ISR Latency Spike Monitor.

Monitors processor DPC and Hardware Interrupt rates to identify system stutter,
driver execution bottlenecks, and audio glitch causes.
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class DpcIsrSample:
    percent_processor_time: float = 0.0
    percent_dpc_time: float = 0.0
    percent_interrupt_time: float = 0.0
    dpcs_queued_per_sec: int = 0
    interrupts_per_sec: int = 0
    is_spiking: bool = False
    verdict: str = "DPC & Interrupt rates normal"


def sample_dpc_isr_latency() -> DpcIsrSample:
    """Sample current processor DPC and ISR rates via Win32_PerfFormattedData_PerfOS_Processor."""
    ps_cmd = r"""
$p = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -ErrorAction SilentlyContinue |
    Select-Object PercentProcessorTime, PercentDPCTime, PercentInterruptTime, DPCsQueuedPersec, InterruptsPersec
$p | ConvertTo-Json -Compress
"""
    sample = DpcIsrSample()
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            sample.verdict = "DPC counters unreadable"
            return sample

        data = json.loads(proc.stdout)
        cpu_pct = float(data.get("PercentProcessorTime") or 0)
        dpc_pct = float(data.get("PercentDPCTime") or 0)
        isr_pct = float(data.get("PercentInterruptTime") or 0)
        dpcs_sec = int(data.get("DPCsQueuedPersec") or 0)
        isrs_sec = int(data.get("InterruptsPersec") or 0)

        # Spike detection: if DPC time > 3% or Interrupt time > 3% continuously, it causes audio dropouts/stutter
        spiking = dpc_pct >= 3.0 or isr_pct >= 3.0

        if spiking:
            verdict = f"High DPC/ISR Execution: DPC={dpc_pct:.1f}%, ISR={isr_pct:.1f}% (potential driver latency bottleneck)"
        else:
            verdict = f"Optimal: DPC={dpc_pct:.1f}%, Interrupts={isr_pct:.1f}% ({isrs_sec:,} int/s)"

        return DpcIsrSample(
            percent_processor_time=cpu_pct,
            percent_dpc_time=dpc_pct,
            percent_interrupt_time=isr_pct,
            dpcs_queued_per_sec=dpcs_sec,
            interrupts_per_sec=isrs_sec,
            is_spiking=spiking,
            verdict=verdict,
        )
    except Exception as exc:
        sample.verdict = f"Error sampling DPC latency: {exc}"
        return sample
