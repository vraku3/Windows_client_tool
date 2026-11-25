"""Event Log Retention & Saturation Sentinel.

Inspects event channels (Application, System, Security, PowerShell, Code Integrity):
1. Reads channel configuration via `wevtutil gl <channel>` (max size, retention policy).
2. Reads the oldest recorded event timestamp to measure actual retention in days.
3. Warns when critical event channels are wrapping too quickly (< 3 to 7 days).

Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MONITORED_CHANNELS = [
    "System",
    "Application",
    "Security",
    "Microsoft-Windows-PowerShell/Operational",
    "Microsoft-Windows-CodeIntegrity/Operational",
]


@dataclass
class ChannelRetention:
    channel_name: str
    enabled: bool = True
    max_size_mb: float = 0.0
    oldest_event_time: Optional[datetime] = None
    retention_days: Optional[float] = None
    is_saturated: bool = False
    status_label: str = "Healthy"
    recommendation: str = ""


def get_channel_config(channel: str) -> Dict[str, str]:
    """Parse `wevtutil gl <channel>` into key-value map."""
    cfg: Dict[str, str] = {}
    try:
        proc = subprocess.run(
            ["wevtutil.exe", "gl", channel],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    cfg[k.strip().lower()] = v.strip()
    except Exception as e:
        logger.debug("Failed to query wevtutil gl for %s: %s", channel, e)
    return cfg


def get_oldest_event_timestamp(channel: str) -> Optional[datetime]:
    """Retrieve timestamp of the oldest event currently stored in the channel."""
    try:
        proc = subprocess.run(
            ["wevtutil.exe", "qe", channel, "/c:1", "/rd:false", "/f:xml"],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None

        root = ET.fromstring(f"<Events>{proc.stdout}</Events>")
        ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
        evt = root.find("e:Event", ns)
        if evt is not None:
            time_elem = evt.find("e:System/e:TimeCreated", ns)
            if time_elem is not None:
                st = time_elem.get("SystemTime", "")
                if st:
                    # Parse ISO format e.g. 2026-11-10T12:00:00.123456Z
                    clean_st = st.split(".")[0].rstrip("Z")
                    return datetime.fromisoformat(clean_st).replace(tzinfo=timezone.utc)
    except Exception as e:
        logger.debug("Could not read oldest event for %s: %s", channel, e)
    return None


def inspect_channel_retention(channel: str) -> ChannelRetention:
    """Evaluate log retention window and saturation status."""
    cfg = get_channel_config(channel)
    enabled = cfg.get("enabled", "true").lower() == "true"
    max_size_bytes = 0
    try:
        max_size_bytes = int(cfg.get("maxsize", "0"))
    except ValueError as e:
        logger.debug("Invalid maxsize for channel %s: %s", channel, e)
    max_mb = max_size_bytes / (1024.0 * 1024.0)

    oldest = get_oldest_event_timestamp(channel)
    retention_days = None
    is_saturated = False
    status = "Healthy"
    rec = ""

    if oldest is not None:
        now = datetime.now(timezone.utc)
        delta = now - oldest
        retention_days = max(0.0, delta.total_seconds() / 86400.0)

        if retention_days < 3.0:
            is_saturated = True
            status = "Warning: Fast Wrap"
            rec = f"Log wraps in only {retention_days:.1f} days. Expand size from {max_mb:.0f} MB to {max(64.0, max_mb * 2):.0f} MB."
        elif retention_days < 7.0:
            status = "Notice: Moderate Retention"
            rec = f"Retention window is {retention_days:.1f} days."
        else:
            status = f"Healthy ({retention_days:.1f} days)"
    else:
        status = "No events or unreadable"

    return ChannelRetention(
        channel_name=channel,
        enabled=enabled,
        max_size_mb=max_mb,
        oldest_event_time=oldest,
        retention_days=retention_days,
        is_saturated=is_saturated,
        status_label=status,
        recommendation=rec,
    )


def audit_all_channel_retentions() -> List[ChannelRetention]:
    """Audit all monitored system and security channels."""
    return [inspect_channel_retention(ch) for ch in MONITORED_CHANNELS]
