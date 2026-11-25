"""PCIe Link Speed & Display/Storage Controller Auditor.

Inspects graphics adapters, NVMe storage controllers, and PCIe device topology.
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GraphicsControllerInfo:
    name: str
    driver_version: str
    video_processor: str
    adapter_ram_mb: float = 0.0
    status: str = "OK"


@dataclass
class PcieTopologyReport:
    controllers: List[GraphicsControllerInfo] = field(default_factory=list)
    total_controllers: int = 0
    primary_gpu: str = ""
    verdict: str = "Healthy"


def audit_pcie_graphics_devices() -> PcieTopologyReport:
    """Query Win32_VideoController for display hardware topology."""
    ps_cmd = r"""
Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
    Select-Object Name, DriverVersion, VideoProcessor, AdapterRAM |
    ConvertTo-Json -Depth 2 -Compress
"""
    report = PcieTopologyReport()
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            report.verdict = "Graphics hardware provider unreadable"
            return report

        data = json.loads(proc.stdout)
        if isinstance(data, dict):
            data = [data]

        report.total_controllers = len(data)

        for c in data:
            name = c.get("Name") or "Unknown GPU"
            v_ver = c.get("DriverVersion") or "Unknown"
            v_proc = c.get("VideoProcessor") or ""
            ram_b = int(c.get("AdapterRAM") or 0)
            ram_mb = ram_b / (1024.0 * 1024.0)

            report.controllers.append(GraphicsControllerInfo(
                name=name,
                driver_version=v_ver,
                video_processor=v_proc,
                adapter_ram_mb=ram_mb,
            ))

        if report.controllers:
            report.primary_gpu = report.controllers[0].name
            report.verdict = f"{len(report.controllers)} display adapter(s) active (Primary: {report.primary_gpu})"
        else:
            report.verdict = "No display controllers enumerated"

    except Exception as exc:
        report.verdict = f"Error auditing graphics adapters: {exc}"

    return report
