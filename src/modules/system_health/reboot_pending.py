"""Pending Reboot Reason Matrix.

Inspects all Windows sub-systems for pending restart flags:
1. Component-Based Servicing (CBS): RebootPending & RebootInProgress
2. Windows Update (AU): RebootRequired & PostRebootReporting
3. Session Manager: PendingFileRenameOperations & PendingFileRenameOperations2
4. ComputerName: ActiveComputerName vs ComputerName
5. SCCM / Intune Client: Reboot pending flags

Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import logging
import winreg
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PendingRebootStatus:
    is_pending: bool = False
    reasons: List[str] = field(default_factory=list)
    queued_renames: List[str] = field(default_factory=list)
    cbs_pending: bool = False
    wu_pending: bool = False
    rename_pending: bool = False
    computer_rename_pending: bool = False
    details: str = ""


def get_pending_reboot_status() -> PendingRebootStatus:
    """Evaluate all system reboot indicators."""
    reasons: List[str] = []
    queued_renames: List[str] = []
    cbs_flag = False
    wu_flag = False
    rename_flag = False
    comp_flag = False

    # 1. Component-Based Servicing (CBS)
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
            0,
            winreg.KEY_READ,
        ):
            cbs_flag = True
            reasons.append("Component-Based Servicing (CBS) Update is pending restart.")
    except OSError as e:
        logger.debug("CBS RebootPending key not found or error: %s", e)

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootInProgress",
            0,
            winreg.KEY_READ,
        ):
            cbs_flag = True
            reasons.append("CBS servicing transaction is currently in progress across boot.")
    except OSError as e:
        logger.debug("CBS RebootInProgress key not found or error: %s", e)

    # 2. Windows Update (AU)
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
            0,
            winreg.KEY_READ,
        ):
            wu_flag = True
            reasons.append("Windows Update requires a restart to finish installing patches.")
    except OSError as e:
        logger.debug("AU RebootRequired key not found or error: %s", e)

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\PostRebootReporting",
            0,
            winreg.KEY_READ,
        ):
            wu_flag = True
            reasons.append("Windows Update post-reboot servicing is queued.")
    except OSError as e:
        logger.debug("AU PostRebootReporting key not found or error: %s", e)

    # 3. PendingFileRenameOperations in Session Manager
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager",
            0,
            winreg.KEY_READ,
        ) as k:
            for val_name in ("PendingFileRenameOperations", "PendingFileRenameOperations2"):
                try:
                    val, _ = winreg.QueryValueEx(k, val_name)
                    if val:
                        rename_flag = True
                        items = val if isinstance(val, list) else [val]
                        for item in items:
                            if item and isinstance(item, str):
                                clean_item = item.strip()
                                # Filter out raw target null markers
                                if clean_item and clean_item not in queued_renames:
                                    # Normalize NT device prefixes like \??\
                                    if clean_item.startswith("\\??\\"):
                                        clean_item = clean_item[4:]
                                    elif clean_item.startswith("*1\\??\\"):
                                        clean_item = clean_item[6:]
                                    queued_renames.append(clean_item)
                except OSError as e:
                    logger.debug("Value %s not found in Session Manager: %s", val_name, e)
    except OSError as e:
        logger.debug("Session Manager key not accessible: %s", e)

    if rename_flag:
        count = len(queued_renames)
        reasons.append(f"{count} file(s) queued for deletion or rename on reboot (PendingFileRenameOperations).")

    # 4. Computer Rename Pending
    try:
        act = ""
        pend = ""
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName",
            0,
            winreg.KEY_READ,
        ) as k1:
            act, _ = winreg.QueryValueEx(k1, "ComputerName")
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName",
            0,
            winreg.KEY_READ,
        ) as k2:
            pend, _ = winreg.QueryValueEx(k2, "ComputerName")
        if act and pend and act != pend:
            comp_flag = True
            reasons.append(f"Computer rename is pending reboot: '{act}' -> '{pend}'.")
    except OSError as e:
        logger.debug("ComputerName check error: %s", e)

    # 5. SCCM / ConfigMgr reboot flags
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\SMS\Mobile Client\Reboot Management\RebootData",
            0,
            winreg.KEY_READ,
        ):
            reasons.append("ConfigMgr / SCCM software deployment requires a system reboot.")
    except OSError as e:
        logger.debug("SCCM RebootData key not found or error: %s", e)

    is_pending = bool(reasons)
    details = "\n".join(f"• {r}" for r in reasons) if is_pending else "No pending reboots detected. System state is clean."

    return PendingRebootStatus(
        is_pending=is_pending,
        reasons=reasons,
        queued_renames=queued_renames,
        cbs_pending=cbs_flag,
        wu_pending=wu_flag,
        rename_pending=rename_flag,
        computer_rename_pending=comp_flag,
        details=details,
    )
