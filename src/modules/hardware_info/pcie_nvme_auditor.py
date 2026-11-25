"""PCIe Link & NVMe Health Auditor (Qt-free, ConstrainedLanguage-safe).

Detects enthusiast/gaming hardware bottlenecks:
- PCIe devices (GPUs / NVMe controllers) running below their negotiated max
  link width or link speed (via DEVPKEY_PciDevice_CurrentLinkWidth / MaxLinkWidth).
- Physical disks / NVMe drives with media errors, wear warnings, or thermal events
  (via Get-StorageReliabilityCounter / Get-PhysicalDisk).
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import List

from core.system_paths import powershell_exe

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PcieNvmeFinding:
    component: str
    category: str   # "PCIe Link" | "NVMe Health"
    status: str     # "OK" | "WARN" | "CRITICAL"
    detail: str


def audit_nvme_reliability() -> List[PcieNvmeFinding]:
    """Query StorageReliabilityCounter for NVMe/SSD media errors, wear, and temperature."""
    ps = (
        "Get-PhysicalDisk | ForEach-Object { "
        "$d = $_; "
        "$r = $d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
        "[PSCustomObject]@{ "
        "Name=$d.FriendlyName; Bus=$d.BusType; Health=$d.HealthStatus; "
        "Temp=$(if($r){$r.Temperature}else{$null}); "
        "TempMax=$(if($r){$r.TemperatureMax}else{$null}); "
        "Wear=$(if($r){$r.Wear}else{$null}); "
        "ReadErr=$(if($r){$r.ReadErrorsUncorrected}else{0}); "
        "WriteErr=$(if($r){$r.WriteErrorsUncorrected}else{0}) "
        "} } | ConvertTo-Json -Compress"
    )
    findings: List[PcieNvmeFinding] = []
    try:
        proc = subprocess.run(
            [powershell_exe(), "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return findings
        raw = json.loads(proc.stdout)
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            name = str(item.get("Name") or "Disk")
            bus = str(item.get("Bus") or "")
            health = str(item.get("Health") or "Unknown")
            wear = item.get("Wear")
            temp = item.get("Temp")
            r_err = int(item.get("ReadErr") or 0)
            w_err = int(item.get("WriteErr") or 0)

            status = "OK"
            notes = [f"Bus: {bus}", f"Health: {health}"]
            if temp is not None:
                notes.append(f"Temp: {temp}°C")
                if int(temp) >= 70:
                    status = "WARN"
            if wear is not None:
                notes.append(f"Wear: {wear}%")
                if int(wear) >= 85:
                    status = "WARN"
            if r_err > 0 or w_err > 0 or health.lower() not in ("healthy", "ok"):
                status = "CRITICAL"
                notes.append(f"Errors R/W: {r_err}/{w_err}")

            findings.append(PcieNvmeFinding(
                component=name,
                category="NVMe Health" if "nvme" in bus.lower() else "Storage Health",
                status=status,
                detail=" | ".join(notes),
            ))
    except Exception as e:
        logger.debug("NVMe reliability audit failed: %s", e)
    return findings


def audit_pcie_links() -> List[PcieNvmeFinding]:
    """Check GPU & NVMe PCIe negotiated link width/speed vs maximum capability."""
    ps = (
        "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'PCI\\*' -and "
        "($_.Class -eq 'Display' -or $_.Class -eq 'SCSIAdapter') } | ForEach-Object { "
        "$id = $_.InstanceId; "
        "$cw = (Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_PciDevice_CurrentLinkWidth' -ErrorAction SilentlyContinue).Data; "
        "$mw = (Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_PciDevice_MaxLinkWidth' -ErrorAction SilentlyContinue).Data; "
        "$cs = (Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_PciDevice_CurrentLinkSpeed' -ErrorAction SilentlyContinue).Data; "
        "$ms = (Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_PciDevice_MaxLinkSpeed' -ErrorAction SilentlyContinue).Data; "
        "if ($cw -or $mw) { "
        "[PSCustomObject]@{ Name=$_.FriendlyName; Class=$_.Class; CurW=$cw; MaxW=$mw; CurS=$cs; MaxS=$ms } "
        "} } | ConvertTo-Json -Compress"
    )
    findings: List[PcieNvmeFinding] = []
    try:
        proc = subprocess.run(
            [powershell_exe(), "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return findings
        raw = json.loads(proc.stdout)
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            name = str(item.get("Name") or "PCIe Device")
            cw, mw = item.get("CurW"), item.get("MaxW")
            cs, ms = item.get("CurS"), item.get("MaxS")
            status = "OK"
            if cw and mw and int(cw) < int(mw):
                status = "WARN"
            detail = f"Width: x{cw or '?'} / max x{mw or '?'} | Speed: Gen{cs or '?'} / max Gen{ms or '?'}"
            findings.append(PcieNvmeFinding(
                component=name,
                category="PCIe Link",
                status=status,
                detail=detail,
            ))
    except Exception as e:
        logger.debug("PCIe link audit failed: %s", e)
    return findings
