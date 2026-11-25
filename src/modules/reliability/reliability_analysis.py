"""Reliability records and the stability index, made to answer "what dragged it down?".

Qt-free. `Win32_ReliabilityStabilityMetrics` gives an hourly 1-10 index;
`Win32_ReliabilityRecords` gives the events behind it. Windows' own Reliability
Monitor shows exactly this pairing; here the pairing is computed, with 30-day trend
analysis, failure burst clustering, and Mean Time Between Failures (MTBF).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from core.types import LogEntry

#: (source, event id) pairs Windows itself counts as failures.
_ERROR_EVENTS = {
    ("application error", 1000), ("application hang", 1002), (".net runtime", 1026),
    ("eventlog", 6008), ("microsoft-windows-kernel-power", 41),
    ("microsoft-windows-wer-systemerrorreporting", 1001), ("bugcheck", 1001),
}
_WARNING_EVENTS = {
    ("microsoft-windows-windowsupdateclient", 20),
    ("msiinstaller", 11708), ("msiinstaller", 11707),
}
#: Failure wording, with the routine "...success or error status: 0." excluded.
_FAILED = re.compile(r"\b(failed|failure|could not be installed|unsuccessful)\b", re.IGNORECASE)


def classify(source: str, event_id: int, message: str) -> str:
    """Error / Warning / Info for a reliability record.

    Based on WHAT HAPPENED, not on whether the word "error" appears: a
    successful MSI install says "Installation success or error status: 0" and
    the old keyword test called every one of them an Error.
    """
    key = ((source or "").lower(), int(event_id or 0))
    if key in _ERROR_EVENTS:
        return "Error"
    if key in _WARNING_EVENTS:
        return "Warning"
    if _FAILED.search(message or ""):
        return "Warning"
    return "Info"


def is_failure_event(entry: LogEntry) -> bool:
    """True if entry represents an error, crash, hardware failure or unexpected shutdown."""
    if entry.level == "Error":
        return True
    raw = entry.raw if isinstance(entry.raw, dict) else {}
    event_id = raw.get("event_id", 0)
    return classify(entry.source, event_id, entry.message) == "Error"


def failure_category(entry: LogEntry) -> str:
    """Categorize a failure into 'Application Crash', 'Windows Failure', 'Hardware Error', or 'Other'."""
    src = (entry.source or "").lower()
    msg = (entry.message or "").lower()
    raw = entry.raw if isinstance(entry.raw, dict) else {}
    eid = raw.get("event_id", 0)

    # Hardware errors
    if any(k in src for k in ("whea", "disk", "nvme", "atapi", "storahci", "volmgr", "hardware")) or "hardware error" in msg:
        return "Hardware Error"

    # Windows OS / Kernel / Subsystem failures
    if (
        src in ("microsoft-windows-kernel-power", "eventlog", "microsoft-windows-wer-systemerrorreporting", "bugcheck", "wininit", "lsass")
        or eid in (41, 6008, 1001)
        or "unexpected shutdown" in msg
        or "bugcheck" in msg
        or "blue screen" in msg
    ):
        return "Windows Failure"

    # Application crashes / hangs
    if (
        src in ("application error", "application hang", ".net runtime")
        or eid in (1000, 1002, 1026)
        or "faulting application" in msg
    ):
        return "Application Crash"

    return "Other"


@dataclass(frozen=True)
class Metric:
    start: datetime
    index: float


@dataclass(frozen=True)
class Dip:
    when: datetime
    before: float
    after: float
    events: Tuple[LogEntry, ...]

    @property
    def drop(self) -> float:
        return self.before - self.after


@dataclass(frozen=True)
class StabilityTrend:
    """Trend metrics for the System Stability Index over a time window."""
    current: Optional[float]
    start_score: Optional[float]
    delta: Optional[float]
    direction: str                     # "improving", "declining", "stable", or "unknown"
    avg_score: Optional[float]
    min_score: Optional[float]
    max_score: Optional[float]
    history: Tuple[float, ...]
    days: int
    description: str


@dataclass(frozen=True)
class FailureCluster:
    """A sudden burst or concentration of failure events."""
    start_time: datetime
    end_time: datetime
    category: str                      # "Application Crash", "Windows Failure", "Hardware Error", or "Mixed"
    count: int
    primary_source: str                # Most frequent source/app in the cluster
    events: Tuple[LogEntry, ...]
    description: str

    @property
    def duration_minutes(self) -> float:
        return max(0.0, (self.end_time - self.start_time).total_seconds() / 60.0)


def parse_wmi_time(text: str) -> Optional[datetime]:
    """`20260926030000.000000-000` -> datetime, or None when it does not parse."""
    try:
        return datetime.strptime((text or "")[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None


def find_dips(metrics: List[Metric], entries: List[LogEntry], min_drop: float = 0.3) -> List[Dip]:
    """Hours where the index fell by at least `min_drop`, with the failures in that hour.

    The index is a weighted moving average that falls when a failure lands and
    recovers slowly, so a fall IS the marker of a failure: the failing events
    are the ones stamped inside (or just before) that measurement hour.
    """
    ordered = sorted(metrics, key=lambda m: m.start)
    dips: List[Dip] = []
    for prev, cur in zip(ordered, ordered[1:]):
        if prev.index - cur.index < min_drop:
            continue
        lo, hi = cur.start - timedelta(hours=1), cur.start + timedelta(hours=1)
        behind = tuple(e for e in entries if lo <= e.timestamp < hi and e.level in ("Error", "Warning"))
        dips.append(Dip(cur.start, prev.index, cur.index, behind))
    return dips


def latest_index(metrics: List[Metric]) -> Optional[Metric]:
    return max(metrics, key=lambda m: m.start) if metrics else None


def index_at(metrics: List[Metric], when: datetime) -> Optional[Metric]:
    """The measurement covering `when` (hourly buckets), or None if outside the series."""
    for m in metrics:
        if m.start <= when < m.start + timedelta(hours=1):
            return m
    return None


def calculate_stability_trend(metrics: List[Metric], days: int = 30) -> StabilityTrend:
    """Calculate stability index trend (1.0 - 10.0) over the specified day window."""
    if not metrics:
        return StabilityTrend(
            current=None,
            start_score=None,
            delta=None,
            direction="unknown",
            avg_score=None,
            min_score=None,
            max_score=None,
            history=(),
            days=days,
            description="No stability index history was returned.",
        )

    ordered = sorted(metrics, key=lambda m: m.start)
    if days > 0:
        cutoff = ordered[-1].start - timedelta(days=days)
        window = [m for m in ordered if m.start >= cutoff]
        if not window:
            window = ordered
    else:
        window = ordered

    current = round(window[-1].index, 1)
    start_score = round(window[0].index, 1)
    delta = round(current - start_score, 1)

    if delta >= 0.3:
        direction = "improving"
    elif delta <= -0.3:
        direction = "declining"
    else:
        direction = "stable"

    avg_score = round(sum(m.index for m in window) / len(window), 1)
    min_score = round(min(m.index for m in window), 1)
    max_score = round(max(m.index for m in window), 1)
    history = tuple(m.index for m in window)

    if direction == "improving":
        desc = f"Improving (+{delta:.1f} pts over {days}d, avg {avg_score:.1f})"
    elif direction == "declining":
        desc = f"Declining ({delta:.1f} pts over {days}d, avg {avg_score:.1f})"
    else:
        desc = f"Stable ({current:.1f}/10 over {days}d, avg {avg_score:.1f})"

    return StabilityTrend(
        current=current,
        start_score=start_score,
        delta=delta,
        direction=direction,
        avg_score=avg_score,
        min_score=min_score,
        max_score=max_score,
        history=history,
        days=days,
        description=desc,
    )


def _build_cluster(burst: List[LogEntry]) -> FailureCluster:
    cats: Dict[str, int] = {}
    sources: Dict[str, int] = {}
    for e in burst:
        cat = failure_category(e)
        cats[cat] = cats.get(cat, 0) + 1

        crash = parse_crash(e)
        src = crash[0] if crash else e.source
        sources[src] = sources.get(src, 0) + 1

    top_cat, top_cat_count = max(cats.items(), key=lambda kv: kv[1])
    category = top_cat if (top_cat_count / len(burst)) >= 0.6 else "Mixed"
    primary_source = max(sources.items(), key=lambda kv: kv[1])[0]

    t_start = burst[0].timestamp
    t_end = burst[-1].timestamp
    span_mins = (t_end - t_start).total_seconds() / 60.0
    desc = f"{len(burst)} {category} events within {span_mins:.0f}m ({primary_source})"
    return FailureCluster(
        start_time=t_start,
        end_time=t_end,
        category=category,
        count=len(burst),
        primary_source=primary_source,
        events=tuple(burst),
        description=desc,
    )


def detect_failure_clusters(
    entries: List[LogEntry],
    window_hours: float = 2.0,
    min_events: int = 3,
) -> List[FailureCluster]:
    """Detect bursts/clusters of failures within a short time window."""
    failures = [e for e in entries if is_failure_event(e)]
    if len(failures) < min_events:
        return []

    failures.sort(key=lambda e: e.timestamp)
    clusters: List[FailureCluster] = []
    current_burst: List[LogEntry] = [failures[0]]

    for prev_e, cur_e in zip(failures, failures[1:]):
        gap = (cur_e.timestamp - prev_e.timestamp).total_seconds() / 3600.0
        if gap <= window_hours:
            current_burst.append(cur_e)
        else:
            if len(current_burst) >= min_events:
                clusters.append(_build_cluster(current_burst))
            current_burst = [cur_e]

    if len(current_burst) >= min_events:
        clusters.append(_build_cluster(current_burst))

    # Sort clusters: largest count first, then newest
    clusters.sort(key=lambda c: (-c.count, c.end_time))
    return clusters


def calculate_mtbf(entries: List[LogEntry], days: Optional[int] = None) -> Optional[float]:
    """Calculate Mean Time Between Failures in hours over the specified time window.

    Returns None if fewer than 2 failure events occurred.
    """
    failures = [e for e in entries if is_failure_event(e)]
    if len(failures) < 2:
        return None

    failures.sort(key=lambda e: e.timestamp)
    if days is not None and days > 0:
        latest_ts = failures[-1].timestamp
        cutoff = latest_ts - timedelta(days=days)
        failures = [e for e in failures if e.timestamp >= cutoff]
        if len(failures) < 2:
            return None

    total_span_hours = (failures[-1].timestamp - failures[0].timestamp).total_seconds() / 3600.0
    intervals_count = len(failures) - 1
    if intervals_count <= 0 or total_span_hours <= 0:
        return 0.0
    return round(total_span_hours / intervals_count, 1)


def format_mtbf(hours: Optional[float]) -> str:
    """Format MTBF hours into a human-friendly duration string."""
    if hours is None:
        return "No failures"
    if hours >= 48.0:
        return f"{hours / 24.0:.1f} days"
    if hours >= 1.0:
        return f"{hours:.1f} hrs"
    return f"{hours * 60:.0f} mins"


#: Exception codes seen in Application Error records, by their documented
#: NTSTATUS / CLR names. Only common, documented codes are listed; anything
#: else is shown as bare hex, never guessed.
EXCEPTION_CODES = {
    0xC0000005: "access violation",
    0xC0000409: "fail-fast / stack buffer overrun (the program stopped itself)",
    0xC000027B: "unhandled exception in a Store/WinRT app (stowed exception)",
    0xE0434352: "unhandled .NET exception",
    0xC0000374: "heap corruption",
    0xC00000FD: "stack overflow",
    0xC0000142: "a DLL failed to initialise",
    0xC000041D: "fatal exception in a user callback",
    0x80000003: "breakpoint",
}

_CRASH = re.compile(r"Faulting application name:\s*([^,]+),.*?Faulting module name:\s*([^,]+),"
                    r".*?Exception code:\s*(0x[0-9a-fA-F]+)", re.S | re.I)
_CRASH_NAME_ONLY = re.compile(r"Faulting application name:\s*([^,\r\n]+)", re.I)
_EXCEPTION_ONLY = re.compile(r"Exception code:\s*(0x[0-9a-fA-F]+)", re.I)
_HANG = re.compile(r"The program\s+(\S+)\s+(?:version\s+\S+\s+)?stopped interacting", re.I)


@dataclass
class Crasher:
    """One application's crashes and hangs across the loaded records."""
    app: str
    count: int
    module: str           # the faulting module seen most often ("(hang)" for hangs only)
    code: Optional[int]   # the exception code seen most often
    last: datetime


def explain_exception(code: Optional[int]) -> str:
    if code is None:
        return ""
    meaning = EXCEPTION_CODES.get(code & 0xFFFFFFFF, "")
    return f"0x{code & 0xFFFFFFFF:08X}" + (f" {meaning}" if meaning else "")


def parse_crash(entry: LogEntry) -> Optional[Tuple[str, str, Optional[int]]]:
    """(application, module, exception code) for an Application Error or Hang record."""
    message = entry.message or ""
    src = entry.source.lower()
    if src == "application error" or "faulting application name:" in message.lower():
        m = _CRASH.search(message)
        if m:
            return (m.group(1).strip(), m.group(2).strip(), int(m.group(3), 16))
        m_name = _CRASH_NAME_ONLY.search(message)
        if m_name:
            code_m = _EXCEPTION_ONLY.search(message)
            code = int(code_m.group(1), 16) if code_m else None
            return (m_name.group(1).strip(), "(unknown)", code)
        return None
    if src == "application hang" or "stopped interacting" in message.lower():
        m = _HANG.search(message)
        return (m.group(1).strip(), "(hang)", None) if m else None
    return None


def top_crashers(entries: List[LogEntry], limit: int = 5) -> List[Crasher]:
    """Applications by number of crash/hang records, worst first.

    The stability index says THAT apps crashed; this says which. Measured
    2026-10-09 on a machine at 4.1/10: SnippingTool.exe 37 records (all
    0xC000027B), python.exe 18, then single crashes of OneDrive, OneNote,
    Outlook, Explorer -- the summary used to say only "Application Error x13".
    """
    groups: Dict[str, List[Tuple[str, Optional[int], datetime]]] = {}
    names: Dict[str, str] = {}
    for e in entries:
        parsed = parse_crash(e)
        if parsed is None:
            continue
        app, module, code = parsed
        key = app.lower()
        names.setdefault(key, app)
        groups.setdefault(key, []).append((module, code, e.timestamp))
    out = []
    for key, rows in groups.items():
        modules: Dict[str, int] = {}
        codes: Dict[Optional[int], int] = {}
        for module, code, _when in rows:
            modules[module] = modules.get(module, 0) + 1
            if code is not None:
                codes[code] = codes.get(code, 0) + 1
        out.append(Crasher(names[key], len(rows), max(modules, key=modules.get),
                           max(codes, key=codes.get) if codes else None,
                           max(when for _m, _c, when in rows)))
    out.sort(key=lambda c: (-c.count, c.app.lower()))
    return out[:limit]


def _fmt_crasher(c: Crasher) -> str:
    where = "hung" if c.module == "(hang)" else f"in {c.module}"
    code = f", {explain_exception(c.code)}" if c.code is not None else ""
    return f"{c.app} x{c.count} ({where}{code})"


def summary_text(metrics: List[Metric], entries: List[LogEntry], problems: Optional[List[str]] = None) -> str:
    parts: List[str] = []
    cur = latest_index(metrics)
    if cur is not None:
        peak = max(m.index for m in metrics)
        parts.append(f"Stability index now {cur.index:.1f} / 10 (best in this history {peak:.1f}).")
        trend = calculate_stability_trend(metrics, days=30)
        if trend.direction != "unknown":
            parts.append(f"30-day trend: {trend.description}.")
        dips = find_dips(metrics, entries)
        if dips:
            worst = max(dips, key=lambda d: d.drop)
            parts.append(f"{len(dips)} drop(s); the biggest was {worst.drop:.1f} on "
                         f"{worst.when:%Y-%m-%d %H:00} ({_describe(worst.events)}).")
    elif metrics == []:
        parts.append("No stability index history was returned.")

    mtbf = calculate_mtbf(entries, days=30)
    if mtbf is not None:
        parts.append(f"MTBF: {format_mtbf(mtbf)}.")

    clusters = detect_failure_clusters(entries)
    if clusters:
        parts.append(f"{len(clusters)} failure cluster(s) detected (worst: {clusters[0].description}).")

    crashers = top_crashers(entries, limit=4)
    if crashers:
        parts.append("Most crashes: " + "; ".join(_fmt_crasher(c) for c in crashers) + ".")
    errors = sum(1 for e in entries if e.level == "Error")
    warnings = sum(1 for e in entries if e.level == "Warning")
    parts.append(f"{len(entries)} records: {errors} failures, {warnings} warnings.")
    if problems:
        parts.extend(problems)
    return " ".join(parts)


def _describe(events) -> str:
    if not events:
        return "no failing record in that hour"
    names: Dict[str, int] = {}
    for e in events:
        crash = parse_crash(e)
        name = crash[0] if crash else e.source      # "SnippingTool.exe", not "Application Error"
        names[name] = names.get(name, 0) + 1
    return ", ".join(f"{n} x{c}" if c > 1 else n for n, c in sorted(names.items(), key=lambda kv: -kv[1])[:3])


def sparkline_tooltip(metrics: List[Metric]) -> str:
    """Hover text for the stability sparkline: the whole loaded history's range."""
    if not metrics:
        return "No stability index history was returned."
    ordered = sorted(metrics, key=lambda m: m.start)
    first, last = ordered[0], ordered[-1]
    worst = min(ordered, key=lambda m: m.index)
    return (
        f"Stability index, {first.start:%Y-%m-%d %H:%M} to {last.start:%Y-%m-%d %H:%M}.\n"
        f"Now: {last.index:.1f} / 10.  Lowest: {worst.index:.1f} / 10 on {worst.start:%Y-%m-%d %H:00}."
    )


def detail_html(entry: LogEntry, metrics: List[Metric]) -> str:
    crash = parse_crash(entry)
    head = ""
    if crash is not None and crash[2] is not None:
        head = (f"<b>Exception:</b> {html.escape(explain_exception(crash[2]))}<br>"
                f"<b>Faulting module:</b> {html.escape(crash[1])}<br>")
    m = index_at(metrics, entry.timestamp)
    if m is None:
        return head + ("<hr>" if head else "")
    later = index_at(metrics, entry.timestamp + timedelta(hours=1))
    text = head + f"<b>Stability index that hour:</b> {m.index:.1f} / 10"
    if later is not None and later.index != m.index:
        text += f" (next hour {later.index:.1f})"
    return text + "<br><hr>"
