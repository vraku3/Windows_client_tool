"""One reading of the machine, joined and shaped.

Brings the three sources together -- the bulk syscall (`ntquery`), the rate
maths (`rates`) and the cold cache (`details`) -- and builds the parent/child
tree the process view draws.

The tree is the interesting part. A parent pid is only a NUMBER: the parent
may be long dead with its pid reused by something unrelated. Joining on the
number alone is how a process tree ends up claiming Notepad started sixty
services. So a parent link is accepted only when the parent was created
BEFORE the child -- the same test Process Explorer applies.

Qt-free, like the rest of the engine.
"""
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .details import DetailCache, ProcessDetails
from .ntquery import ProcessRaw, system_processes
from .rates import Rates, RateTracker


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """One process, from all three sources.

    Composed rather than flattened into forty fields: each source keeps its
    own shape, so it stays obvious which reading a value came from and which
    ones can be `None` because we were refused.
    """

    raw: ProcessRaw
    rates: Rates
    details: ProcessDetails
    is_service: bool = False
    is_own_user: bool = False

    @property
    def pid(self) -> int:
        return self.raw.pid

    @property
    def name(self) -> str:
        return self.raw.name


@dataclass
class TreeNode:
    pid: int
    info: Optional[ProcessInfo] = None
    children: Tuple = ()


@dataclass
class Snapshot:
    by_pid: Dict[int, ProcessInfo] = field(default_factory=dict)
    roots: List[TreeNode] = field(default_factory=list)
    taken_at: float = 0.0
    #: How many processes gave up their cold details, and how many refused.
    #: Unelevated that is roughly half and half on a normal machine, and the
    #: pane needs to be able to say so rather than showing blank cells.
    readable: int = 0
    refused: int = 0


def details_unreadable(info: ProcessInfo) -> bool:
    """Kernel pseudo-processes have no image path; their rows remain listed."""
    return info.pid not in (0, 4) and info.details.path is None


def build_tree(rows) -> List[TreeNode]:
    """The parent/child tree over `rows`.

    Iterative, not recursive: pid reuse can produce a cycle (two processes
    each naming the other as parent), and a recursive walk would blow the
    stack on a machine that is merely unlucky.
    """
    by_pid = {row.pid: row for row in rows}
    parent_of = {row.pid: _real_parent(row, by_pid) for row in rows}
    _break_cycles(parent_of)

    children: Dict[int, List[int]] = {pid: [] for pid in parent_of}
    roots: List[int] = []
    for pid, parent in parent_of.items():
        if parent is None:
            roots.append(pid)
        else:
            children[parent].append(pid)

    nodes = {pid: TreeNode(pid=pid) for pid in parent_of}
    _attach(nodes, children)
    return [nodes[pid] for pid in sorted(roots)]


def _break_cycles(parent_of: Dict[int, Optional[int]]) -> None:
    """Cut one link in every parent cycle, in place.

    Promoting a cycle's members to roots is not enough -- they would each
    still be listed as the other's child, and the result is a structure that
    is a cycle rather than a tree. Walking it never terminates: the test for
    this hung rather than failing.

    So the link is severed. Each node is walked up its parent chain once; if
    the chain re-enters a node still being visited, that node becomes a root.
    O(n), and it terminates by construction.
    """
    state: Dict[int, str] = {}
    for start in list(parent_of):
        if start in state:
            continue
        path = []
        node = start
        while node is not None and node not in state:
            state[node] = "visiting"
            path.append(node)
            node = parent_of.get(node)
        if node is not None and state.get(node) == "visiting":
            parent_of[node] = None
        for pid in path:
            state[pid] = "done"


def _real_parent(row, by_pid) -> Optional[int]:
    """The pid of `row`'s parent, or None if it has none we believe.

    Three ways a ppid is not a parent, and all three happen on a real
    machine within minutes of booting.
    """
    parent_pid = row.ppid
    if parent_pid == row.pid:
        # Pid 0 names itself. Left alone this is an infinite loop.
        return None
    parent = by_pid.get(parent_pid)
    if parent is None:
        # The parent exited: an orphan, shown at the top level.
        return None
    if parent.create_time > row.create_time:
        # The pid was reused. Whatever holds it now started after this
        # process did, so it cannot be its parent.
        return None
    return parent_pid


def _attach(nodes, children) -> None:
    """Fill in each node's children, ordered by pid.

    Stable order matters: without it rows reshuffle between refreshes and
    jump under the cursor as someone is trying to click one.
    """
    for pid, node in nodes.items():
        node.children = tuple(nodes[child]
                              for child in sorted(children.get(pid, ())))


class SnapshotSource:
    """Reads the machine, holding what has to persist between readings.

    Two things do: the previous sample, without which there is no rate, and
    the cold cache, without which every tick pays 141 ms to re-resolve
    processes that have not changed.
    """

    def __init__(self, cores: Optional[int] = None) -> None:
        self._rates = RateTracker(cores=cores)
        self._details = DetailCache()
        self._service_pids: Set[int] = set()
        self._own_user = _current_user()

    def set_service_pids(self, pids: Set[int]) -> None:
        """Which pids host a service. Supplied from outside because the
        service list is its own slow query and does not belong on this
        path."""
        self._service_pids = set(pids)

    def read(self, cold_budget: Optional[int] = None) -> Snapshot:
        """One reading of the machine.

        `cold_budget` caps how many processes may have their cold details
        resolved on THIS call; the rest come back unresolved and are picked
        up by later calls. `None`, the default, resolves everything, which
        is what a pane wants when it can afford one slow first tick.

        A cap is worth having because the cold sweep's cost turns on
        privilege: unelevated, half the processes refuse at once and the
        sweep is ~141 ms, but elevated nothing refuses and the same sweep
        measures **2,252 ms** against 8.5 ms once warm. A live pane pays
        that on the tick it is opened, which is the tick someone is
        watching.
        """
        now = time.monotonic()
        rows = system_processes()
        rates = self._rates.update(rows, now=now)

        live = {row.pid for row in rows}
        self._details.retain(live)

        budget = None if cold_budget is None else [cold_budget]
        by_pid = {}
        readable = 0
        for row in rows:
            details = self._details.get(row.pid, row.create_time, budget)
            if details.path is not None:
                readable += 1
            by_pid[row.pid] = ProcessInfo(
                raw=row,
                rates=rates.get(row.pid, Rates()),
                details=details,
                is_service=row.pid in self._service_pids,
                is_own_user=self._is_ours(details),
            )

        tree = build_tree(rows)
        _link(tree, by_pid)
        return Snapshot(by_pid=by_pid, roots=tree, taken_at=now,
                        readable=readable,
                        refused=sum(details_unreadable(info) for info in by_pid.values()))

    def _is_ours(self, details: ProcessDetails) -> bool:
        if not details.user or not self._own_user:
            # Refused, so we cannot claim it is ours. Not the same as False
            # meaning "definitely someone else's", but the view only needs
            # "highlight the ones we know are ours".
            return False
        return details.user.lower() == self._own_user.lower()


def _link(nodes, by_pid) -> None:
    stack = list(nodes)
    while stack:
        node = stack.pop()
        node.info = by_pid.get(node.pid)
        stack.extend(node.children)


def _current_user() -> Optional[str]:
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME")
    if not user:
        return None
    return f"{domain}\\{user}" if domain else user


def descendants_of(pid: int, rows=None) -> List[int]:
    """Every process genuinely descended from `pid`.

    Walks the same create-time-validated parent link the tree view uses, so
    "End process tree" and the tree someone is looking at can never disagree
    about who is a descendant. `psutil.children(recursive=True)` joins on the
    ppid number alone; on this machine the two agree, but only the validated
    walk is guaranteed to, and the one place it would diverge -- a dead
    parent's pid reused by an unrelated process -- is a list of processes to
    kill.
    """
    if rows is None:
        rows = system_processes()
    by_pid = {row.pid: row for row in rows}
    parent_of = {row.pid: _real_parent(row, by_pid) for row in rows}
    _break_cycles(parent_of)

    children: Dict[int, List[int]] = {}
    for child, parent in parent_of.items():
        if parent is not None:
            children.setdefault(parent, []).append(child)

    found: List[int] = []
    seen = {pid}
    stack = list(children.get(pid, ()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        found.append(current)
        stack.extend(children.get(current, ()))
    return sorted(found)
