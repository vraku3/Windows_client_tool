"""Boot-time trend history: is boot getting slower over time.

Pairs Microsoft-Windows-Kernel-General's own EventID 12 ("The operating
system started at system time...") with EventLog's own EventID 6005 ("The
Event log service was started") in the `System` log. 6005 fires once
user-mode services -- including the Event Log service itself -- are up, so
the gap between the two is a real, reproducible "time to a working desktop"
proxy, and both events are read entirely unelevated.

Measured on this machine (2026-09-30, across the last 10 boots): 16.5s,
26.8s, 42.8s, 38.7s, 32.0s, 32.3s, 32.4s, 32.0s, 33.2s, 32.5s -- noisy in the
older entries, settled to ~32s for the six most recent. The preceding
shutdown is classified the same way: 6006 ("...was stopped") means a clean
shutdown happened before this boot, 6008 means the previous session ended
without one.

CLAUDE.md's own "Sysadmin upgrades to the other tabs" section states
Microsoft-Windows-Diagnostics-Performance/Operational (events 100-103) is
"readable unelevated" -- re-checked live on this machine and it now refuses
unelevated both via PowerShell's `Get-WinEvent` ("Attempted to perform an
unauthorized operation") and `wevtutil` (exit 5, "Access is denied").
Whatever made that log readable when that note was written no longer holds
on this machine, so this reads the `System` log instead, which every other
part of this app (Event Viewer, Reliability, CBS) already treats as
unelevated-readable, and which was independently confirmed above.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"

_BOOT_XPATH = (
    "*[System[Provider[@Name='Microsoft-Windows-Kernel-General'] "
    "and EventID=12]]"
)
_SHUTDOWN_XPATH = "*[System[(EventID=6005 or EventID=6006 or EventID=6008)]]"

#: 6006 = "The Event log service was stopped" (clean). 6008 = "...previous
#: system shutdown ... was unexpected." Anything else in this set is 6005.
_CLEAN_SHUTDOWN_ID = 6006
_UNEXPECTED_SHUTDOWN_ID = 6008
_READY_ID = 6005


@dataclass(frozen=True)
class BootRecord:
    """One boot: when the kernel started, when the machine looked ready,
    and how the session before it ended."""

    boot_time: datetime
    ready_time: Optional[datetime]
    duration_seconds: Optional[float]
    prior_shutdown_time: Optional[datetime]
    #: True = clean (6006), False = unexpected (6008), None = no matching
    #: event found in the window fetched (log rotated past it, or this is
    #: the oldest boot the query reached).
    prior_shutdown_clean: Optional[bool]


@dataclass(frozen=True)
class BootHistoryResult:
    """`available=False` means the System-log query itself was refused --
    that is never collapsed into an empty `records` list, which would read
    as "this machine has no boot history" rather than "could not be read."
    """

    available: bool
    reason: Optional[str]
    records: List[BootRecord]


def _query_events(xpath: str, max_count: int) -> List[Tuple[datetime, int]]:
    """(timestamp_utc, event_id) pairs from the `System` log, newest first,
    matching `xpath`. Raises on refusal or a missing pywin32 -- the caller
    decides how to report that; it is never swallowed here."""
    import win32evtlog

    flags = win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection
    query = win32evtlog.EvtQuery("System", flags, xpath)
    out: List[Tuple[datetime, int]] = []
    while len(out) < max_count:
        batch = win32evtlog.EvtNext(query, min(20, max_count - len(out)))
        if not batch:
            break
        for ev in batch:
            xml_text = win32evtlog.EvtRender(ev, win32evtlog.EvtRenderEventXml)
            root = ET.fromstring(xml_text)
            system_el = root.find(f"{_NS}System")
            time_el = system_el.find(f"{_NS}TimeCreated")
            id_el = system_el.find(f"{_NS}EventID")
            ts_text = time_el.get("SystemTime", "")
            # SystemTime carries 7 fractional digits; %f only parses 6.
            ts = datetime.strptime(ts_text[:26], "%Y-%m-%dT%H:%M:%S.%f").replace(
                tzinfo=timezone.utc
            )
            out.append((ts, int(id_el.text)))
    return out


def _pair_boot(
    boot_time: datetime, shutdowns: List[Tuple[datetime, int]]
) -> BootRecord:
    ready_time = next(
        (t for t, eid in shutdowns if eid == _READY_ID and t >= boot_time), None
    )
    duration = (ready_time - boot_time).total_seconds() if ready_time else None

    prior = [
        (t, eid)
        for t, eid in shutdowns
        if eid in (_CLEAN_SHUTDOWN_ID, _UNEXPECTED_SHUTDOWN_ID) and t < boot_time
    ]
    if prior:
        prior_time, prior_id = prior[-1]
        prior_clean: Optional[bool] = prior_id == _CLEAN_SHUTDOWN_ID
    else:
        prior_time, prior_clean = None, None

    return BootRecord(
        boot_time=boot_time,
        ready_time=ready_time,
        duration_seconds=duration,
        prior_shutdown_time=prior_time,
        prior_shutdown_clean=prior_clean,
    )


def get_boot_history(max_boots: int = 15) -> BootHistoryResult:
    """The last `max_boots` boots, oldest first, each paired with how long
    it took to look ready and how the session before it ended."""
    try:
        boots = _query_events(_BOOT_XPATH, max_boots)
        # 3x headroom: ideally each boot pairs with one 6005 after it and
        # one 6006/6008 before it, so a window this much wider than the
        # boot count comfortably covers both ends for every boot fetched.
        shutdowns = _query_events(_SHUTDOWN_XPATH, max_boots * 3)
    except ImportError as exc:
        logger.error("pywin32 not available -- cannot read boot history: %s", exc)
        return BootHistoryResult(available=False, reason=str(exc), records=[])
    except Exception as exc:
        logger.warning("Boot history query refused: %s", exc)
        return BootHistoryResult(available=False, reason=str(exc), records=[])

    boots.sort(key=lambda pair: pair[0])
    shutdowns.sort(key=lambda pair: pair[0])

    records = [_pair_boot(boot_time, shutdowns) for boot_time, _ in boots]
    return BootHistoryResult(available=True, reason=None, records=records)


def trend_summary(records: List[BootRecord]) -> Optional[str]:
    """A one-line verdict comparing the most recent measured boots against
    the ones before them. `None` when there are fewer than 4 boots with a
    measured duration -- not enough to say anything honest about a trend.
    """
    measured = [r.duration_seconds for r in records if r.duration_seconds is not None]
    if len(measured) < 4:
        return None
    half = len(measured) // 2
    older_avg = sum(measured[:half]) / half
    recent = measured[half:]
    recent_avg = sum(recent) / len(recent)
    delta = recent_avg - older_avg
    if abs(delta) < 2.0:
        return f"Boot time steady: ~{recent_avg:.0f}s (was ~{older_avg:.0f}s)"
    if delta > 0:
        return f"Boot getting slower: ~{recent_avg:.0f}s, up from ~{older_avg:.0f}s"
    return f"Boot getting faster: ~{recent_avg:.0f}s, down from ~{older_avg:.0f}s"
