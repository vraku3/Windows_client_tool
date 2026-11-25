"""WinSxS Component Store & Servicing Packages Health Analyzer.

Analyzes CBS (Component Based Servicing) packages, superseded updates, and pending transactions.
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import logging
import re
import subprocess
import winreg
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CBS_PACKAGES_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\Packages"

# CBS Package States
STATE_ABSENT = 0
STATE_UNINSTALL_PENDING = 5
STATE_RESOLVING = 16
STATE_RESOLVED = 32
STATE_STAGED = 64
STATE_INSTALLED = 112
STATE_INSTALL_PENDING = 128
STATE_SUPERSEDED = 64  # In Windows 10/11 CBS, 64 is often superseded/staged


@dataclass
class WinSxSHealthSnapshot:
    total_packages: int = 0
    installed_count: int = 0
    superseded_count: int = 0
    staged_count: int = 0
    pending_count: int = 0
    reclaimable_estimated: bool = False
    reclaimable_desc: str = ""
    component_cleanup_recommended: bool = False
    details: str = ""


def analyze_cbs_packages() -> WinSxSHealthSnapshot:
    """Analyze package states in the CBS registry hive."""
    installed = 0
    superseded = 0
    staged = 0
    pending = 0
    total = 0

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CBS_PACKAGES_KEY, 0, winreg.KEY_READ) as k:
            num_keys, _, _ = winreg.QueryInfoKey(k)
            total = num_keys
            for i in range(num_keys):
                try:
                    sub_name = winreg.EnumKey(k, i)
                    with winreg.OpenKey(k, sub_name, 0, winreg.KEY_READ) as sub:
                        state, _ = winreg.QueryValueEx(sub, "CurrentState")
                        state = int(state)
                        if state == STATE_INSTALLED:
                            installed += 1
                        elif state == STATE_SUPERSEDED:
                            superseded += 1
                        elif state in (STATE_STAGED, STATE_RESOLVED):
                            staged += 1
                        elif state in (STATE_INSTALL_PENDING, STATE_UNINSTALL_PENDING, STATE_RESOLVING):
                            pending += 1
                except OSError as e:
                    logger.debug("Failed reading CBS package %d: %s", i, e)
    except Exception as exc:
        logger.debug("Could not read CBS Packages key: %s", exc)

    recommend_cleanup = superseded >= 15 or staged >= 30

    desc = []
    if superseded > 0:
        desc.append(f"{superseded} superseded packages retained in component store.")
    if staged > 0:
        desc.append(f"{staged} staged/resolved packages.")
    if pending > 0:
        desc.append(f"{pending} servicing transactions pending across reboots.")

    summary_text = " • ".join(desc) if desc else "WinSxS component store is clean."

    return WinSxSHealthSnapshot(
        total_packages=total,
        installed_count=installed,
        superseded_count=superseded,
        staged_count=staged,
        pending_count=pending,
        reclaimable_estimated=recommend_cleanup,
        reclaimable_desc=summary_text,
        component_cleanup_recommended=recommend_cleanup,
        details=f"CBS Packages: {total} total, {installed} installed, {superseded} superseded, {staged} staged, {pending} pending.",
    )


def run_dism_analyze_component_store() -> Tuple[bool, str, Dict[str, str]]:
    """Execute dism /Online /Cleanup-Image /AnalyzeComponentStore and parse key metrics."""
    cmd = ["dism.exe", "/Online", "/Cleanup-Image", "/AnalyzeComponentStore"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = proc.stdout or ""
        metrics: Dict[str, str] = {}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                metrics[k.strip().lower()] = v.strip()

        if proc.returncode == 0:
            return True, "DISM Component Store analysis completed successfully.", metrics
        else:
            err = (proc.stderr or out).strip()
            return False, f"DISM refused ({proc.returncode}): {err}", metrics
    except Exception as e:
        return False, f"Failed to run DISM: {e}", {}
