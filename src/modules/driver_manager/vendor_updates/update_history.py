"""Persistent record of what THIS APP's own vendor-update checks and
installs have found and done, per device -- survives app restarts,
unlike driver_module's in-memory `_applied_update_tokens` (which only
exists to power same-session Undo). This is what answers "is the driver
up to date", "is there an update available", "did it update", and
"when" -- the status the driver table shows per row.

Stored at %APPDATA%/WindowsTweaker/driver_updates/update_history.json,
next to the NVIDIA pfid cache -- one small JSON object keyed by
device_id (the PNP device instance id), matching the same key
driver_module already uses for _applied_update_tokens.

Why "up to date" is never a raw version comparison: a vendor's own
marketing version (AMD's "26.8.1") and Windows' internal driver version
("32.0.31041.3013") are unrelated numbering schemes for AMD -- there is
no arithmetic that turns one into the other, so comparing them directly
would be a fabricated answer wearing a confident face. Instead, "up to
date" is answered by EQUALITY against what THIS APP itself last
installed: after a successful install, both the vendor version we
applied and the Windows version Windows reported immediately afterward
are recorded together as a pair. A later check either confirms both
still match (confidently "up to date") or names exactly what changed
(the OS reports something else now, or the vendor has since published
something else) -- never a guess bridging the two numbering schemes.
"""
import json
import logging
import os
import threading
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# One process-wide lock: this file is read-modify-written (never a bare
# overwrite of one field), and driver_module's bulk check can update many
# devices back-to-back from one Worker -- a second concurrent caller
# (the per-row check a user triggers by hand) must not race it.
_LOCK = threading.Lock()

# last_check_outcome values -- a closed set, not a free-form string, so a
# typo doesn't silently become a new, unhandled status forever.
OUTCOME_NEVER_CHECKED = "never_checked"
OUTCOME_UPDATE_FOUND = "update_found"
OUTCOME_NO_UPDATE = "no_update"
OUTCOME_CHECK_FAILED = "check_failed"


def _history_path() -> str:
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    directory = os.path.join(base, "WindowsTweaker", "driver_updates")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "update_history.json")


@dataclass
class DeviceHistory:
    device_id: str
    device_name: str = ""
    vendor: str = ""
    last_checked_at: Optional[str] = None       # ISO 8601 UTC
    last_check_outcome: str = OUTCOME_NEVER_CHECKED
    last_seen_vendor_version: Optional[str] = None
    last_check_error: Optional[str] = None
    last_applied_vendor_version: Optional[str] = None
    last_applied_windows_version: Optional[str] = None
    last_applied_at: Optional[str] = None       # ISO 8601 UTC
    last_applied_mode: Optional[str] = None     # "light" | "full"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_all() -> Dict[str, dict]:
    """Never raises: a missing, corrupt, or unreadable file is an empty
    history, the same as a fresh install -- never an uncaught exception
    out of a status-tracking feature that must not be able to crash the
    driver table it feeds."""
    path = _history_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("update_history: could not read %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("update_history: %s did not contain a JSON object -- ignoring", path)
        return {}
    return data


def _save_all(data: Dict[str, dict]) -> None:
    """Best-effort: a failed write is logged, never raised -- a check or
    install that already succeeded must not be reported as failed just
    because its OWN history couldn't be persisted afterward."""
    path = _history_path()
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except OSError as exc:
        logger.warning("update_history: could not write %s: %s", path, exc)


_KNOWN_FIELDS = {f.name for f in fields(DeviceHistory)}


def get(device_id: str) -> Optional[DeviceHistory]:
    """None for "no history at all" -- a device never checked, or an
    empty/unreadable store. Unknown keys in a stored record (e.g. from a
    future version of this app) are dropped rather than raising, so an
    older build reading a newer file's history degrades gracefully."""
    if not device_id:
        return None
    with _LOCK:
        data = _load_all()
    record = data.get(device_id)
    if not isinstance(record, dict):
        return None
    filtered = {k: v for k, v in record.items() if k in _KNOWN_FIELDS}
    try:
        return DeviceHistory(**filtered)
    except TypeError as exc:
        logger.warning("update_history: malformed record for %s: %s", device_id, exc)
        return None


def get_all() -> Dict[str, DeviceHistory]:
    """Every recorded device's history at once, keyed by device_id --
    what driver_module's table population uses so populating N rows
    costs one file read, not N (get() alone, called per row, would
    re-read and re-parse the whole file on every keystroke in the
    filter box). Malformed individual records are dropped (logged),
    same as get()'s single-record behavior -- one bad entry must not
    blank the whole table's status column."""
    with _LOCK:
        data = _load_all()
    result: Dict[str, DeviceHistory] = {}
    for device_id, record in data.items():
        if not isinstance(record, dict):
            continue
        filtered = {k: v for k, v in record.items() if k in _KNOWN_FIELDS}
        try:
            result[device_id] = DeviceHistory(**filtered)
        except TypeError as exc:
            logger.warning("update_history: malformed record for %s: %s", device_id, exc)
    return result


def record_check(device_id: str, device_name: str, vendor: str, outcome: str,
                 seen_vendor_version: Optional[str] = None,
                 error: Optional[str] = None) -> None:
    """Called after every check attempt, regardless of outcome -- a
    failed check is itself a fact worth recording (when, and why), never
    silently dropped."""
    if not device_id:
        return
    with _LOCK:
        data = _load_all()
        existing = data.get(device_id, {})
        existing.update({
            "device_id": device_id,
            "device_name": device_name,
            "vendor": vendor,
            "last_checked_at": _now_iso(),
            "last_check_outcome": outcome,
            "last_seen_vendor_version": seen_vendor_version,
            "last_check_error": error,
        })
        data[device_id] = existing
        _save_all(data)


def record_applied(device_id: str, device_name: str, vendor: str,
                   applied_vendor_version: str,
                   applied_windows_version: Optional[str], mode: str) -> None:
    """Called once, right after a successful install. applied_windows_version
    is None when a fresh post-install read wasn't available (still
    records that an install happened and when -- a partial fact is
    better than none) -- status_label treats a None here as "can't
    confirm 'up to date' from Windows' own version yet", never as a
    match."""
    if not device_id:
        return
    with _LOCK:
        data = _load_all()
        existing = data.get(device_id, {})
        existing.update({
            "device_id": device_id,
            "device_name": device_name,
            "vendor": vendor,
            "last_applied_vendor_version": applied_vendor_version,
            "last_applied_windows_version": applied_windows_version,
            "last_applied_at": _now_iso(),
            "last_applied_mode": mode,
        })
        data[device_id] = existing
        _save_all(data)


def _format_when(iso_timestamp: Optional[str]) -> str:
    if not iso_timestamp:
        return "unknown time"
    try:
        dt = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return iso_timestamp
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def status_label(current_windows_version: str, history: Optional[DeviceHistory]) -> str:
    """The one-line status driver_module shows per row. Pure function
    (no I/O, no Qt) so it's testable without a fake filesystem or a
    display -- current_windows_version is the device's CURRENT
    driver.version from the live table, history is whatever get()
    returned for it (None is a real, common case: never checked)."""
    if history is None:
        return "Not checked yet"

    if history.last_applied_at is not None:
        applied_when = _format_when(history.last_applied_at)
        windows_confirmed = (history.last_applied_windows_version is not None
                             and history.last_applied_windows_version == current_windows_version)
        vendor_still_current = (history.last_seen_vendor_version is None
                                or history.last_seen_vendor_version == history.last_applied_vendor_version)
        if not windows_confirmed:
            if history.last_applied_windows_version is None:
                return (f"Updated to {history.last_applied_vendor_version} via this app "
                       f"on {applied_when} -- Windows version not yet confirmed, Refresh to check")
            return (f"Driver changed since this app updated it to "
                   f"{history.last_applied_vendor_version} on {applied_when} "
                   f"(Windows now reports {current_windows_version}) -- re-check recommended")
        if vendor_still_current:
            return f"Up to date -- updated to {history.last_applied_vendor_version} via this app on {applied_when}"
        return (f"Update available: vendor now shows {history.last_seen_vendor_version} "
               f"(you updated to {history.last_applied_vendor_version} via this app on {applied_when})")

    checked_when = _format_when(history.last_checked_at)
    if history.last_check_outcome == OUTCOME_UPDATE_FOUND:
        return (f"Vendor shows {history.last_seen_vendor_version} (checked {checked_when}) "
               f"-- not compared to your installed version")
    if history.last_check_outcome == OUTCOME_NO_UPDATE:
        return f"No update found by {history.vendor} (checked {checked_when})"
    if history.last_check_outcome == OUTCOME_CHECK_FAILED:
        return f"Last check failed: {history.last_check_error} ({checked_when})"
    return "Not checked yet"
