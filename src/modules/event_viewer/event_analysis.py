"""Triage logic for loaded events: presets, grouping, frequency, correlation.

Qt-free. Everything here works on `LogEntry` lists whose `raw` carries
`event_id` and `provider` (see `event_query`), so it is tested with plain
fabricated entries and run against real ones.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Set, Tuple

from core.types import LogEntry

_BLOCKS = "▁▂▃▄▅▆▇█"
_SEVERITY = {"Critical": 3, "Error": 2, "Warning": 1}


def event_id(entry: LogEntry) -> Optional[int]:
    value = (entry.raw or {}).get("event_id")
    return value if isinstance(value, int) else None


def provider_of(entry: LogEntry) -> str:
    return str((entry.raw or {}).get("provider") or entry.source or "")


def _is(entry: LogEntry, provider_part: str, ids) -> bool:
    return provider_part in provider_of(entry).lower() and event_id(entry) in ids


def _any_level(entry: LogEntry, levels) -> bool:
    return entry.level in levels


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    match: Callable[[LogEntry], bool]


def _unexpected_shutdown(e):
    return (_is(e, "kernel-power", {41}) or _is(e, "eventlog", {6008})
            or _is(e, "bugcheck", {1001}) or _is(e, "systemerrorreporting", {1001}))


def _service_crash(e):
    return _is(e, "service control manager", {7031, 7034, 7023, 7024, 7009, 7011})


def _disk(e):
    prov = provider_of(e).lower()
    if _is(e, "disk", {7, 11, 15, 51, 52, 153, 154, 157}) and prov.endswith("disk"):
        return True
    if _is(e, "ntfs", {55, 137}):
        return True
    return prov in ("stornvme", "storahci", "iastora", "iastorv") and e.level in ("Critical", "Error", "Warning")


def _app_crash(e):
    return (_is(e, "application error", {1000}) or _is(e, "application hang", {1002})
            or _is(e, ".net runtime", {1025, 1026}))


def _whea(e):
    return "whea" in provider_of(e).lower()


def _driver(e):
    prov = provider_of(e).lower()
    return (_is(e, "service control manager", {7000, 7001, 7026})
            or _is(e, "kernel-pnp", {219, 411})
            or prov == "display" and e.level in ("Critical", "Error", "Warning")
            or "driverframeworks" in prov and e.level in ("Critical", "Error", "Warning")
            or "wudfrd" in prov)


def _time(e):
    prov = provider_of(e).lower()
    return ("time-service" in prov or "w32time" in prov) and e.level in ("Critical", "Error", "Warning")


PRESETS: Tuple[Preset, ...] = (
    Preset("all", "Everything", "Every loaded event", lambda e: True),
    Preset("problems", "Critical + errors + warnings", "Anything above informational",
           lambda e: e.level in ("Critical", "Error", "Warning")),
    Preset("errors", "Critical + errors", "Critical and Error events only",
           lambda e: e.level in ("Critical", "Error")),
    Preset("shutdown", "Unexpected shutdowns", "Kernel-Power 41, EventLog 6008, BugCheck 1001", _unexpected_shutdown),
    Preset("service", "Service crashes / timeouts", "Service Control Manager 7031, 7034, 7023, 7024, 7009, 7011",
           _service_crash),
    Preset("disk", "Disk and file-system errors", "disk 7/11/15/51/52/153/154/157, Ntfs 55/137, storage-port errors", _disk),
    Preset("appcrash", "Application crashes and hangs", "Application Error 1000, Application Hang 1002, .NET 1025/1026",
           _app_crash),
    Preset("whea", "Hardware errors (WHEA)", "WHEA-Logger: machine checks, PCIe and memory errors", _whea),
    Preset("driver", "Driver failures", "Driver load failures, PnP problems, display driver errors", _driver),
    Preset("logon", "Failed logons", "Security 4625 (needs the Security log, i.e. elevation)",
           lambda e: _is(e, "security-auditing", {4625}) or _is(e, "security", {4625})),
    Preset("time", "Time-service problems", "W32Time / Time-Service warnings and errors", _time),
)

_PRESET_BY_KEY = {p.key: p for p in PRESETS}


def preset(key: str) -> Preset:
    return _PRESET_BY_KEY[key]


def apply_preset(entries: List[LogEntry], key: str) -> List[LogEntry]:
    match = _PRESET_BY_KEY[key].match
    return [e for e in entries if match(e)]


def preset_counts(entries: List[LogEntry]) -> Dict[str, int]:
    """How many loaded events each preset matches, in one pass."""
    counts = {p.key: 0 for p in PRESETS}
    for e in entries:
        for p in PRESETS:
            if p.match(e):
                counts[p.key] += 1
    return counts


def filter_since(entries: List[LogEntry], hours: Optional[float],
                 now: Optional[datetime] = None) -> List[LogEntry]:
    """Entries no older than `hours` (None = keep all)."""
    if not hours:
        return list(entries)
    cutoff = (now or datetime.now()) - timedelta(hours=hours)
    return [e for e in entries if e.timestamp >= cutoff]


def filter_text(entries: List[LogEntry], text: str) -> List[LogEntry]:
    """Case-insensitive match on source, level, ID and message."""
    needle = (text or "").strip().lower()
    if not needle:
        return list(entries)
    out = []
    for e in entries:
        hay = f"{e.source} {e.level} {event_id(e) or ''} {e.message}".lower()
        if needle in hay:
            out.append(e)
    return out


def row_values(entry: LogEntry) -> list:
    """The extra columns: [event id, occurrences]. Ints, so they sort as numbers.

    Occurrences is blank on an ungrouped row: "1" on every line would be noise."""
    eid = event_id(entry)
    count = (entry.raw or {}).get("count", "")
    return [eid if eid is not None else "", count]


def group_entries(entries: List[LogEntry]) -> List[LogEntry]:
    """One synthetic row per (provider, event id), most frequent first.

    The row's time is the LAST occurrence, its level the WORST seen, and its
    message the newest occurrence's text, so the row still reads as an event.
    """
    buckets: Dict[Tuple[str, Optional[int]], List[LogEntry]] = defaultdict(list)
    for e in entries:
        buckets[(provider_of(e), event_id(e))].append(e)
    rows = []
    for (prov, eid), items in buckets.items():
        items.sort(key=lambda x: x.timestamp)
        newest = items[-1]
        worst = max(items, key=lambda x: _SEVERITY.get(x.level, 0)).level
        first_line = (newest.message or "").strip().splitlines()[0] if (newest.message or "").strip() else ""
        rows.append(LogEntry(
            timestamp=newest.timestamp,
            source=newest.source,
            level=worst,
            message=f"x{len(items)}  {first_line}",
            raw={"group": True, "event_id": eid, "provider": prov, "count": len(items),
                 "first": items[0].timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                 "last": newest.timestamp.strftime("%Y-%m-%d %H:%M:%S")},
        ))
    rows.sort(key=lambda r: (-r.raw["count"], r.timestamp), reverse=False)
    return rows


def occurrences(entries: List[LogEntry], entry: LogEntry) -> List[LogEntry]:
    """Every loaded event with the same provider and ID as `entry` (oldest first)."""
    key = (provider_of(entry), event_id(entry))
    return sorted((e for e in entries if (provider_of(e), event_id(e)) == key),
                  key=lambda e: e.timestamp)


def sparkline(times: List[datetime], start: datetime, end: datetime, buckets: int = 24) -> str:
    """Unicode bar chart of how many of `times` fall in each of `buckets` slices."""
    if not times or end <= start or buckets < 1:
        return ""
    span = (end - start).total_seconds()
    counts = [0] * buckets
    for t in times:
        slot = int((t - start).total_seconds() / span * buckets)
        counts[min(max(slot, 0), buckets - 1)] += 1
    peak = max(counts)
    if peak == 0:
        return ""
    return "".join(_BLOCKS[0] if c == 0 else _BLOCKS[min(len(_BLOCKS) - 1, int(c / peak * (len(_BLOCKS) - 1)))]
                   for c in counts)


@dataclass(frozen=True)
class Frequency:
    total: int
    last_hour: int
    last_24h: int
    first: datetime
    last: datetime
    spark: str


def frequency(entries: List[LogEntry], entry: LogEntry, now: Optional[datetime] = None,
              buckets: int = 24) -> Frequency:
    """How often this (provider, ID) occurred, and when, across `entries`."""
    now = now or datetime.now()
    same = occurrences(entries, entry) or [entry]
    times = [e.timestamp for e in same]
    lo = min(e.timestamp for e in entries) if entries else times[0]
    hi = max(now, max(times))
    return Frequency(
        total=len(same),
        last_hour=sum(1 for t in times if t >= now - timedelta(hours=1)),
        last_24h=sum(1 for t in times if t >= now - timedelta(hours=24)),
        first=times[0], last=times[-1],
        spark=sparkline(times, lo, hi, buckets),
    )


def correlate(entries: List[LogEntry], entry: LogEntry, window_s: int = 60,
              limit: int = 40, min_level: str = "Warning") -> List[Tuple[float, LogEntry]]:
    """Other events within `window_s` of `entry`: (seconds offset, event).

    Only Warning and worse by default -- "what ELSE went wrong nearby" -- since
    informational chatter would bury the answer. Closest in time first.
    """
    floor = _SEVERITY.get(min_level, 0)
    span = timedelta(seconds=window_s)
    near = []
    for e in entries:
        if e is entry or abs(e.timestamp - entry.timestamp) > span:
            continue
        if e.raw.get("group"):
            continue
        if _SEVERITY.get(e.level, 0) < floor:
            continue
        if (provider_of(e), event_id(e), e.timestamp) == (provider_of(entry), event_id(entry), entry.timestamp):
            continue
        near.append(((e.timestamp - entry.timestamp).total_seconds(), e))
    near.sort(key=lambda pair: abs(pair[0]))
    return near[:limit]


def level_counts(entries: List[LogEntry]) -> Counter:
    return Counter(e.level for e in entries)


def entry_as_text(entry: LogEntry) -> str:
    """A plain-text block for pasting into a ticket."""
    raw = entry.raw or {}
    head = f"{entry.timestamp:%Y-%m-%d %H:%M:%S}  [{entry.level}]  {entry.source}"
    eid = raw.get("event_id")
    if eid is not None:
        head += f"  ID {eid}"
    lines = [head]
    if raw.get("log_name"):
        lines.append(f"Log: {raw['log_name']}   Computer: {raw.get('computer', '')}   Record: {raw.get('record_number', '')}")
    lines.append(entry.message or "")
    data = raw.get("data")
    if isinstance(data, dict) and data:
        lines.append("")
        lines.extend(f"  {k} = {v}" for k, v in data.items())
    return "\n".join(lines)


#: Presets worth naming in the summary line when they match something.
_HEADLINE = ("shutdown", "service", "disk", "whea", "driver", "appcrash", "logon", "time")


def summary_text(entries: List[LogEntry], notes: List[str], range_label: str = "") -> str:
    """One paragraph for the strip above the table: counts, findings, caveats."""
    if not entries:
        base = "No events were returned for this range."
    else:
        levels = level_counts(entries)
        base = (f"{len(entries):,} events{' in the ' + range_label if range_label else ''}: "
                f"{levels.get('Critical', 0)} critical, {levels.get('Error', 0)} errors, "
                f"{levels.get('Warning', 0)} warnings.")
        counts = preset_counts(entries)
        found = [f"{counts[k]} {_PRESET_BY_KEY[k].label.lower()}" for k in _HEADLINE if counts[k]]
        if found:
            base += " Worth a look: " + "; ".join(found) + "."
    if notes:
        base += " " + " ".join(notes)
    return base


# -- Burst Analysis & Storm Detection ----------------------------------------

@dataclass(frozen=True)
class EventStorm:
    """A runaway provider or event stream generating rapid bursts (>30 events/min)."""
    provider: str
    total_count: int
    peak_burst_count: int
    rate_per_minute: float
    burst_start: Optional[datetime] = None
    burst_end: Optional[datetime] = None
    top_event_ids: Dict[int, int] = field(default_factory=dict)
    sample_message: str = ""
    event_id: Optional[int] = None


@dataclass(frozen=True)
class StormReport:
    """Analysis of event bursts, runaway storms, and top noisy providers/IDs."""
    storms: List[EventStorm] = field(default_factory=list)
    noisy_providers: List[Tuple[str, int]] = field(default_factory=list)
    noisy_event_ids: List[Tuple[str, Optional[int], int]] = field(default_factory=list)
    total_burst_events: int = 0
    has_storm: bool = False


def detect_event_storms(entries: List[LogEntry], burst_threshold: int = 30) -> StormReport:
    """Identify event storms / runaway providers generating bursts (>30 events/minute).

    Uses a sliding 60-second window across timestamps to measure peak bursts.
    Also compiles top noisy providers and top noisy (provider, event_id) combinations.
    """
    if not entries:
        return StormReport()

    by_provider: Dict[str, List[LogEntry]] = defaultdict(list)
    provider_id_counts: Counter = Counter()

    for e in entries:
        prov = provider_of(e)
        by_provider[prov].append(e)
        provider_id_counts[(prov, event_id(e))] += 1

    storms: List[EventStorm] = []

    for prov, p_entries in by_provider.items():
        sorted_entries = sorted(p_entries, key=lambda x: x.timestamp)
        n = len(sorted_entries)
        if n == 0:
            continue

        # Sliding 60-second window
        peak_count = 0
        burst_start: Optional[datetime] = None
        burst_end: Optional[datetime] = None
        left = 0

        for right in range(n):
            while (sorted_entries[right].timestamp - sorted_entries[left].timestamp).total_seconds() > 60:
                left += 1
            current_window_len = right - left + 1
            if current_window_len > peak_count:
                peak_count = current_window_len
                burst_start = sorted_entries[left].timestamp
                burst_end = sorted_entries[right].timestamp

        timespan_s = (sorted_entries[-1].timestamp - sorted_entries[0].timestamp).total_seconds()
        if timespan_s > 0:
            rate_per_min = (n / timespan_s) * 60.0
        else:
            rate_per_min = float(n)

        # Storm condition: > burst_threshold in any 60-second window, or rate > burst_threshold
        is_storm = (peak_count > burst_threshold) or (rate_per_min > burst_threshold and n > burst_threshold)

        if is_storm:
            id_counts = Counter(event_id(e) for e in p_entries if event_id(e) is not None)
            top_ids = dict(id_counts.most_common(5))
            sample_msg = (sorted_entries[-1].message or "").strip().splitlines()[0] if sorted_entries[-1].message else ""
            primary_id = id_counts.most_common(1)[0][0] if id_counts else None

            storms.append(EventStorm(
                provider=prov,
                total_count=n,
                peak_burst_count=peak_count,
                rate_per_minute=max(float(peak_count), round(rate_per_min, 1)),
                burst_start=burst_start,
                burst_end=burst_end,
                top_event_ids=top_ids,
                sample_message=sample_msg,
                event_id=primary_id,
            ))

    storms.sort(key=lambda s: (-s.peak_burst_count, -s.total_count))

    noisy_providers = Counter({prov: len(p_entries) for prov, p_entries in by_provider.items()}).most_common(10)
    noisy_event_ids = [(p, eid, cnt) for (p, eid), cnt in provider_id_counts.most_common(10)]
    total_burst_events = sum(s.total_count for s in storms)

    return StormReport(
        storms=storms,
        noisy_providers=noisy_providers,
        noisy_event_ids=noisy_event_ids,
        total_burst_events=total_burst_events,
        has_storm=bool(storms),
    )


# -- Benign Background Noise Filtering --------------------------------------

def is_benign_noise(entry: LogEntry) -> bool:
    """Determine whether an event is routine, benign background noise.

    Targets:
    - DCOM 10016 (classic benign permission chatter on all Windows installs)
    - Benign Kerberos 4768 (TGT requests with success/0x0 result code, informational)
    - Routine time syncs (W32Time / Time-Service 35, 37, 158 or Info level syncs)
    - Routine Service Control Manager 7036 (service state changes, informational)
    - Routine Group Policy 1500/1501 (policy applied successfully, informational)
    """
    prov = provider_of(entry).lower()
    eid = event_id(entry)
    raw = entry.raw if isinstance(entry.raw, dict) else {}
    raw_data = raw.get("data", {}) if isinstance(raw, dict) else {}
    if not isinstance(raw_data, dict):
        raw_data = {}

    # 1. DCOM 10016
    if ("dcom" in prov or "distributedcom" in prov or entry.source == "DCOM") and eid == 10016:
        return True

    # 2. Benign Kerberos 4768 (Success / informational)
    if ("security" in prov or "security-auditing" in prov) and eid == 4768:
        result_code = raw_data.get("ResultCode", "0x0")
        if entry.level in ("Info", "Verbose", "LogAlways", 0, 4, 5) and result_code in ("0x0", "0", ""):
            return True

    # 3. Routine time syncs (W32Time / Time-Service)
    if "time-service" in prov or "w32time" in prov or "time" in prov:
        if eid in (35, 37, 158):
            return True
        if entry.level in ("Info", "Verbose", "LogAlways", 0, 4, 5) and entry.level not in ("Critical", "Error", "Warning"):
            return True

    # 4. Routine SCM 7036 informational service state changes
    if "service control manager" in prov and eid == 7036:
        if entry.level in ("Info", "Verbose", 0, 4, 5):
            return True

    # 5. Routine Group Policy success
    if "grouppolicy" in prov and eid in (1500, 1501):
        if entry.level in ("Info", "Verbose", 0, 4, 5):
            return True

    return False


def filter_noise(entries: List[LogEntry], hide_noise: bool = True) -> List[LogEntry]:
    """Hide known benign background noise when `hide_noise` is True."""
    if not hide_noise:
        return list(entries)
    return [e for e in entries if not is_benign_noise(e)]


# -- Provider Muting & Isolating ---------------------------------------------

def filter_providers(
    entries: List[LogEntry],
    muted: Optional[Set[str]] = None,
    isolated: Optional[str] = None,
) -> List[LogEntry]:
    """Filter entries by muted or isolated provider names.

    - isolated: if given, ONLY entries matching this provider are kept.
    - muted: if given, entries matching any of these providers are excluded.
    """
    if not muted and not isolated:
        return list(entries)

    isolated_lower = isolated.strip().lower() if isolated else None
    muted_lower = {m.strip().lower() for m in muted} if muted else set()

    out = []
    for e in entries:
        prov = provider_of(e).strip().lower()
        src = (e.source or "").strip().lower()

        if isolated_lower:
            if prov != isolated_lower and src != isolated_lower:
                continue

        if muted_lower:
            if prov in muted_lower or src in muted_lower:
                continue

        out.append(e)

    return out

