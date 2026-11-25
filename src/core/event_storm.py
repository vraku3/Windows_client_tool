"""Event Storm & Burst Collapse Engine (Qt-Free).

Detects runaway event loops, floods, and repetition bursts in Windows logs:
- Service crash / restart loops (SCM 7031/7034).
- Floods of DCOM 10016 permission warnings.
- Continuous disk retry bursts (disk 153).
- Network disconnect / reconnect storms.
- Security audit failure bursts (4625).

Provides intelligent collapse so administrators can see the true signal
instead of thousands of identical noise records.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Dict, List, Optional, Sequence, Tuple

from core.types import LogEntry


@dataclass(frozen=True)
class EventStorm:
    """An identified high-frequency burst of similar or identical events."""
    key: str
    source: str
    event_id: Optional[int]
    total_count: int
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    events_per_second: float
    sample_entry: LogEntry
    is_critical_burst: bool


@dataclass
class CollapsedLogStream:
    """Stream of log entries with runaway storms collapsed into summary badges."""
    entries: List[LogEntry] = field(default_factory=list)
    collapsed_count: int = 0
    storms_detected: List[EventStorm] = field(default_factory=list)


def _entry_key(e: LogEntry) -> str:
    """Generate a canonical signature key for grouping repetitive events."""
    src = (e.source or "").strip().lower()
    eid = e.raw.get("event_id") if isinstance(e.raw, dict) else None
    if eid is not None:
        return f"{src}:{eid}"

    # For text logs without event_id, normalize message (remove numbers/guids/timestamps)
    msg = (e.message or "").strip().lower()
    # Strip GUIDs and hex numbers
    norm = re.sub(r"\{[0-9a-fA-F\-]{36}\}", "<GUID>", msg)
    norm = re.sub(r"0x[0-9a-fA-F]+", "<HEX>", norm)
    norm = re.sub(r"\b\d+\b", "<NUM>", norm)
    # Take first 80 characters of normalized message
    return f"{src}:{norm[:80]}"


def detect_event_storms(
    entries: Sequence[LogEntry],
    threshold_count: int = 8,
    window_seconds: int = 60,
) -> List[EventStorm]:
    """Detect event bursts exceeding `threshold_count` within `window_seconds`."""
    if not entries:
        return []

    # Sort chronologically
    sorted_entries = sorted((e for e in entries if e.timestamp is not None), key=lambda x: x.timestamp)
    if not sorted_entries:
        return []

    storms: List[EventStorm] = []
    # Group entries by key
    by_key: Dict[str, List[LogEntry]] = defaultdict(list)
    for e in sorted_entries:
        by_key[_entry_key(e)].append(e)

    window_delta = timedelta(seconds=max(5, window_seconds))

    for key, group in by_key.items():
        if len(group) < threshold_count:
            continue

        # Sliding window over the group to find burst segments
        burst_start_idx = 0
        while burst_start_idx < len(group):
            w_start = group[burst_start_idx].timestamp
            w_end = w_start + window_delta

            # Find all entries within this window
            burst_end_idx = burst_start_idx
            while burst_end_idx < len(group) and group[burst_end_idx].timestamp <= w_end:
                burst_end_idx += 1

            burst_len = burst_end_idx - burst_start_idx
            if burst_len >= threshold_count:
                # Extend window forward as long as events continue arriving closely
                last_time = group[burst_end_idx - 1].timestamp
                while burst_end_idx < len(group):
                    next_time = group[burst_end_idx].timestamp
                    # If gap to next event is less than half the window delta, keep extending
                    if (next_time - last_time).total_seconds() <= (window_seconds / 2.0):
                        last_time = next_time
                        burst_end_idx += 1
                    else:
                        break

                total_burst = burst_end_idx - burst_start_idx
                first_e = group[burst_start_idx]
                last_e = group[burst_end_idx - 1]
                duration = max(1.0, (last_e.timestamp - first_e.timestamp).total_seconds())
                eps = total_burst / duration

                lvl = (first_e.level or "").lower()
                is_crit = "err" in lvl or "crit" in lvl or eps >= 5.0

                storms.append(EventStorm(
                    key=key,
                    source=first_e.source,
                    event_id=first_e.raw.get("event_id") if isinstance(first_e.raw, dict) else None,
                    total_count=total_burst,
                    start_time=first_e.timestamp,
                    end_time=last_e.timestamp,
                    duration_seconds=duration,
                    events_per_second=eps,
                    sample_entry=first_e,
                    is_critical_burst=is_crit,
                ))

                burst_start_idx = burst_end_idx
            else:
                burst_start_idx += 1

    return storms


def collapse_repetitive_events(
    entries: Sequence[LogEntry],
    max_consecutive: int = 2,
    time_window_seconds: int = 300,
) -> CollapsedLogStream:
    """Collapse consecutive or closely clustered repetitive events into summary rows."""
    if not entries:
        return CollapsedLogStream()

    sorted_entries = sorted((e for e in entries if e.timestamp is not None), key=lambda x: x.timestamp)
    if not sorted_entries:
        return CollapsedLogStream(entries=list(entries))

    output: List[LogEntry] = []
    collapsed_count = 0
    storms = detect_event_storms(sorted_entries, threshold_count=5, window_seconds=60)

    # Walk entries and collapse consecutive runs of same key
    i = 0
    n = len(sorted_entries)
    while i < n:
        cur = sorted_entries[i]
        key = _entry_key(cur)

        # Lookahead
        j = i + 1
        while j < n and _entry_key(sorted_entries[j]) == key:
            gap = (sorted_entries[j].timestamp - sorted_entries[j - 1].timestamp).total_seconds()
            if gap > time_window_seconds:
                break
            j += 1

        run_len = j - i
        if run_len > max_consecutive:
            # Emit first 1-2 entries
            for k in range(min(max_consecutive, run_len)):
                output.append(sorted_entries[i + k])

            # Emit a collapsed summary badge entry
            suppressed = run_len - max_consecutive
            collapsed_count += suppressed
            first_ts = sorted_entries[i].timestamp.strftime("%H:%M:%S")
            last_ts = sorted_entries[j - 1].timestamp.strftime("%H:%M:%S")

            summary_msg = (
                f"[⚡ STORM COLLAPSED: {suppressed} duplicate event(s) suppressed between {first_ts} and {last_ts}] "
                f"{cur.message}"
            )
            raw_data = dict(cur.raw or {})
            raw_data["is_storm_summary"] = True
            raw_data["suppressed_count"] = suppressed
            raw_data["storm_start"] = sorted_entries[i].timestamp.isoformat()
            raw_data["storm_end"] = sorted_entries[j - 1].timestamp.isoformat()

            output.append(LogEntry(
                timestamp=sorted_entries[j - 1].timestamp,
                source=f"{cur.source} [Storm x{suppressed}]",
                level=cur.level,
                message=summary_msg,
                raw=raw_data,
            ))
            i = j
        else:
            output.append(cur)
            i += 1

    return CollapsedLogStream(
        entries=output,
        collapsed_count=collapsed_count,
        storms_detected=storms,
    )
