"""Reliability records and the stability index, made to answer "what dragged it down?".

Qt-free. `Win32_ReliabilityStabilityMetrics` gives an hourly 1-10 index;
`Win32_ReliabilityRecords` gives the events behind it. Windows' own Reliability
Monitor shows exactly this pairing; here the pairing is computed.
"""
from __future__ import annotations

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


def summary_text(metrics: List[Metric], entries: List[LogEntry], problems: Optional[List[str]] = None) -> str:
    parts: List[str] = []
    cur = latest_index(metrics)
    if cur is not None:
        peak = max(m.index for m in metrics)
        parts.append(f"Stability index now {cur.index:.1f} / 10 (best in this history {peak:.1f}).")
        dips = find_dips(metrics, entries)
        if dips:
            worst = max(dips, key=lambda d: d.drop)
            parts.append(f"{len(dips)} drop(s); the biggest was {worst.drop:.1f} on "
                         f"{worst.when:%Y-%m-%d %H:00} ({_describe(worst.events)}).")
    elif metrics == []:
        parts.append("No stability index history was returned.")
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
        names[e.source] = names.get(e.source, 0) + 1
    return ", ".join(f"{n} x{c}" if c > 1 else n for n, c in sorted(names.items(), key=lambda kv: -kv[1])[:3])


def sparkline_tooltip(metrics: List[Metric]) -> str:
    """Hover text for the stability sparkline: the whole loaded history's range.

    `summary_text` above already states the single biggest one-hour drop, but
    a slow multi-day decline (measured on this real machine: a flat 10.0 on
    2026-09-04 sliding to 4.4 by 2026-09-29, with no single hour ever dropping
    more than ~3 points) never shows up as "a drop" at all -- this is the
    plain start/now/lowest reading the sparkline draws, in words.
    """
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
    m = index_at(metrics, entry.timestamp)
    if m is None:
        return ""
    later = index_at(metrics, entry.timestamp + timedelta(hours=1))
    text = f"<b>Stability index that hour:</b> {m.index:.1f} / 10"
    if later is not None and later.index != m.index:
        text += f" (next hour {later.index:.1f})"
    return text + "<br><hr>"
