"""Reliability log and metric reader with deep analytics helpers.

Reads Win32_ReliabilityRecords and Win32_ReliabilityStabilityMetrics from WMI,
and provides analytics for trend evaluation, failure bursts, and MTBF.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from core.types import LogEntry
from modules.reliability.reliability_analysis import (
    Crasher,
    Dip,
    FailureCluster,
    Metric,
    StabilityTrend,
    calculate_mtbf,
    calculate_stability_trend,
    classify,
    detect_failure_clusters,
    find_dips,
    format_mtbf,
    is_failure_event,
    parse_wmi_time,
    top_crashers,
)

logger = logging.getLogger(__name__)


def _connect():
    """A WMI connection, or raises: a failed read must reach the error banner."""
    try:
        import ctypes
        ctypes.windll.ole32.CoInitialize(None)
    except Exception as exc:
        logger.debug("CoInitialize in reliability_reader: %s", exc)
    import wmi
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

    Raises when WMI cannot be read: an empty list means 'no records', never
    'could not look'.
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


def read_reliability(
    max_records: int = 1000,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Tuple[List[LogEntry], Optional[List[Metric]], List[str]]:
    """(records, stability metrics, problems). A metrics failure is reported, not hidden."""
    try:
        conn = _connect()
    except Exception as exc:
        logger.warning("Could not connect to WMI for reliability records: %s", exc)
        return [], None, [f"Could not connect to WMI: {exc}"]

    entries = read_reliability_records(max_records, progress_callback, conn=conn)
    problems: List[str] = []
    try:
        metrics = read_stability_metrics(conn)
    except Exception as exc:
        logger.warning("Stability index history could not be read: %s", exc)
        metrics = None
        problems.append(f"Stability index history could not be read ({exc}).")
    return entries, metrics, problems


def analyze_stability_overview(
    entries: List[LogEntry],
    metrics: Optional[List[Metric]] = None,
    days: int = 30,
) -> Dict[str, object]:
    """Calculate comprehensive stability analytics: trend, MTBF, bursts, and crashers."""
    metrics_list = metrics or []
    trend = calculate_stability_trend(metrics_list, days=days)
    dips = find_dips(metrics_list, entries)
    clusters = detect_failure_clusters(entries)
    mtbf = calculate_mtbf(entries, days=days)
    crashers = top_crashers(entries, limit=5)

    return {
        "trend": trend,
        "dips": dips,
        "clusters": clusters,
        "mtbf_hours": mtbf,
        "mtbf_formatted": format_mtbf(mtbf),
        "crashers": crashers,
        "total_failures": sum(1 for e in entries if is_failure_event(e)),
    }
