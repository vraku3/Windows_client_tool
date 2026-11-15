"""Cross-references Driver Manager's device list against Reliability
Monitor and Kernel-PnP event 219 driver load failures, pairing
error codes with concrete next steps.

Reliability Monitor records a device by free-text product/message fields,
not by hardware ID or device instance id -- there is no exact join
available. `crashes_for` is therefore substring matching, not an exact
match, and callers must present its result as approximate, not
authoritative.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.types import LogEntry

logger = logging.getLogger(__name__)


def crashes_for(device_name: str, records: List[LogEntry]) -> List[LogEntry]:
    """Best-effort: checks both raw["product_name"] (the WMI ProductName
    field reliability_reader.py captures there) and `message` (which
    reliability_reader.py falls back to using AS the product name when
    WMI's own Message field is empty) for a substring match in either
    direction -- a truncated name on one side or the other is common in
    real Reliability Monitor data."""
    if not device_name:
        return []
    needle = device_name.lower()

    def _matches(text: str) -> bool:
        text = (text or "").lower()
        return bool(text) and (needle in text or text in needle)

    return [
        r for r in records
        if _matches(r.raw.get("product_name", "")) or _matches(r.message)
    ]


_SUGGESTED_ACTIONS = {
    1: "Reinstall the driver, or check for an update.",
    3: "Free up memory or reinstall the driver -- it may be corrupted.",
    10: "Try updating the driver; if that doesn't help, roll it back.",
    18: "Reinstall the driver for this device.",
    19: "The registry entry for this driver may be corrupted -- reinstall it.",
    21: "Wait for Windows to finish removing this device, then reconnect it.",
    22: "Re-enable this device in Device Manager if it should be active.",
    24: "Check for a driver update, or reinstall the driver.",
    28: "No driver is installed for this device -- check for an update.",
    31: "Update or reinstall the driver -- Windows can't load it as-is.",
    32: "A driver for this device was disabled -- re-enable or reinstall it.",
    37: "Reinstall the driver -- Windows can't initialize it.",
    39: "The driver may be corrupted or missing -- reinstall it.",
    43: "Check Device Manager's error details, then update or reinstall the driver.",
}


def suggested_action(error_code: int) -> str:
    return _SUGGESTED_ACTIONS.get(
        error_code,
        "No specific guidance for this error code -- check Device Manager "
        "for details, or try updating the driver.")


# ---- Kernel-PnP Event 219 driver load failures -----------------------------------

@dataclass
class DriverLoadFailure:
    driver: str
    device_id: str
    status: str
    status_meaning: str
    count: int
    most_recent: str
    sample_times: List[str] = field(default_factory=list)


NTSTATUS_DESCRIPTIONS: Dict[int, str] = {
    0xC0000365: "STATUS_DEVICE_NOT_READY (device not ready or driver service not yet started)",
    0xC0000034: "STATUS_OBJECT_NAME_NOT_FOUND (driver binary or service key missing)",
    0xC000000E: "STATUS_NO_SUCH_DEVICE (device disconnected or not present)",
    0xC0000022: "STATUS_ACCESS_DENIED (access denied or driver blocked)",
    0xC0000428: "STATUS_INVALID_IMAGE_HASH (driver signature check failed)",
    0xC000007B: "STATUS_INVALID_IMAGE_FORMAT (driver binary corrupted or architecture mismatch)",
}

_PNP_219_RE = re.compile(
    r"The driver\s+(.*?)\s+failed to load\.\s*Device:\s*(.*?)\s*Status:\s*(0x[0-9a-fA-F]+)",
    re.IGNORECASE | re.DOTALL,
)


def ntstatus_meaning(status_hex: str) -> str:
    try:
        val = int(status_hex, 16)
        return NTSTATUS_DESCRIPTIONS.get(val, status_hex)
    except (ValueError, TypeError):
        return status_hex


def parse_driver_load_failures(events: List[Dict[str, Any]]) -> List[DriverLoadFailure]:
    """Group System log event 219 records by (driver, device_id)."""
    groups: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = {}
    for ev in events:
        msg = str(ev.get("Message") or "").strip()
        time_str = str(ev.get("Time") or ev.get("TimeCreated") or "").strip()
        m = _PNP_219_RE.search(msg)
        if not m:
            continue
        driver = m.group(1).strip()
        device_id = m.group(2).strip()
        status_hex = m.group(3).strip()
        meaning = ntstatus_meaning(status_hex)
        groups.setdefault((driver, device_id), []).append((time_str, status_hex, meaning))

    out: List[DriverLoadFailure] = []
    for (driver, device_id), records in groups.items():
        latest_time, latest_status, latest_meaning = records[0]
        out.append(DriverLoadFailure(
            driver=driver,
            device_id=device_id,
            status=latest_status,
            status_meaning=latest_meaning,
            count=len(records),
            most_recent=latest_time,
            sample_times=[t for t, _, _ in records[:5]],
        ))
    return out


def driver_load_failures_for(device_id_or_name: str, failures: List[DriverLoadFailure]) -> List[DriverLoadFailure]:
    """Find load failure records matching a device ID or driver name."""
    if not device_id_or_name:
        return []
    needle = device_id_or_name.lower()
    return [
        f for f in failures
        if needle in f.device_id.lower() or f.device_id.lower() in needle or needle in f.driver.lower()
    ]
