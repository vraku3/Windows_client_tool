"""Guess which process created a file, by correlating the file's creation
time against every process's start time -- the same heuristic
Find-FileCreator.ps1 used manually, on this app's own fast process
engine instead of one-at-a-time Get-Process calls.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from core.procengine.details import resolve
from core.procengine.ntquery import system_processes

#: Same conversion procengine/columns.py already uses for display --
#: reused here for arithmetic, since Windows FILETIME is 100ns ticks
#: since 1601, not a Python timestamp.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class CreatorCandidate:
    pid: int
    name: str
    path: Optional[str]
    user: Optional[str]
    #: LOCAL time (system timezone), matching `created_at`'s expected
    #: convention -- see `_filetime_to_datetime`.
    started_at: datetime
    delta_seconds: float
    path_error: Optional[str] = None
    user_error: Optional[str] = None


def _filetime_to_datetime(value: int) -> Optional[datetime]:
    """Convert a raw FILETIME to a naive LOCAL-time datetime.

    Matches `procengine/columns.py`'s `fmt_start_time` convention exactly:
    build the UTC instant, then `.astimezone()` to the system's local
    timezone, THEN drop tzinfo -- never strip tzinfo straight off the UTC
    value, or the wall-clock digits stay UTC while everything this is
    compared against (`find_creator_candidates`'s `created_at`, and Task 6's
    file-metadata `created` from `datetime.fromtimestamp`) is local time.
    """
    if not value:
        return None
    try:
        stamp = _FILETIME_EPOCH + timedelta(microseconds=value // 10)
        return stamp.astimezone().replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def find_creator_candidates(created_at: datetime,
                            tolerance_seconds: float = 5.0
                            ) -> List[CreatorCandidate]:
    """Find processes whose start time is close to `created_at`.

    `created_at` is expected to be a naive LOCAL-time datetime (the same
    convention `_filetime_to_datetime` produces for `started_at`, and the
    convention Task 6's file-metadata `created` -- from
    `datetime.fromtimestamp(st.st_ctime)` -- already uses).
    """
    candidates = []
    for proc in system_processes():
        started = _filetime_to_datetime(proc.create_time)
        if started is None:
            continue
        delta = (started - created_at).total_seconds()
        if abs(delta) > tolerance_seconds:
            continue
        details = resolve(proc.pid)
        candidates.append(CreatorCandidate(
            pid=proc.pid, name=proc.name, path=details.path,
            user=details.user, started_at=started, delta_seconds=delta,
            path_error=details.path_error, user_error=details.user_error,
        ))
    candidates.sort(key=lambda c: abs(c.delta_seconds))
    return candidates
