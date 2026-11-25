"""Volume Shadow Storage (VSS) Allocation & Capacity Explorer.

Inspects shadow copy storage space on all volumes (System Restore storage, shadow copies).
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ShadowStorageEntry:
    for_volume: str
    storage_volume: str
    used_mb: float = 0.0
    allocated_mb: float = 0.0
    max_mb: float = 0.0
    max_percent: Optional[float] = None
    is_excessive: bool = False
    details: str = ""


def _parse_size_mb(text: str) -> float:
    """Convert human strings like '5.234 GB', '500 MB', '10.5 TB' into MB."""
    m = re.search(r"([\d.]+)\s*(KB|MB|GB|TB|B)\b", text, re.I)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "TB":
        return val * 1024.0 * 1024.0
    elif unit == "GB":
        return val * 1024.0
    elif unit == "MB":
        return val
    elif unit == "KB":
        return val / 1024.0
    return val / (1024.0 * 1024.0)


def get_shadow_storage_entries() -> List[ShadowStorageEntry]:
    """Execute vssadmin list shadowstorage and parse all volume associations."""
    entries: List[ShadowStorageEntry] = []
    try:
        proc = subprocess.run(
            ["vssadmin.exe", "list", "shadowstorage"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            logger.debug("vssadmin list shadowstorage refused (%d)", proc.returncode)
            return []

        out = proc.stdout
        current: Dict[str, str] = {}

        def flush():
            f_vol = current.get("for volume", "")
            s_vol = current.get("shadow copy storage volume", "")
            if f_vol:
                used = _parse_size_mb(current.get("used shadow copy storage space", ""))
                alloc = _parse_size_mb(current.get("allocated shadow copy storage space", ""))
                max_str = current.get("maximum shadow copy storage space", "")
                max_val = _parse_size_mb(max_str)

                pct_m = re.search(r"\(([\d.]+)%\)", max_str)
                pct = float(pct_m.group(1)) if pct_m else None

                # Flag excessive if used/allocated > 40 GB or > 15%
                is_exc = (pct is not None and pct > 15.0) or alloc > 40000.0

                entries.append(ShadowStorageEntry(
                    for_volume=f_vol,
                    storage_volume=s_vol,
                    used_mb=used,
                    allocated_mb=alloc,
                    max_mb=max_val,
                    max_percent=pct,
                    is_excessive=is_exc,
                    details=f"Used: {used / 1024.0:.1f} GB | Allocated: {alloc / 1024.0:.1f} GB | Max: {max_val / 1024.0:.1f} GB ({pct or 0:.0f}%)",
                ))

        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k_clean = k.strip().lower()
                v_clean = v.strip()
                if k_clean == "for volume":
                    if current:
                        flush()
                        current = {}
                current[k_clean] = v_clean

        if current:
            flush()

    except Exception as exc:
        logger.warning("Error reading shadow storage: %s", exc)

    return entries
