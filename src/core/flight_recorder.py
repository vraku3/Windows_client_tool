"""Unified Change Timeline ('Flight Recorder') engine.

Aggregates both internal tool operations and Windows system changes into
a single, chronological, searchable audit trail answering:
'What changed on this PC before the issue began?'

Sources aggregated:
1. WinClientTool timeline journal (timeline.jsonl: tweaks, debloat, game mode, updates).
2. Windows Update Client events (Event ID 19 = update installed, 20 = update failed).
3. PnP Driver Management events (Microsoft-Windows-UserPnp Event ID 20001 = driver installed).
4. Application installations/removals (MsiInstaller Event ID 1033 = install, 1034 = uninstall).
5. System state transitions (Eventlog 6005 = boot, 6006 = clean shut, 6008 = dirty shut, KP 41).
6. System Restore points (Microsoft-Windows-SystemRestore Event ID 8193 = created, 8194 = restored).

Qt-free, testable on any machine.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

from core.timeline import load_timeline
from core.types import LogEntry
from core.windows_utils import system_root

logger = logging.getLogger(__name__)


def _entry_from_internal_item(item: dict) -> LogEntry:
    """Convert an internal timeline.jsonl dictionary into a LogEntry."""
    ts_str = item.get("ts", "")
    try:
        ts = datetime.fromisoformat(ts_str).replace(tzinfo=None)
    except (ValueError, TypeError):
        ts = datetime.now()

    source = f"WinClientTool ({item.get('source', 'app')})"
    sev = item.get("severity", "info").capitalize()
    if sev not in ("Info", "Warning", "Error", "Critical"):
        sev = "Info"

    summary = item.get("summary", "")
    detail = item.get("detail", {})

    return LogEntry(
        timestamp=ts,
        source=source,
        level=sev,
        message=summary,
        raw={
            "category": "tool_action",
            "event": item.get("event", ""),
            "detail": detail,
            "internal": True,
        },
    )


def _query_system_events(days: int = 30) -> Tuple[List[LogEntry], List[str]]:
    """Query external Windows event log sources for machine changes."""
    from modules.event_viewer import event_query

    entries: List[LogEntry] = []
    notes: List[str] = []
    ms = days * 86400 * 1000

    # 1. System state & boot events (System log)
    xpath_boot = (
        "*[System[(EventID=6005 or EventID=6006 or EventID=6008) "
        f"and TimeCreated[timediff(@SystemTime) <= {ms}]]]"
    )
    res_boot = event_query.query_log("System", None, 100, xpath=xpath_boot)
    if res_boot.error:
        notes.append(f"Boot/shutdown events could not be queried: {res_boot.error}")
    else:
        for ev in res_boot.entries:
            eid = ev.raw.get("event_id", 0) if ev.raw else 0
            if eid == 6005:
                lvl = "Info"
                msg = "System Boot: Windows Event Log service started"
            elif eid == 6006:
                lvl = "Info"
                msg = "Clean Shutdown: Windows Event Log service stopped"
            elif eid == 6008:
                lvl = "Error"
                msg = f"Unexpected Shutdown: {ev.message or 'Previous shutdown was not clean'}"
            else:
                lvl = "Info"
                msg = ev.message
            entries.append(LogEntry(
                timestamp=ev.timestamp,
                source="Windows System",
                level=lvl,
                message=msg,
                raw={"category": "system_state", "event_id": eid, "data": ev.raw.get("data", {}) if ev.raw else {}},
            ))

    # 2. Windows Update Client events (System log)
    xpath_wu = (
        "*[System[Provider[@Name='Microsoft-Windows-WindowsUpdateClient'] "
        f"and (EventID=19 or EventID=20) and TimeCreated[timediff(@SystemTime) <= {ms}]]]"
    )
    res_wu = event_query.query_log("System", None, 100, xpath=xpath_wu)
    if res_wu.error:
        notes.append(f"Windows Update events could not be queried: {res_wu.error}")
    else:
        for ev in res_wu.entries:
            eid = ev.raw.get("event_id", 0) if ev.raw else 0
            lvl = "Info" if eid == 19 else "Error"
            act = "Installed" if eid == 19 else "Installation Failed"
            entries.append(LogEntry(
                timestamp=ev.timestamp,
                source="Windows Update",
                level=lvl,
                message=f"Windows Update {act}: {ev.message}",
                raw={"category": "update", "event_id": eid, "data": ev.raw.get("data", {}) if ev.raw else {}},
            ))

    # 3. PnP Driver Installation events (System log)
    xpath_pnp = (
        "*[System[Provider[@Name='Microsoft-Windows-UserPnp'] "
        f"and EventID=20001 and TimeCreated[timediff(@SystemTime) <= {ms}]]]"
    )
    res_pnp = event_query.query_log("System", None, 100, xpath=xpath_pnp)
    if not res_pnp.error:
        for ev in res_pnp.entries:
            entries.append(LogEntry(
                timestamp=ev.timestamp,
                source="Driver Manager (PnP)",
                level="Info",
                message=f"Driver Installed/Updated: {ev.message}",
                raw={"category": "driver", "event_id": 20001, "data": ev.raw.get("data", {}) if ev.raw else {}},
            ))

    # 4. MSI Installer Software changes (Application log)
    xpath_msi = (
        "*[System[Provider[@Name='MsiInstaller'] "
        f"and (EventID=1033 or EventID=1034) and TimeCreated[timediff(@SystemTime) <= {ms}]]]"
    )
    res_msi = event_query.query_log("Application", None, 100, xpath=xpath_msi)
    if not res_msi.error:
        for ev in res_msi.entries:
            eid = ev.raw.get("event_id", 0) if ev.raw else 0
            act = "Installed" if eid == 1033 else "Removed"
            entries.append(LogEntry(
                timestamp=ev.timestamp,
                source="Software Installer (MSI)",
                level="Info" if eid == 1033 else "Warning",
                message=f"Application {act}: {ev.message}",
                raw={"category": "software", "event_id": eid, "data": ev.raw.get("data", {}) if ev.raw else {}},
            ))

    # 5. System Restore Points (Application / System log)
    xpath_sr = (
        "*[System[Provider[@Name='Microsoft-Windows-SystemRestore'] "
        f"and (EventID=8193 or EventID=8194) and TimeCreated[timediff(@SystemTime) <= {ms}]]]"
    )
    res_sr = event_query.query_log("Application", None, 50, xpath=xpath_sr)
    if not res_sr.error:
        for ev in res_sr.entries:
            eid = ev.raw.get("event_id", 0) if ev.raw else 0
            act = "Restore Point Created" if eid == 8193 else "System Restored from Point"
            entries.append(LogEntry(
                timestamp=ev.timestamp,
                source="System Restore",
                level="Info",
                message=f"{act}: {ev.message}",
                raw={"category": "restore_point", "event_id": eid, "data": ev.raw.get("data", {}) if ev.raw else {}},
            ))

    return entries, notes


def read_flight_recorder_entries(
    app_data_dir: str,
    days: int = 30,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Tuple[List[LogEntry], List[str]]:
    """Assemble all internal and external machine changes into a unified timeline."""
    entries: List[LogEntry] = []
    notes: List[str] = []

    def step(pct):
        if progress_callback:
            progress_callback(pct)

    step(10)
    # 1. Load internal WinClientTool actions
    try:
        since_dt = datetime.now() - timedelta(days=days)
        internal_items = load_timeline(app_data_dir, limit=1000, since=since_dt)
        for itm in internal_items:
            entries.append(_entry_from_internal_item(itm))
    except Exception as exc:
        logger.warning("Could not load internal timeline: %s", exc)
        notes.append(f"Internal timeline journal could not be read: {exc}")

    step(40)
    # 2. Load system change events
    sys_entries, sys_notes = _query_system_events(days=days)
    entries.extend(sys_entries)
    notes.extend(sys_notes)
    step(85)

    # 3. Sort newest first
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    step(100)

    return entries, notes


def timeline_summary_text(entries: List[LogEntry], notes: List[str]) -> str:
    """Formatted summary for the timeline pane."""
    if not entries:
        base = ("No timeline events were found in the places that could be read." if notes
                else "No system change events or maintenance operations on record.")
    else:
        counts = {}
        for e in entries:
            src = e.source.split(" (", 1)[0]
            counts[src] = counts.get(src, 0) + 1
        breakdown = ", ".join(f"{src} ({cnt})" for src, cnt in sorted(counts.items()))
        base = f"{len(entries)} total change event(s) recorded: {breakdown}."

    if notes:
        base += "  " + " ".join(notes)
    return base

