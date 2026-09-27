"""Disk-related System log events -- the earliest warning of failing
hardware, often appearing before SMART flags anything at all. Qt-free.

Confirmed live on this real machine: a genuine hardware I/O error (event
154, "IO operation... failed due to a hardware error") on one disk, and
dozens of recurring "duplicate disk identifiers" warnings (event 158) on
another over the past week -- neither visible anywhere else in this app
before this reader existed.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Providers that log disk/storage hardware and filesystem problems.
_PROVIDERS = ("disk", "Disk", "volsnap", "stornvme", "storahci", "Ntfs")

#: The event IDs worth surfacing, and what each one actually means --
#: sourced from Microsoft's own documentation for these providers, not
#: guessed from the numbers alone.
EVENT_MEANINGS: Dict[int, str] = {
    7: "Bad block detected -- the disk remapped a failing sector.",
    9: "The device did not respond in time (a controller timeout).",
    11: "The driver detected a controller error.",
    15: "The device was not ready for an operation.",
    51: "A paging I/O operation failed -- Windows retried a page-file read/write.",
    52: "An error was reported by the file system while updating a bad-cluster list.",
    129: "The storage adapter reset a device that stopped responding (a timeout).",
    153: "An I/O operation was retried after an error.",
    154: "An I/O operation failed due to a hardware error.",
    157: "The disk was surprise-removed (unplugged or lost connection without warning).",
    158: "Windows sees the same disk identifiers on more than one disk (a cloned or duplicated disk signature).",
}

#: Severity classification -- errors that reflect data actually being at
#: risk right now (154, 157, 11, 15) versus warnings that are a signal worth
#: reading but not yet proof of loss.
_ERROR_IDS = {11, 15, 154, 157}

_DISK_RE = re.compile(r"[Dd]isk\s+(\d+)")


@dataclass
class DiskEvent:
    event_id: int
    level: str
    time: str
    disk: Optional[str]
    message: str
    meaning: str

    @property
    def is_error(self) -> bool:
        return self.event_id in _ERROR_IDS


def _extract_disk(message: str) -> Optional[str]:
    match = _DISK_RE.search(message)
    return match.group(1) if match else None


def read_disk_events(days: int = 14, max_events: int = 200, timeout: int = 30) -> Optional[List[DiskEvent]]:
    """None means the read failed or was refused; an empty list means no
    matching events in the window -- a genuinely healthy sign, not the same
    as "could not tell"."""
    ids = ",".join(str(i) for i in EVENT_MEANINGS)
    providers = ",".join(f"'{p}'" for p in _PROVIDERS)
    script = (
        "$ErrorActionPreference='Stop';"
        f"$f=@{{LogName='System';ProviderName=@({providers});Id=@({ids});"
        f"StartTime=(Get-Date).AddDays(-{int(days)})}};"
        f"try{{$e=Get-WinEvent -FilterHashtable $f -MaxEvents {int(max_events)}}}"
        "catch{if($_.FullyQualifiedErrorId -like '*NoMatchingEventsFound*'){'[]';exit 0}else{throw}};"
        "@($e|%{[pscustomobject]@{Id=$_.Id;Level=$_.LevelDisplayName;"
        "Time=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss');Message=$_.Message}})"
        "|ConvertTo-Json -Compress")
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("disk event read failed: %s", exc)
        return None
    if done.returncode != 0:
        logger.warning("disk event read refused: %s", (done.stderr or "").strip()[:200])
        return None
    try:
        data = json.loads(done.stdout.strip() or "[]")
    except ValueError:
        logger.warning("disk event history was not JSON")
        return None
    if isinstance(data, dict):
        data = [data]
    events: List[DiskEvent] = []
    for rec in data:
        event_id = rec.get("Id")
        message = (rec.get("Message") or "").strip()
        events.append(DiskEvent(
            event_id=event_id, level=rec.get("Level") or "", time=rec.get("Time") or "",
            disk=_extract_disk(message), message=message,
            meaning=EVENT_MEANINGS.get(event_id, "")))
    return events


@dataclass
class EventGroup:
    event_id: int
    disk: Optional[str]
    count: int
    latest: str
    meaning: str
    is_error: bool


def group_events(events: List[DiskEvent]) -> List[EventGroup]:
    """One row per (event id, disk) rather than one per raw event --
    confirmed live, a single recurring warning showed up 20+ times in a
    14-day window on this real machine, and a finding per occurrence would
    have buried everything else."""
    groups: Dict[tuple, EventGroup] = {}
    for e in events:
        key = (e.event_id, e.disk)
        existing = groups.get(key)
        if existing is None:
            groups[key] = EventGroup(e.event_id, e.disk, 1, e.time, e.meaning, e.is_error)
        else:
            existing.count += 1
            if e.time > existing.latest:
                existing.latest = e.time
    return sorted(groups.values(), key=lambda g: (not g.is_error, -g.count))
