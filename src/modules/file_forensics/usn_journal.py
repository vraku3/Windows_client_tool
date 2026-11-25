"""NTFS USN (Update Sequence Number) Change Journal Inspector.

Queries USN journal status, sizing, and allocation on NTFS/ReFS volumes.
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
class UsnJournalInfo:
    drive_letter: str
    is_active: bool = False
    journal_id: str = ""
    max_size_mb: float = 0.0
    allocation_delta_mb: float = 0.0
    next_usn: str = ""
    write_tracking: str = ""
    details: str = ""


def query_usn_journal(drive: str = "C:") -> UsnJournalInfo:
    """Query USN change journal properties for a drive."""
    clean_drive = drive.rstrip("\\")
    if not clean_drive.endswith(":"):
        clean_drive += ":"

    info = UsnJournalInfo(drive_letter=clean_drive)

    try:
        proc = subprocess.run(
            ["fsutil.exe", "usn", "queryjournal", clean_drive],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            info.details = f"USN Journal query refused ({proc.returncode})"
            return info

        out = proc.stdout
        info.is_active = True

        for line in out.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v = v.strip()

            if "journal id" in k:
                info.journal_id = v
            elif "maximum size" in k:
                # e.g. "0x0000000002000000 (32.0 MB)"
                m = re.search(r"\(\s*([\d.]+)\s*MB\)", v, re.I)
                if m:
                    info.max_size_mb = float(m.group(1))
            elif "allocation delta" in k:
                m = re.search(r"\(\s*([\d.]+)\s*MB\)", v, re.I)
                if m:
                    info.allocation_delta_mb = float(m.group(1))
            elif "next usn" in k:
                info.next_usn = v
            elif "write range tracking" in k:
                info.write_tracking = v

        info.details = f"Active: ID {info.journal_id}, Max Size {info.max_size_mb:.1f} MB, Delta {info.allocation_delta_mb:.1f} MB."

    except Exception as exc:
        info.details = f"Error querying USN Journal: {exc}"

    return info
