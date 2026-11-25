"""Storage Spaces & Degraded Pools Monitor.

Queries Windows Storage Management CIM provider (root/Microsoft/Windows/Storage)
to inspect Storage Pools, Physical Disks, and Degraded virtual disks.
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# HealthStatus codes: 0=Healthy, 1=Warning, 2=Unhealthy
HEALTH_STATUS_LABELS = {0: "Healthy", 1: "Warning", 2: "Unhealthy"}

# OperationalStatus codes: 2=OK, 6=Degraded, 7=Stressed, 8=Predictive Failure, 9=Error
OP_STATUS_LABELS = {
    1: "Other",
    2: "OK",
    3: "Degraded",
    4: "Stressed",
    5: "Predictive Failure",
    6: "Error",
    7: "Non-Recoverable Error",
    8: "Starting",
    9: "Stopping",
    10: "Stopped",
    11: "In Service",
    12: "No Contact",
    13: "Lost Communication",
    14: "Aborted",
    15: "Dormant",
}

MEDIA_TYPES = {3: "HDD", 4: "SSD", 5: "SCM", 0: "Unspecified"}


@dataclass
class StoragePoolInfo:
    friendly_name: str
    health_status: int
    health_label: str
    operational_status_codes: List[int] = field(default_factory=list)
    operational_desc: str = "OK"
    total_bytes: int = 0
    allocated_bytes: int = 0
    is_degraded: bool = False


@dataclass
class PhysicalDiskInfo:
    friendly_name: str
    media_type: str
    health_status: int
    health_label: str
    operational_status_codes: List[int] = field(default_factory=list)
    size_bytes: int = 0
    is_failing: bool = False


@dataclass
class StoragePoolsOverview:
    pools: List[StoragePoolInfo] = field(default_factory=list)
    disks: List[PhysicalDiskInfo] = field(default_factory=list)
    is_degraded: bool = False
    verdict: str = "Healthy"


def inspect_storage_spaces() -> StoragePoolsOverview:
    """Query MSFT_StoragePool and MSFT_PhysicalDisk via PowerShell CIM."""
    ps_cmd = r"""
$pools = Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_StoragePool -ErrorAction SilentlyContinue |
    Select-Object FriendlyName, OperationalStatus, HealthStatus, Size, AllocatedSize
$disks = Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_PhysicalDisk -ErrorAction SilentlyContinue |
    Select-Object FriendlyName, MediaType, OperationalStatus, HealthStatus, Size
[PSCustomObject]@{
    Pools = @($pools)
    Disks = @($disks)
} | ConvertTo-Json -Depth 3 -Compress
"""
    overview = StoragePoolsOverview()

    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            overview.verdict = "Storage Spaces provider unreadable"
            return overview

        data = json.loads(proc.stdout)
        pools_raw = data.get("Pools", [])
        disks_raw = data.get("Disks", [])

        if isinstance(pools_raw, dict):
            pools_raw = [pools_raw]
        if isinstance(disks_raw, dict):
            disks_raw = [disks_raw]

        is_degraded = False

        for p in pools_raw:
            name = p.get("FriendlyName") or "Unnamed Pool"
            h_code = int(p.get("HealthStatus") or 0)
            op_codes = p.get("OperationalStatus") or [2]
            if not isinstance(op_codes, list):
                op_codes = [op_codes]

            h_label = HEALTH_STATUS_LABELS.get(h_code, f"Status {h_code}")
            op_desc = ", ".join(OP_STATUS_LABELS.get(c, str(c)) for c in op_codes)

            pool_deg = h_code != 0 or any(c in (3, 5, 6, 7) for c in op_codes)
            if pool_deg:
                is_degraded = True

            overview.pools.append(StoragePoolInfo(
                friendly_name=name,
                health_status=h_code,
                health_label=h_label,
                operational_status_codes=op_codes,
                operational_desc=op_desc,
                total_bytes=int(p.get("Size") or 0),
                allocated_bytes=int(p.get("AllocatedSize") or 0),
                is_degraded=pool_deg,
            ))

        for d in disks_raw:
            d_name = d.get("FriendlyName") or "Physical Disk"
            m_code = int(d.get("MediaType") or 0)
            h_code = int(d.get("HealthStatus") or 0)
            op_codes = d.get("OperationalStatus") or [2]
            if not isinstance(op_codes, list):
                op_codes = [op_codes]

            disk_failing = h_code != 0 or any(c in (3, 5, 6, 7) for c in op_codes)
            if disk_failing:
                is_degraded = True

            overview.disks.append(PhysicalDiskInfo(
                friendly_name=d_name,
                media_type=MEDIA_TYPES.get(m_code, "Disk"),
                health_status=h_code,
                health_label=HEALTH_STATUS_LABELS.get(h_code, str(h_code)),
                operational_status_codes=op_codes,
                size_bytes=int(d.get("Size") or 0),
                is_failing=disk_failing,
            ))

        overview.is_degraded = is_degraded
        if is_degraded:
            overview.verdict = "DEGRADED: Storage pool or physical disk failure detected!"
        elif overview.pools:
            overview.verdict = f"All {len(overview.pools)} storage pool(s) and {len(overview.disks)} physical drive(s) healthy."
        else:
            overview.verdict = "No Storage Spaces pools configured (standard disk topology)."

    except Exception as exc:
        overview.verdict = f"Error querying Storage Spaces: {exc}"

    return overview
