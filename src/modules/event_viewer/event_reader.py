import logging
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

from core.types import LogEntry

logger = logging.getLogger(__name__)

EVENT_TYPE_MAP = {
    1: "Error",
    2: "Warning",
    4: "Info",
    8: "Info",   # Audit success
    16: "Info",  # Audit failure
}

DEFAULT_LOGS = ["System", "Application"]
ADMIN_LOGS = ["Security"]

#: Informational events kept per log alongside ALL the errors and warnings.
INFO_PER_LOG = 2500


def read_event_log(
    log_name: str = "System",
    hours_back: int = 24,
    max_events: int = 5000,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> List[LogEntry]:
    """Read Windows Event Log entries from the specified log."""
    try:
        import win32evtlog
    except ImportError:
        logger.error("pywin32 not installed — cannot read Event Logs")
        return []

    entries = []
    cutoff = datetime.now() - timedelta(hours=hours_back)

    try:
        handle = win32evtlog.OpenEventLog(None, log_name)
        flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ

        total_read = 0
        while total_read < max_events:
            events = win32evtlog.ReadEventLog(handle, flags, 0)
            if not events:
                break
            for event in events:
                if total_read >= max_events:
                    break
                ts = event.TimeGenerated
                event_time = datetime(ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second)
                if event_time < cutoff:
                    # Since we read backwards, once we pass cutoff we're done
                    total_read = max_events  # force exit
                    break

                level = EVENT_TYPE_MAP.get(event.EventType, "Info")
                event_id = event.EventID & 0xFFFF
                message_parts = event.StringInserts or []
                message = " | ".join(str(s) for s in message_parts) if message_parts else f"Event ID {event_id}"

                entries.append(LogEntry(
                    timestamp=event_time,
                    source=event.SourceName or log_name,
                    level=level,
                    message=message,
                    raw={
                        "event_id": event_id,
                        "log_name": log_name,
                        "category": event.EventCategory,
                        "computer": event.ComputerName,
                        "record_number": event.RecordNumber,
                    },
                ))
                total_read += 1
                if progress_callback and total_read % 100 == 0:
                    progress_callback(min(int((total_read / max_events) * 100), 99))

        win32evtlog.CloseEventLog(handle)
    except Exception as e:
        logger.error("Failed to read %s log: %s", log_name, e)

    if progress_callback:
        progress_callback(100)

    return entries


#: The Security log is audit noise at level "Information"; these are the IDs a
#: troubleshooter reads it for: failed logon, account lockout, log cleared,
#: audit policy changed.
SECURITY_IDS = (4625, 4740, 1102, 4719)


def _read_security(entries: List[LogEntry], notes: List[str], hours_back: int, cap: int) -> None:
    from modules.event_viewer import event_query

    ids = " or ".join(f"EventID={i}" for i in SECURITY_IDS)
    clauses = [f"({ids})"]
    if hours_back:
        clauses.append(f"TimeCreated[timediff(@SystemTime) <= {int(hours_back) * 3600 * 1000}]")
    result = event_query.query_log("Security", None, cap, xpath=f"*[System[{' and '.join(clauses)}]]")
    if result.error is None:
        for e in result.entries:
            e.level = "Warning"  # audit failures/lockouts are logged at Information
        entries.extend(result.entries)
    elif result.access_denied:
        notes.append("Security: access denied - run elevated to read failed logons (4625) and lockouts.")
    else:
        notes.append(f"Security: could not be read ({result.error}).")


def read_logs(
    hours_back: int = 24,
    max_events_per_log: int = 5000,
    include_security: bool = False,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Tuple[List[LogEntry], List[str]]:
    """Read the standard logs; returns (entries newest first, notes).

    `notes` says everything that was NOT the whole truth: a channel that was
    refused (with the reason), one that fell back to the legacy reader (whose
    messages are the raw inserts), one cut off at the cap. A refused channel is
    never silently the same as an empty one.
    """
    from modules.event_viewer import event_query

    logs = list(DEFAULT_LOGS)
    if include_security:
        logs.extend(ADMIN_LOGS)
    entries: List[LogEntry] = []
    notes: List[str] = []
    for i, log_name in enumerate(logs):
        if log_name == "Security":
            _read_security(entries, notes, hours_back, max_events_per_log)
            if progress_callback:
                progress_callback(min(int((i + 1) / len(logs) * 100), 99))
            continue
        # Problems and chatter are read separately: a busy log's informational
        # events would otherwise fill the cap and push the errors out of range.
        result = event_query.query_log(log_name, hours_back, max_events_per_log,
                                       levels=event_query.PROBLEM_LEVELS)
        if result.error is None:
            entries.extend(result.entries)
            if result.truncated:
                notes.append(f"{log_name}: only the newest {max_events_per_log} errors/warnings are shown; "
                             f"older ones in this range were not loaded.")
            info = event_query.query_log(log_name, hours_back, INFO_PER_LOG,
                                         levels=event_query.INFO_LEVELS)
            if info.error is None:
                entries.extend(info.entries)
                if info.truncated:
                    notes.append(f"{log_name}: informational events are capped at the newest {INFO_PER_LOG}.")
            else:
                notes.append(f"{log_name}: informational events could not be read ({info.error}).")
        elif result.access_denied:
            notes.append(f"{log_name}: access denied - run elevated to read it.")
        else:
            logger.warning("wevtutil failed for %s (%s); using the legacy reader", log_name, result.error)
            notes.append(f"{log_name}: rendered messages unavailable ({result.error}); "
                         f"showing raw event data instead.")
            entries.extend(read_event_log(log_name, hours_back, max_events_per_log))
        if progress_callback:
            progress_callback(min(int((i + 1) / len(logs) * 100), 99))
    if not include_security:
        notes.append("Security log not read (needs elevation), so failed logons are not shown.")
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    if progress_callback:
        progress_callback(100)
    return entries, notes


def read_all_logs(
    hours_back: int = 24,
    max_events_per_log: int = 5000,
    include_security: bool = False,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> List[LogEntry]:
    """`read_logs` without the notes (kept for callers that only want the events)."""
    entries, _notes = read_logs(hours_back, max_events_per_log, include_security, progress_callback)
    return entries
