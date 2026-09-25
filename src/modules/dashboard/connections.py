"""Every TCP/UDP endpoint on the machine and the process that owns it. No Qt.

`netstat -b` for people who do not want to wait for it: the same owner-per-
socket answer, live, filterable, and joined to the process name so "who is
talking to that address" is one glance. A failed read is `None` -- never an
empty list, which would read as "this machine has no connections".
"""
import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

LISTEN, ESTABLISHED = "LISTEN", "ESTABLISHED"


@dataclass(frozen=True)
class Connection:
    proto: str            # "TCP4" "TCP6" "UDP4" "UDP6"
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int
    state: str            # "LISTEN", "ESTABLISHED", ... or "" for UDP
    pid: int
    process: str

    @property
    def local(self) -> str:
        return endpoint(self.local_ip, self.local_port)

    @property
    def remote(self) -> str:
        return endpoint(self.remote_ip, self.remote_port) if self.remote_ip else ""

    @property
    def key(self) -> Tuple:
        return (self.proto, self.local, self.remote, self.pid)


def endpoint(ip: str, port: int) -> str:
    if not ip:
        return ""
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def _proto(kind, family) -> str:
    base = "TCP" if kind == socket.SOCK_STREAM else "UDP"
    return base + ("6" if family == socket.AF_INET6 else "4")


def read_connections(name_of: Optional[Callable[[int], str]] = None) -> Optional[List[Connection]]:
    """All inet sockets, or None if Windows refused the table."""
    import psutil
    try:
        raw = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError) as e:
        logger.warning("net_connections refused: %s", e)
        return None
    names: Dict[int, str] = {}
    found = []
    for c in raw:
        pid = c.pid or 0
        if pid not in names:
            names[pid] = (name_of(pid) if name_of else _process_name(pid))
        state = "" if c.type == socket.SOCK_DGRAM else (c.status or "")
        found.append(Connection(
            proto=_proto(c.type, c.family),
            local_ip=c.laddr.ip if c.laddr else "", local_port=c.laddr.port if c.laddr else 0,
            remote_ip=c.raddr.ip if c.raddr else "", remote_port=c.raddr.port if c.raddr else 0,
            state=state, pid=pid, process=names[pid]))
    return found


def _process_name(pid: int) -> str:
    if pid == 0:
        return "System Idle / kernel"
    import psutil
    try:
        return psutil.Process(pid).name()
    except psutil.NoSuchProcess:
        return "(exited)"
    except psutil.Error as e:
        logger.debug("process %s name refused: %s", pid, e)
        return ""


# ---- judgements --------------------------------------------------------------------

def is_external(ip: str) -> bool:
    """A remote address that is on the internet, not loopback / LAN / link-local."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_unspecified)


def _listening(c: Connection) -> bool:
    return c.state == LISTEN or (c.proto.startswith("UDP") and not c.remote_ip)


FILTERS: Tuple[Tuple[str, str, Callable[[Connection], bool]], ...] = (
    ("all", "All", lambda c: True),
    ("listening", "Listening", _listening),
    ("established", "Established", lambda c: c.state == ESTABLISHED),
    ("external", "Internet", lambda c: c.state == ESTABLISHED and is_external(c.remote_ip)),
    ("tcp", "TCP", lambda c: c.proto.startswith("TCP")),
    ("udp", "UDP", lambda c: c.proto.startswith("UDP")),
)
_BY_KEY = {k: fn for k, _, fn in FILTERS}


def passes(filter_key: str, c: Connection) -> bool:
    return _BY_KEY.get(filter_key, _BY_KEY["all"])(c)


def filter_counts(conns: Iterable[Connection]) -> Dict[str, int]:
    conns = list(conns)
    return {k: sum(1 for c in conns if fn(c)) for k, _, fn in FILTERS}


def matches(c: Connection, needle: str) -> bool:
    needle = needle.strip().lower()
    if not needle:
        return True
    hay = f"{c.process} {c.local} {c.remote} {c.state} {c.proto} {c.pid}".lower()
    return needle in hay


def visible(conns: Iterable[Connection], filter_key: str, needle: str) -> List[Connection]:
    return [c for c in conns if passes(filter_key, c) and matches(c, needle)]


def by_process(conns: Iterable[Connection]) -> List[Tuple[str, int, int]]:
    """(process, pid, connection count), busiest first: who is holding the sockets."""
    counts: Dict[Tuple[str, int], int] = {}
    for c in conns:
        counts[(c.process, c.pid)] = counts.get((c.process, c.pid), 0) + 1
    return sorted(((n, p, k) for (n, p), k in counts.items()), key=lambda r: -r[2])
