import logging
from datetime import datetime, timedelta
from typing import List, Optional, Callable

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


def event_message(event, log_name: str, event_id: int) -> str:
    """Text for one event, never blank.

    The insert strings are what the event carries; an event whose inserts are
    all empty (`['']`, which is a non-empty LIST) produced a blank Message
    cell, and roughly a third of a real System log looked like that. Those are
    formatted by Windows from the provider's own message table; if that is not
    possible, the event ID is at least something to search on."""
    inserts = [str(part) for part in (event.StringInserts or []) if str(part).strip()]
    if inserts:
        return " | ".join(inserts)
    try:
        import win32evtlogutil
        formatted = (win32evtlogutil.FormatMessage(event, log_name) or "").strip()
    except Exception:
        logger.debug("No message table for event %s in %s", event_id, log_name)
        formatted = ""
    return " ".join(formatted.split()) or f"Event ID {event_id}"


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
                message = event_message(event, log_name, event_id)

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


def read_all_logs(
    hours_back: int = 24,
    max_events_per_log: int = 2000,
    include_security: bool = False,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> List[LogEntry]:
    """Read from all standard event logs and merge results."""
    logs = list(DEFAULT_LOGS)
    if include_security:
        logs.extend(ADMIN_LOGS)

    all_entries = []
    for i, log_name in enumerate(logs):
        def log_progress(p, _i=i):
            if progress_callback:
                overall = int(((_i + p / 100) / len(logs)) * 100)
                progress_callback(min(overall, 99))

        entries = read_event_log(log_name, hours_back, max_events_per_log, log_progress)
        all_entries.extend(entries)

    all_entries.sort(key=lambda e: e.timestamp, reverse=True)
    if progress_callback:
        progress_callback(100)
    return all_entries
