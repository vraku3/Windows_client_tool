"""What the Processes tab knows about a row beyond Task Manager's five columns.

No Qt in here, so all of it tests without a display: the optional columns, the
filter chips, the "why is this row worth a look" badges, and the detail text.

A value we were refused is shown as blank, never as a guess -- each of these
reads `None` from `ProcessDetails` when the kernel said no.
"""
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

_FILETIME_UNIX_EPOCH = 116444736000000000

RECENT_SECONDS = 300
HIGH_CPU_PERCENT = 5.0
HIGH_MEMORY_BYTES = 500 * 1024 * 1024


def started_at(info) -> Optional[datetime]:
    ticks = info.raw.create_time
    if not ticks or ticks < _FILETIME_UNIX_EPOCH:
        return None
    seconds = (ticks - _FILETIME_UNIX_EPOCH) / 1e7
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone()


def cpu_seconds(info) -> float:
    """Total processor time the process has used, kernel plus user."""
    return (info.raw.kernel_time + info.raw.user_time) / 1e7


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class Column(NamedTuple):
    key: str
    title: str
    width: int
    text: Callable       # info -> str
    value: Callable      # info -> sortable
    summable: bool = False


def _started_text(info) -> str:
    when = started_at(info)
    return when.strftime("%Y-%m-%d %H:%M:%S") if when else ""


def _integrity(info) -> str:
    return info.details.integrity or ""


def _rate_text(bytes_per_s: float) -> str:
    return f"{_fmt_bytes(bytes_per_s)}/s" if bytes_per_s >= 1 else ""


def _net_rate(info) -> float:
    from . import net_trace           # only stdlib inside; imported lazily anyway
    return net_trace.rate_of(info.pid)


COLUMNS: Tuple[Column, ...] = (
    Column("network", "Network", 90, lambda i: _rate_text(_net_rate(i)), _net_rate, summable=True),
    Column("user", "User", 130, lambda i: i.details.user or "",
           lambda i: (i.details.user or "").lower()),
    Column("session", "Session", 64, lambda i: str(i.raw.session),
           lambda i: i.raw.session),
    Column("threads", "Threads", 70, lambda i: str(i.raw.threads),
           lambda i: i.raw.threads, summable=True),
    Column("handles", "Handles", 70, lambda i: f"{i.raw.handles:,}",
           lambda i: i.raw.handles, summable=True),
    Column("cpu_time", "CPU time", 84, lambda i: format_duration(cpu_seconds(i)),
           cpu_seconds, summable=True),
    Column("started", "Started", 140, _started_text,
           lambda i: i.raw.create_time),
    Column("elevated", "Elevated",
           74, lambda i: {True: "Yes", False: "No"}.get(i.details.elevated, ""),
           lambda i: {True: 2, False: 1}.get(i.details.elevated, 0)),
    Column("integrity", "Integrity", 84, _integrity, lambda i: _integrity(i).lower()),
    Column("arch", "Arch", 56, lambda i: i.details.architecture or "",
           lambda i: i.details.architecture or ""),
    Column("publisher", "Publisher", 150, lambda i: i.details.company or "",
           lambda i: (i.details.company or "").lower()),
    Column("path", "Path", 260, lambda i: i.details.path or "",
           lambda i: (i.details.path or "").lower()),
    Column("cmdline", "Command line", 320, lambda i: i.details.cmdline or "",
           lambda i: (i.details.cmdline or "").lower()),
)

BY_KEY: Dict[str, Column] = {c.key: c for c in COLUMNS}
DEFAULT_VISIBLE: Tuple[str, ...] = ("user",)


def aggregate_text(column: Column, members) -> str:
    """One cell for an app row that stands for several processes."""
    if column.summable:
        total = sum(column.value(m) for m in members)
        if column.key == "cpu_time":
            return format_duration(total)
        if column.key == "network":
            return _rate_text(total)
        return f"{int(total):,}"
    unique = {column.text(m) for m in members if column.text(m)}
    return unique.pop() if len(unique) == 1 else ("(varies)" if unique else "")


# ---- filter chips -----------------------------------------------------------

def _is_microsoft(info) -> bool:
    return "microsoft" in (info.details.company or "").lower()


def _cpu_hot(info) -> bool:
    return (info.rates.cpu_percent or 0.0) >= HIGH_CPU_PERCENT


def _mem_hot(info) -> bool:
    return info.raw.working_set_private >= HIGH_MEMORY_BYTES


def _elevated(info) -> bool:
    return info.details.elevated is True


def _third_party(info) -> bool:
    # Unknown publisher counts: that is exactly what an admin wants to see.
    return not _is_microsoft(info) and info.pid > 4


def _recent(info) -> bool:
    """Started in the last five minutes: the quickest way to spot what just
    launched itself, or what a click or a logon script just spawned."""
    when = started_at(info)
    if when is None:
        return False
    return (datetime.now(timezone.utc) - when).total_seconds() <= RECENT_SECONDS


def _unreadable(info) -> bool:
    return info.details.path is None and info.pid > 4


FILTERS: Tuple[Tuple[str, str, Callable], ...] = (
    ("all", "All", lambda i: True),
    ("cpu", "High CPU", _cpu_hot),
    ("memory", "High memory", _mem_hot),
    ("recent", "Started <5 min", _recent),
    ("elevated", "Elevated", _elevated),
    ("thirdparty", "Not Microsoft", _third_party),
    ("unreadable", "Unreadable", _unreadable),
)
FILTER_BY_KEY = {key: fn for key, _, fn in FILTERS}


def passes(filter_key: str, info) -> bool:
    return FILTER_BY_KEY.get(filter_key, FILTER_BY_KEY["all"])(info)


def filter_counts(infos) -> Dict[str, int]:
    """How many rows each chip would show, so a chip can say "High CPU (3)"."""
    infos = list(infos)
    return {key: sum(1 for i in infos if fn(i)) for key, _, fn in FILTERS}


# ---- badges ------------------------------------------------------------------

def badges(info) -> List[str]:
    """Short reasons a row deserves a second look."""
    found = []
    if _elevated(info):
        found.append("elevated")
    if _cpu_hot(info):
        found.append("high CPU")
    if _mem_hot(info):
        found.append("high memory")
    if _unreadable(info):
        found.append("unreadable")
    return found


# ---- detail panel -------------------------------------------------------------

def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def detail_text(info, snapshot, services: Optional[List[str]] = None) -> str:
    """Everything worth reading about one process, as plain text."""
    d, r = info.details, info.raw
    parent = snapshot.by_pid.get(r.ppid) if snapshot is not None else None
    children = ([p for p in snapshot.by_pid.values() if p.raw.ppid == r.pid]
                if snapshot is not None else [])
    started = started_at(info)
    lines = [
        f"{d.description or info.name}   (PID {r.pid})",
        f"Image:      {info.name}",
        f"Path:       {d.path or _why(d.path_error)}",
        f"Command:    {d.cmdline or _why(d.cmdline_error)}",
        f"User:       {d.user or _why(d.user_error)}"
        + (f"   integrity {d.integrity}" if d.integrity else "")
        + ("   ELEVATED" if d.elevated else ""),
        f"Publisher:  {d.company or 'unknown'}   {d.architecture or ''}".rstrip(),
        f"Parent:     {parent.name + f' (PID {parent.pid})' if parent else f'PID {r.ppid} (gone)'}",
        f"Started:    {started.strftime('%Y-%m-%d %H:%M:%S') if started else 'unknown'}"
        f"   CPU time {format_duration(cpu_seconds(info))}",
        f"Memory:     {_fmt_bytes(r.working_set_private)} private   "
        f"{_fmt_bytes(r.working_set)} working set   peak {_fmt_bytes(r.peak_working_set)}",
        f"Commit:     {_fmt_bytes(r.private_bytes)} private bytes   "
        f"{r.page_faults:,} page faults",
        f"Kernel:     {r.threads} threads   {r.handles:,} handles   session {r.session}"
        f"   base priority {r.base_priority}",
    ]
    if children:
        names = ", ".join(sorted({c.name for c in children})[:6])
        lines.append(f"Children:   {len(children)} ({names})")
    if services:
        lines.append(f"Services:   {', '.join(services)}")
    flags = badges(info)
    if flags:
        lines.append("Flags:      " + ", ".join(flags))
    return "\n".join(lines)


def _why(error: Optional[str]) -> str:
    return f"unavailable ({error})" if error else "unavailable"


# ---- service map (svchost) -----------------------------------------------------

def services_by_pid() -> Optional[Dict[int, List[str]]]:
    """Which services each process is hosting, or None if it could not be read.

    Slow enough (a few hundred ms) that it runs on a worker, and only for a
    selected row -- never per refresh.
    """
    try:
        import psutil
        mapping: Dict[int, List[str]] = {}
        for svc in psutil.win_service_iter():
            try:
                pid = svc.pid()
            except Exception as e:       # a service can vanish mid-enumeration
                logging.getLogger(__name__).debug("service pid unreadable: %s", e)
                continue
            if pid:
                mapping.setdefault(pid, []).append(svc.name())
        return mapping
    except Exception:
        logging.getLogger(__name__).warning("service map unreadable", exc_info=True)
        return None
