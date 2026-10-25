r"""Find which process has a file, key or DLL open -- Process Explorer's Ctrl+F.

The question this answers is the one that makes a process explorer worth
having: *what is holding this file open?* It is asked about a path that
cannot be deleted, a registry key that will not budge, a DLL that should
have been unloaded.

Measured on this machine, and the reason the search is shaped the way it
is:

| Step | Cost | Yield |
|---|---|---|
| enumerate every handle | 143 ms | 164,173 handles, 266 processes |
| name all of them | ~0.5 s | most objects have no name at all |
| read every process's modules | 1,273 ms | 15,391 modules, 21 refused |

So the whole sweep is about two seconds. That is fast enough to be worth
doing on demand and far too slow to do on a keystroke, which is why this
is a function a caller runs on a worker with `should_stop`, not a filter
over something already in memory.

**Both halves report what they could not look at.** Twenty-one processes
refuse their module list outright, and unelevated far more refuse handle
duplication. A search that quietly skipped them would answer "nothing has
that file open" when the honest answer is "nothing I was allowed to look
at has it open" -- and for this particular question, that difference is
the whole point of asking.

Qt-free, like the rest of the engine.
"""
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .handles import HandleNamer, system_handles
from .modinfo import loaded_modules
from .ntquery import system_processes

logger = logging.getLogger(__name__)

#: Per-process naming deadline, SCALED to how much there is to name.
#:
#: A flat limit is wrong in both directions: 0.25s is luxurious for a
#: process with twenty handles and too tight for one with four hundred on
#: a loaded machine -- and when it trips, that process loses every name
#: after the cut. It cost real debugging: a search for a file THIS process
#: had open kept coming back empty, because our own handle-heavy process
#: was the one being cut off.
#:
#: Naming measures ~0.003 ms a handle, so 2 ms each is ~600x headroom --
#: enough that reaching the deadline still means blocked, not slow.
FIND_DEADLINE_MIN = 0.25
FIND_DEADLINE_MAX = 2.0
FIND_DEADLINE_PER_HANDLE = 0.002


def deadline_for(count: int) -> float:
    """How long naming `count` handles may take before we call it blocked."""
    return min(FIND_DEADLINE_MAX,
               max(FIND_DEADLINE_MIN, count * FIND_DEADLINE_PER_HANDLE))

#: And an overall budget, which is the one that actually bounds the search.
#:
#: Extrapolating from a sample said the whole sweep would take 0.5 s. It
#: took **20 s**, because the cost is dominated by the processes that
#: BLOCK, and a sample of readable ones contains none of them. A per-item
#: deadline is not a bound on a loop over items; only a total is.
FIND_BUDGET_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class Match:
    """One thing found, and where."""

    pid: int
    process: str
    kind: str          # "Handle" or "DLL"
    type_name: str     # "File", "Key", "Section", or "Module"
    detail: str        # the name or path that matched
    handle: Optional[int] = None


@dataclass
class FindReport:
    """What was found, and what could not be looked at.

    The second half is not a footnote. "Nothing has that file open" and
    "nothing I was allowed to look at has it open" are different answers
    to the question this search exists to ask.
    """

    matches: List[Match] = field(default_factory=list)
    searched_processes: int = 0
    refused_handles: int = 0
    refused_modules: int = 0
    stopped_early: bool = False
    note: Optional[str] = None
    #: Processes whose naming actually blocked, as opposed to processes
    #: we were simply not allowed to open. Counted apart because one is a
    #: permission and the other is the driver limit.
    blocked_handles: int = 0
    #: How many processes the sweep never reached. Counted separately from
    #: the refusals because it is a different kind of gap -- one is "not
    #: allowed", the other is "ran out of time" -- and because it is the
    #: number that tells someone to re-run with a longer budget.
    unsearched: int = 0

    @property
    def refused_any(self) -> bool:
        return bool(self.refused_handles or self.refused_modules
                    or self.blocked_handles)

    def summary(self) -> str:
        if self.stopped_early:
            lead = f"Stopped with {len(self.matches):,} matches"
        else:
            lead = (f"{len(self.matches):,} matches in "
                    f"{self.searched_processes:,} processes")
        if not self.refused_any and not self.unsearched:
            return lead
        parts = []
        if self.unsearched:
            parts.append(f"{self.unsearched} were never reached")
        if self.refused_modules:
            parts.append(f"{self.refused_modules} refused their module list")
        if self.refused_handles:
            parts.append(f"{self.refused_handles} refused handle inspection")
        if self.blocked_handles:
            parts.append(f"{self.blocked_handles} had a handle query block")
        return (f"{lead} · {', '.join(parts)} — so this is what could be "
                f"searched, not necessarily everything")


def find(text: str,
         handles: bool = True,
         modules: bool = True,
         should_stop: Optional[Callable[[], bool]] = None,
         progress: Optional[Callable[[int, int], None]] = None,
         budget_seconds: float = FIND_BUDGET_SECONDS,
         process_snapshot=None
         ) -> FindReport:
    """Every handle name and module path containing `text`.

    Case-insensitive substring, which is what the question wants: someone
    searching for `report.xlsx` should not have to know it is held as
    `\\Device\\HarddiskVolume3\\Users\\...\\report.xlsx`.

    `budget_seconds` bounds the WHOLE sweep. Necessary rather than tidy:
    with only a per-process deadline this took 20 seconds on this machine,
    because a handful of processes block and each burns its own limit.
    """
    # Callers correlating many files can reuse one process snapshot for both
    # this handle sweep and the creator heuristic. Default callers are unchanged.
    import time as _time

    report = FindReport()
    needle = (text or "").strip().lower()
    if not needle:
        report.note = "Enter something to search for."
        return report

    snapshot = system_processes() if process_snapshot is None else process_snapshot
    names: Dict[int, str] = {row.pid: row.name for row in snapshot}
    report.searched_processes = len(names)
    caller_stop = should_stop or (lambda: False)
    deadline = _time.monotonic() + budget_seconds
    out_of_time = [False]

    def stop() -> bool:
        if caller_stop():
            return True
        if _time.monotonic() >= deadline:
            out_of_time[0] = True
            return True
        return False

    if handles:
        _find_handles(needle, names, report, stop, progress)
    if modules and not report.stopped_early:
        _find_modules(needle, names, report, stop, progress)

    if report.stopped_early:
        report.note = (
            f"The search ran out of its {budget_seconds:.0f}s budget before "
            f"finishing, so this is a partial answer."
            if out_of_time[0] else
            "The search was stopped before it finished.")
    return report


def _find_handles(needle, names, report, stop, progress) -> None:
    try:
        entries = system_handles()
    except Exception as error:  # noqa: BLE001
        logger.warning("The handle table could not be read: %s", error)
        report.refused_handles += 1
        return

    grouped: Dict[int, list] = {}
    for entry in entries:
        grouped.setdefault(entry.pid, []).append(entry)

    namer = HandleNamer()
    # Fewest handles first. Truncation is then paid by the processes that
    # were going to be slowest anyway, instead of falling on whoever
    # happens to sort last -- which, in pid order, is always the most
    # RECENTLY STARTED process, i.e. exactly the one someone searching for
    # their own just-locked file is looking for.
    order = sorted(grouped.items(), key=lambda item: len(item[1]))
    total = len(order)
    for index, (pid, group) in enumerate(order):
        if stop():
            report.stopped_early = True
            report.unsearched = max(report.unsearched, total - index)
            return
        if progress is not None:
            progress(index, total)
        rows, note = namer.describe(group,
                                    deadline=deadline_for(len(group)))
        if note:
            # Two very different gaps, and counting them together made the
            # numbers meaningless: 17 "refusals" turned out to be 16
            # processes we simply cannot open and ONE whose name query
            # actually blocked.
            if note.startswith("BLOCKED"):
                report.blocked_handles += 1
            else:
                report.refused_handles += 1
        for row in rows:
            if row.name and needle in row.name.lower():
                report.matches.append(Match(
                    pid=pid, process=names.get(pid, f"pid {pid}"),
                    kind="Handle", type_name=row.type_name or "?",
                    detail=row.name, handle=row.value))


def _find_modules(needle, names, report, stop, progress) -> None:
    pids = sorted(names)
    total = len(pids)
    for index, pid in enumerate(pids):
        if stop():
            report.stopped_early = True
            report.unsearched = max(report.unsearched, total - index)
            return
        if progress is not None:
            progress(index, total)
        # No version pass: a search does not need the company, and it is
        # the expensive half of reading a module list.
        found, _reason = loaded_modules(pid, with_version=False)
        if found is None:
            report.refused_modules += 1
            continue
        for module in found:
            if needle in module.path.lower() or needle in module.name.lower():
                report.matches.append(Match(
                    pid=pid, process=names.get(pid, f"pid {pid}"),
                    kind="DLL", type_name="Module", detail=module.path))
