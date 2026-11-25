"""Storage Integrity (TRIM, Dirty Bit, Chkdsk) & Boot Health (Fast Startup, WinRE, Secure Boot) Engine.

Pure Python (Qt-free), robust and SAC-safe.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import winreg
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.windows_utils import system32

logger = logging.getLogger(__name__)

REAGENT_XML_PATH = os.path.join(system32(), "Recovery", "ReAgent.xml")


@dataclass
class VolumeIntegrity:
    drive_letter: str       # e.g. "C:"
    file_system: str        # e.g. "NTFS"
    total_bytes: int = 0
    free_bytes: int = 0
    is_dirty: bool = False
    chkdsk_queued: bool = False
    status_text: str = "Clean"


@dataclass
class BootHealthSnapshot:
    fast_startup_enabled: bool = False
    kernel_uptime_hours: float = 0.0
    kernel_uptime_days: float = 0.0
    winre_enabled: bool = False
    winre_location: str = ""
    winre_bcd_id: str = ""
    secure_boot_enabled: bool = False
    boot_execute: List[str] = None
    trim_ntfs_enabled: bool = True
    trim_refs_enabled: bool = True
    volumes: List[VolumeIntegrity] = None
    overall_health: str = "Healthy"
    recommendations: List[str] = None


def get_fast_startup_info() -> Dict[str, Any]:
    """Read Fast Startup state from registry and calculate kernel uptime."""
    enabled = False
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Power",
            0,
            winreg.KEY_READ,
        ) as k:
            val, _ = winreg.QueryValueEx(k, "HiberbootEnabled")
            enabled = bool(val)
    except Exception as e:
        logger.debug("Could not read HiberbootEnabled: %s", e)

    uptime_hours = 0.0
    uptime_days = 0.0
    try:
        import psutil
        boot_time = psutil.boot_time()
        uptime_seconds = max(0.0, time.time() - boot_time)
        uptime_hours = uptime_seconds / 3600.0
        uptime_days = uptime_seconds / 86400.0
    except Exception as e:
        logger.debug("Could not calculate uptime: %s", e)

    return {
        "enabled": enabled,
        "uptime_hours": uptime_hours,
        "uptime_days": uptime_days,
        "needs_cold_reboot": enabled and uptime_days >= 7.0,
    }


def get_winre_info(xml_path: str = REAGENT_XML_PATH) -> Dict[str, Any]:
    """Parse Windows Recovery Environment state from ReAgent.xml."""
    info = {
        "enabled": False,
        "location": "",
        "bcd_id": "",
        "auto_repair": True,
        "exists": False,
    }
    if not os.path.isfile(xml_path):
        return info

    info["exists"] = True
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        install_state = root.find("InstallState")
        if install_state is not None:
            info["enabled"] = install_state.get("state", "0") == "1"

        bcd = root.find("WinreBCD")
        if bcd is not None:
            info["bcd_id"] = bcd.get("id", "")

        loc = root.find("WinreLocation")
        if loc is not None:
            info["location"] = loc.get("path", "")

        repair = root.find("IsAutoRepairOn")
        if repair is not None:
            info["auto_repair"] = repair.get("state", "1") == "1"

    except Exception as e:
        logger.debug("Failed to parse ReAgent.xml: %s", e)

    return info


def get_secure_boot_info() -> Dict[str, Any]:
    """Read Secure Boot UEFI state from registry."""
    sb_enabled = False
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\SecureBoot\State",
            0,
            winreg.KEY_READ,
        ) as k:
            val, _ = winreg.QueryValueEx(k, "UEFISecureBootEnabled")
            sb_enabled = bool(val)
    except Exception as e:
        logger.debug("Could not read SecureBoot state: %s", e)

    return {"enabled": sb_enabled}


def get_trim_status() -> Dict[str, Any]:
    """Query TRIM delete notification status via fsutil."""
    ntfs_trim = True
    refs_trim = True
    try:
        proc = subprocess.run(
            ["fsutil.exe", "behavior", "query", "DisableDeleteNotify"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.splitlines():
                if "ntfs" in line.lower() and "1" in line:
                    ntfs_trim = False
                elif "refs" in line.lower() and "1" in line:
                    refs_trim = False
    except Exception as e:
        logger.debug("Error checking TRIM status: %s", e)

    return {
        "ntfs_trim": ntfs_trim,
        "refs_trim": refs_trim,
        "trim_healthy": ntfs_trim and refs_trim,
    }


def get_queued_chkdsk_drives() -> List[str]:
    """Read BootExecute to find drives scheduled for autochk."""
    queued = []
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager",
            0,
            winreg.KEY_READ,
        ) as k:
            val, _ = winreg.QueryValueEx(k, "BootExecute")
            entries = val if isinstance(val, list) else [val]
            for e in entries:
                if isinstance(e, str) and ("autochk /p" in e or "\\??\\" in e):
                    m = re.search(r"\\([A-Za-z]:)", e)
                    if m:
                        queued.append(m.group(1).upper())
                    else:
                        queued.append("All")
    except Exception as e:
        logger.debug("Could not read BootExecute: %s", e)
    return queued


def query_volumes_integrity() -> List[VolumeIntegrity]:
    """Inspect all fixed local drives for file system integrity, size, and dirty bit."""
    import psutil

    queued_chkdsk = get_queued_chkdsk_drives()
    results: List[VolumeIntegrity] = []

    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        partitions = []

    for part in partitions:
        drive = part.mountpoint.rstrip("\\")
        if not drive or len(drive) < 2 or drive[1] != ":":
            continue

        fs = part.fstype or "NTFS"
        total = 0
        free = 0
        try:
            usage = psutil.disk_usage(part.mountpoint)
            total = usage.total
            free = usage.free
        except Exception as e:
            logger.debug("Failed getting disk_usage for %s: %s", part.mountpoint, e)

        # Query dirty bit via fsutil
        is_dirty = False
        try:
            p = subprocess.run(
                ["fsutil.exe", "dirty", "query", drive],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out = p.stdout.lower()
            if "is dirty" in out and "not dirty" not in out:
                is_dirty = True
        except Exception as e:
            logger.debug("fsutil dirty query failed for %s: %s", drive, e)

        chkdsk_pending = drive in queued_chkdsk or "All" in queued_chkdsk
        status = "Dirty (Corruption Detected)" if is_dirty else ("Chkdsk Queued on Boot" if chkdsk_pending else "Clean")

        results.append(VolumeIntegrity(
            drive_letter=drive,
            file_system=fs,
            total_bytes=total,
            free_bytes=free,
            is_dirty=is_dirty,
            chkdsk_queued=chkdsk_pending,
            status_text=status,
        ))

    return results


def get_boot_storage_health() -> BootHealthSnapshot:
    """Aggregate complete Storage Integrity & Boot Health status."""
    fs_info = get_fast_startup_info()
    winre = get_winre_info()
    sb = get_secure_boot_info()
    trim = get_trim_status()
    volumes = query_volumes_integrity()

    recs: List[str] = []
    overall = "Healthy"

    # Evaluate recommendations
    if any(v.is_dirty for v in volumes):
        overall = "Critical"
        recs.append("One or more volumes have the NTFS dirty bit set. Run chkdsk /f to fix filesystem corruption.")

    if not trim.get("ntfs_trim", True):
        if overall != "Critical":
            overall = "Warning"
        recs.append("NTFS TRIM is disabled. SSD performance and lifespan are degraded. Re-enable TRIM via fsutil.")

    if not winre.get("enabled", False):
        if overall != "Critical":
            overall = "Warning"
        recs.append("Windows Recovery Environment (WinRE) is disabled. Automated boot recovery and Reset PC will not function.")

    if fs_info.get("needs_cold_reboot"):
        if overall == "Healthy":
            overall = "Notice"
        recs.append(f"Kernel uptime is {fs_info['uptime_days']:.1f} days due to Fast Startup. A full restart is recommended to flush kernel memory.")

    return BootHealthSnapshot(
        fast_startup_enabled=fs_info["enabled"],
        kernel_uptime_hours=fs_info["uptime_hours"],
        kernel_uptime_days=fs_info["uptime_days"],
        winre_enabled=winre["enabled"],
        winre_location=winre.get("location", ""),
        winre_bcd_id=winre.get("bcd_id", ""),
        secure_boot_enabled=sb["enabled"],
        trim_ntfs_enabled=trim["ntfs_trim"],
        trim_refs_enabled=trim["refs_trim"],
        volumes=volumes,
        overall_health=overall,
        recommendations=recs,
    )
