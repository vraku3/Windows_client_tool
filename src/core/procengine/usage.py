r"""App history: how much CPU each program has cost since it started.

Task Manager's App history tab answers "what has eaten the machine" -- not
what is eating it right now (that is the Details and Performance tabs) but
what has ADDED UP since each program began. That is a different question
from the live rates, and it needs cumulative numbers, not deltas.

**The honest data is CPU time.** Every process in the bulk syscall carries
kernel + user time as cumulative counters (`ntquery`), so per-program CPU
time is real, readable, and needs no privilege. An "app" is identified the
way the Processes tab identifies one -- by its image path where readable,
its name where the path was refused -- so Chrome's twenty-six processes sum
into one row the person can reason about.

**What this pane does NOT show is named, not faked.** Task Manager's App
history also lists network used, metered-network used and tile updates.
None of that is reachable from a public API: per-process network byte
counters require a kernel driver (the same driver Process Explorer ships
for its handle and job views), and tile updates exist only for UWP
packages whose usage store Windows keeps behind its own private table. A
column of zeros would read as "this app used no network", which is a lie
this pane is not allowed to tell. So those columns are simply not there,
and the module says why instead of pretending.

Qt-free, like the rest of the engine.
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AppUsage:
    """One program's accumulated cost since it started.

    `name` is the description where the process carries one ("Google
    Chrome"), else the image name -- the same choice the Processes tab
    makes. `cpu_ticks` is kernel + user time in 100ns units, summed across
    every process of the program, which is the number that answers "what
    has cost the most".
    """

    name: str
    cpu_ticks: int
    process_count: int
    path: Optional[str] = None
    started_earliest: int = 0


def app_usage(snapshot) -> List[AppUsage]:
    """Roll a snapshot's processes up into per-program cumulative usage.

    Returns the list sorted by CPU time, most expensive first -- the
    question the tab exists to answer, so it is already in the order the
    user wants and no widget has to re-sort it.

    An unreadable path files the process under its name; an unknown
    process never becomes a separate unnamed row.
    """
    buckets: Dict[str, AppUsage] = {}
    for info in snapshot.by_pid.values():
        # PID 0 is the idle "process": its CPU time is the time nothing ran,
        # so it always topped the list with days of "usage".
        if info.pid == 0:
            continue
        name = getattr(info.details, "description", None) or info.name
        path = getattr(info.details, "path", None)
        key = (path or name).lower()
        usage = buckets.get(key)
        ticks = getattr(info.raw, "kernel_time", 0) + \
            getattr(info.raw, "user_time", 0)
        if usage is None:
            buckets[key] = AppUsage(
                name=name, cpu_ticks=ticks, process_count=1, path=path,
                started_earliest=getattr(info.raw, "create_time", 0))
        else:
            buckets[key] = AppUsage(
                name=usage.name, cpu_ticks=usage.cpu_ticks + ticks,
                process_count=usage.process_count + 1,
                path=usage.path or path,
                started_earliest=min(
                    usage.started_earliest,
                    getattr(info.raw, "create_time", 0)))
    return sorted(buckets.values(), key=lambda usage: usage.cpu_ticks,
                  reverse=True)
