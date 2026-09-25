"""Process identity: a PID alone is not one.

Windows reuses PIDs. A row that says "PID 4120 used 90% CPU" is a lie if 4120
was a different program a minute ago, and a log of "PID 4120" is worse. The
identity of a process is (PID, creation time). This watches consecutive
snapshots and reports every PID that reappears under a different creation
time, so the Processes tab can say "this PID was reused" instead of silently
splicing two programs' histories together. No Qt.
"""
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Tuple


@dataclass(frozen=True)
class Recycled:
    pid: int
    old_name: str
    new_name: str
    seen_at: float


def identity(info) -> Tuple[int, int]:
    return info.pid, info.raw.create_time


class RecycleWatch:
    """Feed it every snapshot; it remembers which (pid -> creation, name) it saw."""

    def __init__(self, keep: int = 50) -> None:
        self._seen: Dict[int, Tuple[int, str]] = {}
        self.events: Deque[Recycled] = deque(maxlen=keep)
        self.total = 0

    def update(self, snapshot) -> List[Recycled]:
        found: List[Recycled] = []
        now = getattr(snapshot, "taken_at", 0.0)
        for pid, info in snapshot.by_pid.items():
            if pid <= 4:                      # System / Idle: creation time is not meaningful
                continue
            created = info.raw.create_time
            previous = self._seen.get(pid)
            if previous is not None and previous[0] != created:
                event = Recycled(pid, previous[1], info.name, now)
                found.append(event)
                self.events.append(event)
                self.total += 1
            self._seen[pid] = (created, info.name)
        return found

    def recently_reused(self, pid: int, within_s: float, now: float) -> bool:
        return any(e.pid == pid and now - e.seen_at <= within_s for e in self.events)
