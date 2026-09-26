import logging
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from core.types import LogEntry
from modules.reliability.reliability_analysis import Metric, classify, parse_wmi_time

logger = logging.getLogger(__name__)


def _connect():
    """A WMI connection, or raises: a failed read must reach the error banner."""
    import wmi
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        logger.debug("CoInitialize was not needed or not available", exc_info=True)
    return wmi.WMI(namespace=r"root\cimv2")


def read_stability_metrics(conn=None) -> List[Metric]:
    """Hourly stability index history (newest last). Raises if WMI cannot be read."""
    conn = conn or _connect()
    out = []
    for row in conn.query("SELECT * FROM Win32_ReliabilityStabilityMetrics"):
        start = parse_wmi_time(getattr(row, "StartMeasurementDate", ""))
        index = getattr(row, "SystemStabilityIndex", None)
        if start is not None and index is not None:
            out.append(Metric(start, float(index)))
    out.sort(key=lambda m: m.start)
    return out


def read_reliability_records(
    max_records: int = 1000,
    progress_callback: Optional[Callable[[int], None]] = None,
    conn=None,
) -> List[LogEntry]:
    """Read reliability records from WMI, newest first.

    Raises when WMI cannot be read: an empty list means "no records", never
    "could not look".
    """
    conn = conn or _connect()
    records = conn.query("SELECT * FROM Win32_ReliabilityRecords")
    total = max(min(len(records), max_records), 1)
    entries = []
    for i, record in enumerate(records[:max_records]):
        ts = getattr(record, "TimeGenerated", "")
        event_time = parse_wmi_time(ts)
        if event_time is None:
            logger.debug("Reliability record with unreadable time %r skipped", ts)
            continue
        source_name = getattr(record, "SourceName", "") or "Unknown"
        event_id = getattr(record, "EventIdentifier", 0) or 0
        message = getattr(record, "Message", "") or ""
        product_name = getattr(record, "ProductName", "") or ""
        display_message = message or product_name or f"Event {event_id} from {source_name}"
        entries.append(LogEntry(
            timestamp=event_time,
            source=source_name,
            level=classify(source_name, event_id, message),
            message=display_message,
            raw={
                "event_id": int(event_id),
                "product_name": product_name,
                "computer_name": getattr(record, "ComputerName", ""),
            },
        ))
        if progress_callback and i % 50 == 0:
            progress_callback(min(int((i / total) * 100), 99))

    entries.sort(key=lambda e: e.timestamp, reverse=True)
    if progress_callback:
        progress_callback(100)
    return entries


def read_reliability(max_records: int = 1000,
                     progress_callback: Optional[Callable[[int], None]] = None
                     ) -> Tuple[List[LogEntry], List[Metric], List[str]]:
    """(records, stability metrics, problems). A metrics failure is reported, not hidden."""
    conn = _connect()
    entries = read_reliability_records(max_records, progress_callback, conn=conn)
    problems: List[str] = []
    try:
        metrics = read_stability_metrics(conn)
    except Exception as exc:
        logger.warning("Stability index history could not be read: %s", exc)
        metrics = None
        problems.append(f"Stability index history could not be read ({exc}).")
    return entries, metrics, problems
