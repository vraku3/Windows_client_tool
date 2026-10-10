from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ProcessNode:
    pid: int
    name: str
    exe: str
    cmdline: str
    user: str
    status: str          # running | sleeping | stopped | zombie
    parent_pid: int
    children: List['ProcessNode'] = field(default_factory=list)

    # Real-time metrics
    cpu_percent: float = 0.0
    memory_rss: int = 0      # bytes
    memory_vms: int = 0
    disk_read_bps: float = 0.0
    disk_write_bps: float = 0.0
    net_send_bps: float = 0.0
    net_recv_bps: float = 0.0
    gpu_percent: float = 0.0

    # Classification (set once, stable per process lifetime)
    is_system: bool = False
    is_service: bool = False
    is_dotnet: bool = False
    is_suspended: bool = False
    integrity_level: str = "Medium"  # Low | Medium | High | System
    #: Whether the process token is an AppContainer token -- a sandbox
    #: boundary, distinct from `is_immersive` (package identity). `None`
    #: means the token could not be read (unelevated, most processes that
    #: are not ours). See `core.procengine.details._token_appcontainer`.
    appcontainer: Optional[bool] = None

    # Process Explorer's remaining row categories.
    #: Runs as the user we are, which is the distinction that makes a
    #: process list readable at a glance.
    is_own: bool = False
    #: Has an AppX package identity -- a Store/packaged app.
    is_immersive: bool = False
    #: The image LOOKS compressed. A heuristic, hence the entropy beside
    #: it: see procengine/classify.py for what it gets wrong.
    is_packed: bool = False
    #: `core.procengine.mitigations.MitigationReport`, read once per process
    #: (pid + start time) for the optional Protection/DEP/ASLR/CFG columns.
    #: None until it has been read.
    mitigations: Optional[object] = None
    packed_entropy: Optional[float] = None
    #: Appeared, or vanished, within the highlight window. Transient --
    #: these are the only two fields here that are not stable for the
    #: life of the process.
    is_new: bool = False
    is_deleted: bool = False
    #: FILETIME the process started; 0 where unknown. What tells a real
    #: parent from a later process that merely reuses its pid.
    create_time: int = 0

    # VirusTotal (populated on demand)
    sha256: Optional[str] = None
    vt_score: Optional[str] = None   # e.g. "3/72"


#: The System process. Its pid is fixed and never reused, and on this
#: machine it reports a create time LATER than its own child Registry
#: (pid 464) -- so the create-time test below would orphan Registry.
SYSTEM_PID = 4


def link_children(snapshot: Dict[int, "ProcessNode"]) -> List["ProcessNode"]:
    """Rebuild every node's `children` from `parent_pid`; return the roots.

    The model used to keep whatever children lists the FIRST tick built.
    Every later start and exit went through `load_snapshot` with those
    stale lists, so -- measured on this machine over six seconds of the
    live pane -- a `ping.exe` this process had just started sat in the
    snapshot under no parent at all (invisible in the tree), while three
    exited processes (conhost.exe, git.exe x2) were still drawn as rows:
    354 processes, 346 reachable rows.

    A ppid is believed only if that parent started before the child: a
    dead parent's pid reused by an unrelated process is not the parent,
    and hanging the child under it is how a tree tells an incident
    responder that, say, notepad spawned a shell. And no ppid chain may
    loop -- pid reuse can make two processes each other's parent, which
    would leave both unreachable from any root.
    """
    for node in snapshot.values():
        node.children = []
    parent_of: Dict[int, Optional[int]] = {}
    for pid, node in snapshot.items():
        parent_of[pid] = _believed_parent(node, snapshot)
    _cut_cycles(parent_of)
    roots: List[ProcessNode] = []
    for pid, node in snapshot.items():
        parent = parent_of[pid]
        if parent is None:
            roots.append(node)
        else:
            snapshot[parent].children.append(node)
    return roots


def _believed_parent(node: "ProcessNode", snapshot) -> Optional[int]:
    parent = snapshot.get(node.parent_pid)
    if parent is None or parent is node:
        return None
    if (parent.pid != SYSTEM_PID and parent.create_time and node.create_time
            and parent.create_time > node.create_time):
        return None
    return parent.pid


def _cut_cycles(parent_of: Dict[int, Optional[int]]) -> None:
    state: Dict[int, int] = {}          # 1 = on the current walk, 2 = done
    for start in list(parent_of):
        path = []
        pid: Optional[int] = start
        while pid is not None and pid not in state:
            state[pid] = 1
            path.append(pid)
            pid = parent_of.get(pid)
        if pid is not None and state.get(pid) == 1:
            parent_of[pid] = None
        for visited in path:
            state[visited] = 2
