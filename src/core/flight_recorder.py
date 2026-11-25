"""Unified Change Timeline ('Flight Recorder') engine.

Aggregates both internal tool operations and Windows system changes into
a single, chronological, searchable audit trail answering:
'What changed on this PC before the issue began?'

Sources aggregated:
1. WinClientTool timeline journal (timeline.jsonl: tweaks, debloat, game mode, updates).
2. Windows Update Client events (Event ID 19 = update installed, 20 = update failed).
3. PnP Driver Management events (Microsoft-Windows-UserPnp Event ID 20001 = driver installed).
4. Application installations/removals (MsiInstaller Event ID 1033 = install, 1034 = uninstall).
5. System state transitions (Kernel-General 12/13, Kernel-Power 41, Eventlog 6005/6006/6008).
6. System Restore points (Microsoft-Windows-SystemRestore Event ID 8193 = created, 8194 = restored).

Includes structured Reboot Session Blocks and Change Correlation:
- Groups events between boot and shutdown boundaries.
- Tracks session start, end, total uptime duration.
- Identifies clean shutdown vs dirty shutdown / power-loss / BSOD.
- Correlates all system changes to specific reboot sessions.

Qt-free, testable on any machine.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

from core.timeline import load_timeline
from core.types import LogEntry
from core.windows_utils import system_root

logger = logging.getLogger(__name__)


def format_uptime(duration: Optional[timedelta]) -> str:
    """Format a timedelta into a human-readable duration (e.g. '2d 4h 15m')."""
    if duration is None:
        return "Unknown"
    secs = int(duration.total_seconds())
    if secs < 0:
        return "Unknown"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, s = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if mins > 0 or days > 0 or hours > 0:
        parts.append(f"{mins}m")
    elif s > 0 or not parts:
        parts.append(f"{s}s")
    return " ".join(parts) if parts else "0m"


@dataclass
class SessionBlock:
    """A structured reboot session grouping boot/shutdown events and machine changes."""

    session_id: int  # 0 = Current/Latest, 1 = Previous, 2 = Earlier...
    start_time: datetime
    end_time: Optional[datetime] = None  # None for active/ongoing session
    clean_shutdown: Optional[bool] = None  # True = clean, False = dirty/crash, None = active/unknown
    shutdown_type: str = "Active"  # e.g. "Active", "Clean Shutdown", "Crash / BSOD (Kernel-Power 41)"
    is_crash: bool = False
    boot_events: List[LogEntry] = field(default_factory=list)
    shutdown_events: List[LogEntry] = field(default_factory=list)
    entries: List[LogEntry] = field(default_factory=list)

    @property
    def uptime(self) -> Optional[timedelta]:
        """Total uptime duration for this session."""
        if self.end_time and self.start_time:
            return max(timedelta(0), self.end_time - self.start_time)
        elif self.start_time:
            return max(timedelta(0), datetime.now() - self.start_time)
        return None

    @property
    def uptime_str(self) -> str:
        """Formatted human-readable uptime duration."""
        return format_uptime(self.uptime)

    @property
    def tweaks(self) -> List[LogEntry]:
        """All tweak apply or revert operations in this session."""
        return [e for e in self.entries if self._is_tweak(e)]

    @property
    def windows_updates(self) -> List[LogEntry]:
        """All Windows Update installations/failures in this session."""
        return [
            e for e in self.entries
            if (e.raw or {}).get("category") == "update"
            or "Windows Update" in e.source
            or "Windows Update" in e.message
        ]

    @property
    def driver_updates(self) -> List[LogEntry]:
        """All driver updates and PnP installations in this session."""
        return [
            e for e in self.entries
            if (e.raw or {}).get("category") == "driver"
            or "Driver" in e.source
            or "PnP" in e.source
        ]

    @property
    def software_installs(self) -> List[LogEntry]:
        """All application installations and removals in this session."""
        return [
            e for e in self.entries
            if (e.raw or {}).get("category") == "software"
            or "Software" in e.source
            or "MsiInstaller" in e.source
        ]

    @property
    def restore_points(self) -> List[LogEntry]:
        """All System Restore creations or restorations in this session."""
        return [
            e for e in self.entries
            if (e.raw or {}).get("category") == "restore_point"
            or "System Restore" in e.source
        ]

    @property
    def all_changes(self) -> List[LogEntry]:
        """All machine change events in this session (excluding raw boot/shutdown state events)."""
        return [
            e for e in self.entries
            if (e.raw or {}).get("category") != "system_state"
        ]

    @property
    def change_summary(self) -> Dict[str, int]:
        """Counts of each type of change recorded during this session."""
        return {
            "tweaks": len(self.tweaks),
            "windows_updates": len(self.windows_updates),
            "driver_updates": len(self.driver_updates),
            "software_installs": len(self.software_installs),
            "restore_points": len(self.restore_points),
            "total_changes": len(self.all_changes),
        }

    def _is_tweak(self, e: LogEntry) -> bool:
        raw = e.raw or {}
        cat = raw.get("category", "")
        evt = str(raw.get("event", "")).lower()
        msg = (e.message or "").lower()
        src = (e.source or "").lower()
        if cat in ("tweak", "tweaks"):
            return True
        if cat == "tool_action" and ("tweak" in evt or "tweak" in msg):
            return True
        if "tweak" in src:
            return True
        return False

    def to_dict(self) -> dict:
        """Structured dictionary representation."""
        return {
            "session_id": self.session_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "uptime_str": self.uptime_str,
            "clean_shutdown": self.clean_shutdown,
            "shutdown_type": self.shutdown_type,
            "is_crash": self.is_crash,
            "change_summary": self.change_summary,
            "total_entries": len(self.entries),
        }


def _is_boot_event(entry: LogEntry) -> bool:
    raw = entry.raw or {}
    eid = raw.get("event_id")
    if eid in (12, 6005):
        return True
    evt_type = raw.get("event_type") or raw.get("event")
    if evt_type == "boot":
        return True
    msg = entry.message or ""
    return "System Boot:" in msg or "Operating system started" in msg


def _is_clean_shutdown_event(entry: LogEntry) -> bool:
    raw = entry.raw or {}
    eid = raw.get("event_id")
    if eid in (13, 6006):
        return True
    evt_type = raw.get("event_type") or raw.get("event")
    if evt_type in ("shutdown_clean", "clean_shutdown"):
        return True
    msg = entry.message or ""
    return "Clean Shutdown:" in msg or "Operating system is shutting down" in msg


def _is_dirty_shutdown_event(entry: LogEntry) -> bool:
    raw = entry.raw or {}
    eid = raw.get("event_id")
    if eid in (41, 6008):
        return True
    evt_type = raw.get("event_type") or raw.get("event")
    if evt_type in ("dirty_shutdown", "unexpected_shutdown", "crash", "bsod"):
        return True
    msg = entry.message or ""
    return (
        "Unexpected Shutdown" in msg
        or "Dirty Shutdown" in msg
        or "System Crash" in msg
        or "Kernel-Power 41" in msg
    )


def correlate_sessions(entries: List[LogEntry]) -> List[SessionBlock]:
    """Correlate boot and shutdown events into structured SessionBlocks with associated changes.

    Analyzes:
    - Kernel-General 12 & Eventlog 6005 (Boot)
    - Kernel-General 13 & Eventlog 6006 (Clean Shutdown)
    - Kernel-Power 41 & Eventlog 6008 (Dirty Shutdown / Crash / BSOD)

    Returns a list of `SessionBlock`s sorted newest first (Session 0 = Current).
    Every entry in `entries` is annotated with its session metadata in `entry.raw`.
    """
    if not entries:
        return []

    # Sort chronological for correlation
    sorted_entries = sorted(entries, key=lambda e: e.timestamp)

    # 1. Detect and cluster boot events (within 300s window)
    boot_events = [e for e in sorted_entries if _is_boot_event(e)]
    boot_clusters: List[List[LogEntry]] = []
    for be in boot_events:
        if not boot_clusters or (be.timestamp - boot_clusters[-1][-1].timestamp).total_seconds() > 300:
            boot_clusters.append([be])
        else:
            boot_clusters[-1].append(be)

    # 2. Detect clean shutdown events (within 300s window)
    clean_events = [e for e in sorted_entries if _is_clean_shutdown_event(e)]
    clean_clusters: List[List[LogEntry]] = []
    for ce in clean_events:
        if not clean_clusters or (ce.timestamp - clean_clusters[-1][-1].timestamp).total_seconds() > 300:
            clean_clusters.append([ce])
        else:
            clean_clusters[-1].append(ce)

    # 3. Detect dirty shutdown / crash events
    dirty_events = [e for e in sorted_entries if _is_dirty_shutdown_event(e)]

    # Edge case: No boot events found in queried entries
    if not boot_clusters:
        clean_shut = None
        is_cr = False
        shut_type = "Active"
        end_t = None
        shut_events: List[LogEntry] = []

        if dirty_events:
            clean_shut = False
            is_cr = True
            shut_type = "Dirty Shutdown / System Crash"
            end_t = dirty_events[-1].timestamp
            shut_events = list(dirty_events)
        elif clean_clusters:
            clean_shut = True
            is_cr = False
            shut_type = "Clean Shutdown"
            end_t = clean_clusters[-1][-1].timestamp
            shut_events = list(clean_clusters[-1])

        session = SessionBlock(
            session_id=0,
            start_time=sorted_entries[0].timestamp,
            end_time=end_t,
            clean_shutdown=clean_shut,
            shutdown_type=shut_type,
            is_crash=is_cr,
            shutdown_events=shut_events,
            entries=list(reversed(sorted_entries)),
        )
        _annotate_entries([session])
        return [session]

    raw_sessions: List[SessionBlock] = []
    num_clusters = len(boot_clusters)
    claimed_dirty_ids: Set[int] = set()

    for i in range(num_clusters):
        b_group = boot_clusters[i]
        next_b_group = boot_clusters[i + 1] if i + 1 < num_clusters else None
        sess = _build_session_for_cluster(i, b_group, next_b_group, clean_clusters, dirty_events, claimed_dirty_ids, sorted_entries)
        raw_sessions.append(sess)

    # Reverse to make Session 0 = Current / Latest, Session 1 = Previous, etc.
    sessions: List[SessionBlock] = list(reversed(raw_sessions))
    for idx, s in enumerate(sessions):
        s.session_id = idx
        # Sort session entries newest first
        s.entries.sort(key=lambda e: e.timestamp, reverse=True)

    _annotate_entries(sessions)
    return sessions


def _build_session_for_cluster(
    i: int,
    b_group: List[LogEntry],
    next_b_group: Optional[List[LogEntry]],
    clean_clusters: List[List[LogEntry]],
    dirty_events: List[LogEntry],
    claimed_dirty_ids: Set[int],
    sorted_entries: List[LogEntry],
) -> SessionBlock:
    start_t = min(e.timestamp for e in b_group)
    next_boot_t = min(e.timestamp for e in next_b_group) if next_b_group else None

    # Look for clean shutdown between this boot and next boot
    clean_for_session = [
        c for c in clean_clusters
        if start_t <= c[0].timestamp and (next_boot_t is None or c[-1].timestamp < next_boot_t)
    ]

    if clean_for_session:
        clean_shut = True
        is_cr = False
        shut_type = "Clean Shutdown"
        end_t = clean_for_session[-1][-1].timestamp
        shut_events = list(clean_for_session[-1])
    elif next_boot_t is not None:
        clean_shut = False
        is_cr = True
        dirty_for_session = [
            e for e in dirty_events
            if start_t <= e.timestamp <= (next_boot_t + timedelta(minutes=10))
        ]
        shut_events = list(dirty_for_session)
        for de in dirty_for_session:
            claimed_dirty_ids.add(id(de))

        if dirty_for_session:
            is_bsod = any(
                (e.raw or {}).get("event_type") == "bsod"
                or "BSOD" in (e.message or "")
                or ((e.raw or {}).get("data", {}).get("BugcheckCode") not in (None, "0", 0, ""))
                for e in dirty_for_session
            )
            has_kp41 = any((e.raw or {}).get("event_id") == 41 for e in dirty_for_session)
            has_6008 = any((e.raw or {}).get("event_id") == 6008 for e in dirty_for_session)

            if is_bsod:
                bcc = ""
                for e in dirty_for_session:
                    code = (e.raw or {}).get("data", {}).get("BugcheckCode")
                    if code and code != "0":
                        bcc = f" (Bugcheck {code})"
                        break
                shut_type = f"System Crash / BSOD (Kernel-Power 41){bcc}"
            elif has_kp41:
                shut_type = "Dirty Shutdown / Power Loss (Kernel-Power 41)"
            elif has_6008:
                shut_type = "Unexpected Shutdown (Event 6008)"
            else:
                shut_type = "Dirty Shutdown / System Crash"

            earliest_dirty = min(e.timestamp for e in dirty_for_session)
            end_t = earliest_dirty if earliest_dirty < next_boot_t else next_boot_t
        else:
            shut_type = "Abrupt Termination / Power Loss (Unrecorded)"
            end_t = next_boot_t
    else:
        recent_dirty = [
            e for e in dirty_events
            if id(e) not in claimed_dirty_ids
            and e.timestamp > (start_t + timedelta(minutes=10))
        ]
        if recent_dirty:
            clean_shut = False
            is_cr = True
            shut_events = list(recent_dirty)
            shut_type = "System Crash / Abnormal State"
            end_t = recent_dirty[-1].timestamp
        else:
            clean_shut = None
            is_cr = False
            shut_events = []
            shut_type = "Active"
            end_t = None

    lower_bound = datetime.min if i == 0 else start_t
    upper_bound = next_boot_t if next_boot_t is not None else datetime.max

    sess_entries = [
        e for e in sorted_entries
        if lower_bound <= e.timestamp < upper_bound
        and id(e) not in claimed_dirty_ids
    ]

    for se in shut_events:
        if se not in sess_entries:
            sess_entries.append(se)

    return SessionBlock(
        session_id=0,
        start_time=start_t if i > 0 else min(start_t, sorted_entries[0].timestamp),
        end_time=end_t,
        clean_shutdown=clean_shut,
        shutdown_type=shut_type,
        is_crash=is_cr,
        boot_events=list(b_group),
        shutdown_events=shut_events,
        entries=sess_entries,
    )
    return sessions


def _annotate_entries(sessions: List[SessionBlock]) -> None:
    """Attach session metadata to entry.raw for all correlated entries."""
    for s in sessions:
        for e in s.entries:
            if e.raw is None:
                e.raw = {}
            e.raw["session_id"] = s.session_id
            e.raw["session_clean_shutdown"] = s.clean_shutdown
            e.raw["session_is_crash"] = s.is_crash
            e.raw["session_shutdown_type"] = s.shutdown_type
            e.raw["session_uptime_str"] = s.uptime_str
            e.raw["session_start"] = s.start_time
            e.raw["session_end"] = s.end_time


def filter_entries_by_session(
    entries: List[LogEntry],
    filter_mode: Union[str, int],
    sessions: Optional[List[SessionBlock]] = None,
) -> List[LogEntry]:
    """Filter entries according to the selected session mode.

    Modes supported:
    - "All Sessions": returns all entries.
    - "Current Session": returns entries belonging to Session 0 (latest/active).
    - "Previous Session": returns entries belonging to Session 1.
    - "Session before Crash": returns entries from the most recent session that ended with a crash.
    - Integer or "Session X": returns entries for that session id.
    """
    if sessions is None:
        sessions = correlate_sessions(entries)

    if not sessions:
        return list(entries)

    mode_str = str(filter_mode).strip().lower()

    if mode_str in ("all", "all sessions", ""):
        return list(entries)

    if mode_str in ("current", "current session"):
        return list(sessions[0].entries)

    if mode_str in ("previous", "previous session"):
        return list(sessions[1].entries) if len(sessions) > 1 else []

    if mode_str in ("session before crash", "crash", "crashed session", "before crash"):
        for s in sessions:
            if s.is_crash:
                return list(s.entries)
        return []

    # Check for direct session id (e.g. 0, 1, or "Session 2")
    m = re.search(r"\b(\d+)\b", str(filter_mode))
    if m:
        target_id = int(m.group(1))
        for s in sessions:
            if s.session_id == target_id:
                return list(s.entries)

    return list(entries)


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
    # Correlates Kernel-General 12/13, Kernel-Power 41, Eventlog 6005/6006/6008
    xpath_boot = (
        "*[System[(EventID=6005 or EventID=6006 or EventID=6008 "
        "or (Provider[@Name='Microsoft-Windows-Kernel-General'] and (EventID=12 or EventID=13)) "
        "or (Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=41)) "
        f"and TimeCreated[timediff(@SystemTime) <= {ms}]]]"
    )
    res_boot = event_query.query_log("System", None, 150, xpath=xpath_boot)
    if res_boot.error:
        notes.append(f"Boot/shutdown events could not be queried: {res_boot.error}")
    else:
        for ev in res_boot.entries:
            eid = ev.raw.get("event_id", 0) if ev.raw else 0
            prov = (ev.raw.get("provider", "") if ev.raw else "") or ev.source
            data = ev.raw.get("data", {}) if ev.raw else {}
            if eid == 6005:
                lvl = "Info"
                msg = "System Boot: Windows Event Log service started (6005)"
                evt_type = "boot"
            elif eid == 6006:
                lvl = "Info"
                msg = "Clean Shutdown: Windows Event Log service stopped (6006)"
                evt_type = "shutdown_clean"
            elif eid == 6008:
                lvl = "Error"
                msg = f"Unexpected Shutdown: {ev.message or 'Previous shutdown was not clean (Event 6008)'}"
                evt_type = "unexpected_shutdown"
            elif eid == 12:
                lvl = "Info"
                msg = "System Boot: Operating system started (Kernel-General 12)"
                evt_type = "boot"
            elif eid == 13:
                lvl = "Info"
                msg = "Clean Shutdown: Operating system shutting down (Kernel-General 13)"
                evt_type = "shutdown_clean"
            elif eid == 41:
                lvl = "Critical"
                bcc = data.get("BugcheckCode", "0")
                if bcc and bcc != "0":
                    msg = f"System Crash (BSOD): Kernel-Power 41 (Bugcheck {bcc})"
                    evt_type = "bsod"
                else:
                    msg = "Dirty Shutdown / Power Loss: System rebooted without cleanly shutting down (Kernel-Power 41)"
                    evt_type = "dirty_shutdown"
            else:
                lvl = "Info"
                msg = ev.message
                evt_type = "system_state"

            entries.append(LogEntry(
                timestamp=ev.timestamp,
                source="Windows System",
                level=lvl,
                message=msg,
                raw={
                    "category": "system_state",
                    "event_type": evt_type,
                    "event_id": eid,
                    "provider": prov,
                    "data": data,
                },
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
    step(80)

    # 3. Sort newest first
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    step(90)

    # 4. Correlate reboot sessions
    try:
        correlate_sessions(entries)
    except Exception as exc:
        logger.warning("Could not correlate reboot sessions: %s", exc)

    step(100)
    return entries, notes


def timeline_summary_text(
    entries: List[LogEntry],
    notes: List[str],
    sessions: Optional[List[SessionBlock]] = None,
) -> str:
    """Formatted summary for the timeline pane."""
    if sessions is None and entries:
        try:
            sessions = correlate_sessions(entries)
        except Exception:
            sessions = []

    if not entries:
        base = ("No timeline events were found in the places that could be read." if notes
                else "No system change events or maintenance operations on record.")
    else:
        counts = {}
        for e in entries:
            src = e.source.split(" (", 1)[0]
            counts[src] = counts.get(src, 0) + 1
        breakdown = ", ".join(f"{src} ({cnt})" for src, cnt in sorted(counts.items()))

        sess_info = ""
        if sessions:
            crashes = sum(1 for s in sessions if s.is_crash)
            curr = sessions[0]
            curr_str = f"Current uptime: {curr.uptime_str}" if curr.clean_shutdown is None else f"Latest session: {curr.uptime_str}"
            crash_str = f", ⚠️ {crashes} crash(es) detected" if crashes else ""
            sess_info = f"{len(sessions)} session(s) tracked ({curr_str}{crash_str}). "

        base = f"{sess_info}{len(entries)} total change event(s) recorded: {breakdown}."

    if notes:
        base += "  " + " ".join(notes)
    return base
