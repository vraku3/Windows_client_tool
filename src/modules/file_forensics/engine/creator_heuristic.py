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
    started_at: datetime
    delta_seconds: float


def _filetime_to_datetime(value: int) -> Optional[datetime]:
    if not value:
        return None
    try:
        return (_FILETIME_EPOCH + timedelta(microseconds=value // 10)).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def find_creator_candidates(created_at: datetime,
                            tolerance_seconds: float = 5.0
                            ) -> List[CreatorCandidate]:
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
        ))
    candidates.sort(key=lambda c: abs(c.delta_seconds))
    return candidates
