"""Yellow-Bang PNP Device Auto-Diagnostic & Hardware ID Resolver.

Enumerates devices with ConfigManagerErrorCode != 0, decodes Windows CM error codes,
extracts Hardware IDs (VEN_xxxx & DEV_xxxx / VID_xxxx & PID_xxxx), and provides actionable fixes.
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CM_ERROR_CODES = {
    1: ("CM_PROB_NOT_CONFIGURED", "This device is not configured correctly. Needs driver update or INF install."),
    10: ("CM_PROB_FAILED_START", "This device cannot start. Hardware failure, firmware issue, or incompatible driver."),
    12: ("CM_PROB_NORMAL_CONFLICT", "This device cannot find enough free resources that it can use (I/O, IRQ conflict)."),
    14: ("CM_PROB_NEED_RESTART", "This device cannot work properly until you restart your computer."),
    18: ("CM_PROB_REINSTALL", "Reinstall the drivers for this device."),
    22: ("CM_PROB_DISABLED", "This device is disabled in Device Manager."),
    24: ("CM_PROB_FAILED_INSTALL", "This device is not present, is not working properly, or does not have all its drivers installed."),
    28: ("CM_PROB_FAILED_INSTALL", "The drivers for this device are not installed. Search for vendor driver via Hardware ID."),
    31: ("CM_PROB_FAILED_ADD", "This device is not working properly because Windows cannot load the drivers required for this device."),
    32: ("CM_PROB_REGISTRY_ERROR", "A driver (service) for this device has been disabled in the registry (e.g. Start=4)."),
    38: ("CM_PROB_DRIVER_BLOCKED", "Windows cannot load the device driver because a previous instance of the driver is still in memory."),
    39: ("CM_PROB_DRIVER_FAILED_LOAD", "Windows cannot load the device driver (driver binary corrupt or missing)."),
    43: ("CM_PROB_FAILED_POST_START", "Windows has stopped this device because it has reported problems. (Hardware error or crash)."),
    48: ("CM_PROB_FAILED_DRIVER_ENTRY", "The software for this device has been blocked from starting because it has known issues with Windows."),
    52: ("CM_PROB_UNSIGNED_DRIVER", "Windows cannot verify the digital signature for the drivers required for this device (blocked by Code Integrity)."),
}


@dataclass
class PnpErrorDevice:
    name: str
    device_id: str
    hardware_ids: List[str] = field(default_factory=list)
    error_code: int = 0
    error_name: str = ""
    error_description: str = ""
    vendor_id: str = ""
    product_id: str = ""
    recommended_fix: str = ""


def get_failing_pnp_devices() -> List[PnpErrorDevice]:
    """Query Win32_PnPEntity for devices with non-zero ConfigManagerErrorCode."""
    ps_cmd = r"""
Get-CimInstance Win32_PnPEntity -Filter "ConfigManagerErrorCode != 0" -ErrorAction SilentlyContinue |
    Select-Object Name, DeviceID, HardWareID, ConfigManagerErrorCode |
    ConvertTo-Json -Depth 2 -Compress
"""
    failing: List[PnpErrorDevice] = []
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []

        data = json.loads(proc.stdout)
        if isinstance(data, dict):
            data = [data]

        for d in data:
            name = d.get("Name") or "Unknown Device"
            dev_id = d.get("DeviceID") or ""
            hw_ids = d.get("HardWareID") or []
            if isinstance(hw_ids, str):
                hw_ids = [hw_ids]

            err_code = int(d.get("ConfigManagerErrorCode") or 0)
            err_name, err_desc = CM_ERROR_CODES.get(
                err_code,
                (f"Code {err_code}", f"Windows reported Configuration Manager error code {err_code}."),
            )

            # Extract VEN_xxxx and DEV_xxxx or VID_xxxx and PID_xxxx
            ven = ""
            dev = ""
            for hid in hw_ids:
                m_pci = re.search(r"VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})", hid)
                if m_pci:
                    ven = "0x" + m_pci.group(1).upper()
                    dev = "0x" + m_pci.group(2).upper()
                    break
                m_usb = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", hid)
                if m_usb:
                    ven = "0x" + m_usb.group(1).upper()
                    dev = "0x" + m_usb.group(2).upper()
                    break

            fix = "Inspect driver in Driver Manager or check vendor INF."
            if err_code == 28:
                fix = f"Install missing driver package matching Hardware ID {hw_ids[0] if hw_ids else dev_id}."
            elif err_code == 14:
                fix = "Restart computer to finish driver activation."
            elif err_code == 43:
                fix = "Power cycle device, re-seat hardware, or reinstall OEM vendor driver."
            elif err_code == 52:
                fix = "Driver is unsigned. Verify digital signature or obtain WHQL driver."

            failing.append(PnpErrorDevice(
                name=name,
                device_id=dev_id,
                hardware_ids=hw_ids,
                error_code=err_code,
                error_name=err_name,
                error_description=err_desc,
                vendor_id=ven,
                product_id=dev,
                recommended_fix=fix,
            ))

    except Exception as exc:
        logger.warning("Error querying failing PNP devices: %s", exc)

    return failing
