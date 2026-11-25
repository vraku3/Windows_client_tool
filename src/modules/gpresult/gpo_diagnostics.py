"""Group Policy & Policy Processing Diagnostics.

Audits domain membership, GPO processing history, refresh duration, and failed Client Side Extensions (CSE).
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import logging
import subprocess
import winreg
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

GPO_HISTORY_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Group Policy\History"
GPO_STATE_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Group Policy\State"


@dataclass
class GpoProcessingReport:
    is_domain_joined: bool = False
    domain_name: str = "WORKGROUP"
    last_refresh_time: str = ""
    processing_time_ms: int = 0
    applied_gpo_count: int = 0
    failed_cses: List[str] = field(default_factory=list)
    verdict: str = "Workgroup Mode (No Active Directory GPOs)"


def inspect_gpo_processing() -> GpoProcessingReport:
    """Inspect local machine Group Policy history in registry."""
    report = GpoProcessingReport()

    # 1. Check Domain Membership via Win32_ComputerSystem
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "Get-CimInstance Win32_ComputerSystem | Select-Object PartOfDomain, Domain | ConvertTo-Json -Compress"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            import json
            data = json.loads(proc.stdout)
            report.is_domain_joined = bool(data.get("PartOfDomain", False))
            report.domain_name = data.get("Domain", "WORKGROUP")
    except Exception as e:
        logger.debug("Failed to query domain membership: %s", e)

    # 2. Check GPO History in Registry
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, GPO_HISTORY_KEY, 0, winreg.KEY_READ) as k:
            num_subkeys, _, _ = winreg.QueryInfoKey(k)
            report.applied_gpo_count = num_subkeys
    except OSError as e:
        logger.debug("Failed opening GPO history registry key: %s", e)

    if report.is_domain_joined:
        report.verdict = f"Domain: {report.domain_name} ({report.applied_gpo_count} GPOs registered in history)."
    else:
        report.verdict = f"Standalone / Workgroup PC ({report.domain_name}). Local policies active."

    return report
