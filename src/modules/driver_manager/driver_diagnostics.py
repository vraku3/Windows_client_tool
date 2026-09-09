"""Cross-references Driver Manager's device list against Reliability
Monitor, and pairs each ConfigManagerErrorCode with a concrete next step.

Reliability Monitor records a device by free-text product/message fields,
not by hardware ID or device instance id -- there is no exact join
available. `crashes_for` is therefore substring matching, not an exact
match, and callers must present its result as approximate, not
authoritative.
"""
from typing import List

from core.types import LogEntry


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
