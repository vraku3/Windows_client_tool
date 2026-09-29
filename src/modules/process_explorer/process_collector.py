"""The Process Explorer tab's snapshot, on the Dashboard's process engine.

It used to call `psutil.process_iter` with ten attributes, which the wave-1
benchmark measured at **667.9 ms** for this machine's ~270 processes against
**2.6 ms** for `NtQuerySystemInformation` -- a 257x gap that showed up in the
UI as a tab which sat empty for about five seconds after you clicked it, and
then burned two thirds of a core to stay current at 1 Hz.

The engine in `modules/dashboard/procengine/` already had everything this
pane needs, so this is now a translation layer rather than a second
collector: `SnapshotSource` reads, and each `ProcessInfo` becomes the
`ProcessNode` that `ProcessTreeModel` and the lower panes already expect.
That also fills in the GPU column, which had shown 0.0 for every process
since it was added because nothing ever wrote to it.

Two things are deliberately kept from the old behaviour: the shape of
`ProcessNode` (the tree model, the colour scheme, the properties dialog and
seven lower panes all read it) and the `{pid: node}` snapshot contract.
"""
from __future__ import annotations
import logging
import time
from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.worker import Worker
from modules.process_explorer.process_node import ProcessNode

logger = logging.getLogger(__name__)

#: Session 0 is the non-interactive session Windows reserves for services.
#: Used to classify system processes because it arrives free with the bulk
#: syscall and CANNOT be refused -- the wave-1 finding that unelevated, the
#: token classifies zero system processes by user name, so the "Windows
#: processes" group came out empty.
SERVICES_SESSION = 0

#: How long a process stays flagged as just-started or just-exited.
#: Process Explorer's own difference highlight is one second.
HIGHLIGHT_SECONDS = 1.0


def node_from_info(info, service_names: Set[str],
                   gpu: Optional[Dict[int, float]] = None,
                   kind=None) -> ProcessNode:
    """One engine `ProcessInfo` as the `ProcessNode` the pane speaks.

    The engine reports `None` for anything it could not read, which is the
    distinction the whole thing is built on. `ProcessNode` predates that and
    has non-optional fields, so the conversion has to choose -- and it
    chooses the empty string for text and 0.0 for rates, NOT to claim a
    measurement but because the widgets downstream cannot render a `None`.
    The unfilled cell is the honest rendering of "we were refused"; what
    must never happen is a refusal arriving as a plausible NUMBER, and no
    refusal here produces one.
    """
    raw, rates, details = info.raw, info.rates, info.details
    return ProcessNode(
        pid=raw.pid,
        name=raw.name or "",
        exe=details.path or "",
        cmdline=details.cmdline or "",
        user=details.user or "",
        status="suspended" if _suspended(raw) else "running",
        parent_pid=raw.ppid or 0,
        cpu_percent=rates.cpu_percent or 0.0,
        memory_rss=raw.working_set or 0,
        memory_vms=raw.virtual_size or 0,
        disk_read_bps=rates.read_bps or 0.0,
        disk_write_bps=rates.write_bps or 0.0,
        gpu_percent=(gpu or {}).get(raw.pid, 0.0),
        is_system=raw.session == SERVICES_SESSION,
        is_service=info.is_service or (raw.name or "").lower() in service_names,
        is_suspended=_suspended(raw),
        integrity_level=details.integrity or "Medium",
        # Kept Optional, unlike is_immersive/is_dotnet below: this is not a
        # row-colour input, so there is no reason to lie a refusal into
        # False. The Properties dialog shows "could not tell" for None.
        appcontainer=details.appcontainer,
        is_own=info.is_own_user,
        # `is_immersive` and `is_dotnet` are `Optional[bool]` in the engine
        # -- None where we were refused. ProcessNode has no None, and the
        # colour scheme reads them as plain booleans, so a refusal must
        # become False here. That is the honest direction: an UNCOLOURED
        # row claims nothing, where colouring one on a reading we never got
        # would be a lie told in colour.
        is_immersive=bool(kind.immersive) if kind is not None else False,
        is_dotnet=bool(kind.dotnet) if kind is not None else False,
        is_packed=bool(kind.packed.looks_packed)
        if kind is not None and kind.packed is not None else False,
        packed_entropy=kind.packed.entropy
        if kind is not None and kind.packed is not None else None,
    )


def _suspended(raw) -> bool:
    """A process with threads but no cycles is not merely idle.

    The engine's own snapshot marks this the same way; kept here rather than
    imported so the translation has one source of truth for the field.
    """
    return bool(getattr(raw, "threads", 0)) and getattr(raw, "cycles", 1) == 0


#: How many processes may have their cold details resolved per tick.
#: Elevated, a full cold sweep of this machine's ~270 processes measures
#: 2,252 ms, so an unbounded first tick leaves the pane blank for over two
#: seconds. At 60 a tick the list appears immediately with names, rates and
#: memory, and the paths and users fill in over the next four seconds --
#: which is what the two-tier design was for. Warm ticks cost 8.5 ms and
#: never come near the cap.
COLD_BUDGET_PER_TICK = 60


def build_snapshot(service_names: Set[str],
                   source=None,
                   gpu: Optional[Dict[int, float]] = None,
                   cold_budget: Optional[int] = COLD_BUDGET_PER_TICK,
                   kinds=None
                   ) -> Dict[int, ProcessNode]:
    """Every process as a `{pid: ProcessNode}` map, children linked.

    `source` is the caller's `SnapshotSource`, which must persist between
    ticks: it holds the previous sample (without which there is no rate at
    all) and the cold cache (without which every tick re-resolves 270
    paths). Passing `None` builds a throwaway one, which is correct only
    for a single reading -- every rate will be 0.0.
    """
    from core.procengine.snapshot import SnapshotSource

    from core.procengine.classify import service_pids

    if source is None:
        source = SnapshotSource()
    # Re-asked every tick: services start and stop, and 1.3 ms is far less
    # than a stale answer costs. Without this the "hosts a service"
    # category matched 0 of this machine's 114 service-hosting processes,
    # because it was comparing process names against SERVICE names.
    hosting = service_pids()
    if hosting is not None:
        source.set_service_pids(hosting)
    snapshot = source.read(cold_budget=cold_budget)

    # The category facts share the cold budget's discipline but keep their
    # own counter: the module scan is 1.69 ms a process, so an unbounded
    # first sweep of 271 of them is most of half a second on its own.
    kind_budget = None if cold_budget is None else [cold_budget]

    result: Dict[int, ProcessNode] = {}
    for pid, info in snapshot.by_pid.items():
        if pid == 0:
            continue
        kind = None
        if kinds is not None:
            kind = kinds.get(pid, info.raw.create_time,
                             info.details.path, kind_budget)
        result[pid] = node_from_info(info, service_names, gpu, kind)

    # Parent -> children, over the pids that are actually present. The
    # engine's own tree has already broken any ppid cycle (pid reuse makes
    # them, and a cycle wearing the shape of a tree never stops being
    # walked), so this only has to re-hang the same links on these nodes.
    for node in result.values():
        parent = result.get(node.parent_pid)
        if parent is not None and parent.pid != node.pid:
            parent.children.append(node)

    return result


def diff_snapshots(
    old: Dict[int, ProcessNode],
    new: Dict[int, ProcessNode],
) -> Tuple[List[int], List[int], List[int]]:
    """Return (added_pids, removed_pids, changed_pids) — all List[int].

    GPU and the disk rates are part of "changed" as well as CPU and memory.
    They were not, which was harmless while nothing wrote to them and is
    not now: a row whose ONLY movement is on the GPU would never be
    repainted, so the column would sit at whatever it read the tick some
    other number happened to change.
    """
    old_pids = set(old)
    new_pids = set(new)
    added   = list(new_pids - old_pids)
    removed = list(old_pids - new_pids)
    changed = [
        p for p in old_pids & new_pids
        if (old[p].cpu_percent != new[p].cpu_percent or
            old[p].memory_rss != new[p].memory_rss or
            old[p].gpu_percent != new[p].gpu_percent or
            old[p].disk_read_bps != new[p].disk_read_bps or
            old[p].disk_write_bps != new[p].disk_write_bps or
            old[p].status != new[p].status or
            # The highlight and the late-arriving cold details change a
            # row without any number moving. Without these, a green row
            # never fades, a row that just died never reddens, and a path
            # resolved on tick four never reaches the view at all.
            old[p].is_new != new[p].is_new or
            old[p].is_deleted != new[p].is_deleted or
            old[p].exe != new[p].exe or
            old[p].user != new[p].user)
    ]
    return added, removed, changed


class ProcessCollector(QObject):
    """Polls process list on a background Worker, diffs, and emits signals."""
    process_added     = pyqtSignal(object)   # emits ProcessNode
    process_removed   = pyqtSignal(int)      # emits pid
    processes_updated = pyqtSignal(list)     # emits List[int] changed pids
    snapshot_ready    = pyqtSignal(dict)     # emits full {pid: ProcessNode} on first load

    def __init__(self, interval_ms: int = 1000, parent=None,
                 want_packed: bool = False):
        super().__init__(parent)
        self._interval_ms = interval_ms
        #: Whether to run the packed-image heuristic. Off by default: at
        #: 4.11 ms a process it costs more than every other category fact
        #: put together, for the only answer here that can be wrong about
        #: a healthy program. Reachable rather than hardcoded, so the
        #: colour is not the dead code the old GPU tint turned out to be.
        self._want_packed = want_packed
        self._snapshot: Dict[int, ProcessNode] = {}
        self._service_names: Set[str] = set()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._thread_pool = None
        self._first = True
        self._busy = False
        #: Both of these MUST live across ticks, and for the same reason:
        #: they hold the previous reading. A fresh SnapshotSource has no
        #: rates and an empty cold cache (141 ms of re-resolution); a fresh
        #: GpuSampler has no interval, so PDH refuses its first read. Built
        #: lazily on the worker thread, because constructing them opens
        #: handles and a PDH query that a never-started collector should
        #: not be holding.
        self._source = None
        self._gpu = None
        self._kinds = None
        #: pid -> when we first saw it, for the green highlight.
        self._first_seen: Dict[int, float] = {}
        #: pid -> (node, when it vanished), for the red one. A process that
        #: has exited is not in any snapshot any more, so the row has to be
        #: held here or there is nothing left to colour.
        self._departed: Dict[int, Tuple[ProcessNode, float]] = {}

    def set_thread_pool(self, pool):
        self._thread_pool = pool

    def set_service_names(self, names: Set[str]):
        self._service_names = {n.lower() for n in names}

    def set_interval(self, ms: int):
        self._interval_ms = ms
        if self._timer.isActive():
            self._timer.setInterval(ms)

    def start(self):
        self._timer.start(self._interval_ms)
        # And read once NOW. A QTimer does not fire until its first interval
        # has elapsed, so starting it alone bought a guaranteed second of
        # empty table on the tick someone clicked the tab -- a full second
        # of the ~3.5 that made this pane feel broken.
        self._tick()

    def stop(self):
        self._timer.stop()
        self._close_gpu()

    def _close_gpu(self) -> None:
        """Release the PDH query. It lives in the performance-counter
        service rather than in this process, so an abandoned one outlives
        the pane that opened it."""
        if self._gpu is not None:
            self._gpu.close()
            self._gpu = None

    def _tick(self):
        if self._thread_pool is None or self._busy:
            return
        self._busy = True
        service_names = self._service_names

        def do_work(worker):
            if self._source is None:
                from core.procengine.snapshot import \
                    SnapshotSource
                self._source = SnapshotSource()
            if self._gpu is None:
                from core.procengine.gpuinfo import GpuSampler
                self._gpu = GpuSampler()
            if self._kinds is None:
                from core.procengine.classify import \
                    ClassifyCache
                self._kinds = ClassifyCache(want_packed=self._want_packed)
            # Collect once, then read the per-pid slice of that same
            # collection: sampling twice would halve each interval and
            # report GPU figures for a window that does not line up with
            # the CPU and disk rates on the same row.
            self._gpu.sample()
            gpu = self._gpu.process_usage()
            return build_snapshot(service_names, source=self._source,
                                  gpu=gpu, kinds=self._kinds)

        def _on_error(e: str) -> None:
            logger.error("ProcessCollector error: %s", e)
            self._busy = False

        w = Worker(do_work)
        w.signals.result.connect(self._on_snapshot)
        w.signals.error.connect(_on_error)
        self._thread_pool.start(w)

    def _on_snapshot(self, new_snapshot: Dict[int, ProcessNode]):
        self._busy = False
        if self._first:
            # Nothing on the FIRST snapshot is new: 270 processes were all
            # running before this pane opened, and flashing every one of
            # them green says the machine just booted.
            self._first_seen = {pid: 0.0 for pid in new_snapshot}
            self._snapshot = new_snapshot
            self._first = False
            self.snapshot_ready.emit(new_snapshot)
            return

        new_snapshot = self._mark_transient(new_snapshot)
        added, removed, changed = diff_snapshots(self._snapshot, new_snapshot)
        self._snapshot = new_snapshot
        for pid in added:
            node = new_snapshot.get(pid)
            if node:
                self.process_added.emit(node)
        for pid in removed:
            self.process_removed.emit(pid)
        if changed:
            self.processes_updated.emit(changed)

    def _mark_transient(self, snapshot: Dict[int, ProcessNode]
                        ) -> Dict[int, ProcessNode]:
        """Flag what just started, and hold what just exited.

        Process Explorer flashes a new process green and a dead one red for
        a moment. The green half is a timestamp. The red half is harder,
        and it is why this returns a NEW mapping: an exited process is in
        no snapshot any more, so the only way to colour its row is to keep
        the row -- it is re-inserted here, marked, and allowed to fall out
        one tick after the highlight expires.
        """
        now = time.monotonic()
        window = HIGHLIGHT_SECONDS

        for pid in snapshot:
            self._first_seen.setdefault(pid, now)
        # A pid that came back is a NEW process, not the old one returning:
        # this drops the departed row so its successor gets its own life.
        for pid in list(self._departed):
            if pid in snapshot:
                del self._departed[pid]
        for pid, node in self._snapshot.items():
            if pid not in snapshot and pid not in self._departed:
                self._departed[pid] = (node, now)

        merged = dict(snapshot)
        for pid, (node, went) in list(self._departed.items()):
            if now - went > window:
                del self._departed[pid]
                continue
            node.is_deleted = True
            node.is_new = False
            merged[pid] = node

        for pid, node in snapshot.items():
            node.is_new = (now - self._first_seen.get(pid, now)) <= window

        live = set(snapshot)
        self._first_seen = {pid: seen for pid, seen in self._first_seen.items()
                            if pid in live}
        return merged

    def get_snapshot(self) -> Dict[int, ProcessNode]:
        return self._snapshot
