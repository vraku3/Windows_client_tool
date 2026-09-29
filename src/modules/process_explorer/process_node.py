from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional


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
    packed_entropy: Optional[float] = None
    #: Appeared, or vanished, within the highlight window. Transient --
    #: these are the only two fields here that are not stable for the
    #: life of the process.
    is_new: bool = False
    is_deleted: bool = False

    # VirusTotal (populated on demand)
    sha256: Optional[str] = None
    vt_score: Optional[str] = None   # e.g. "3/72"
