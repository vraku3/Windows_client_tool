"""Process Network Connections & Sockets Matrix.

Maps live TCP/UDP socket connections to active Process IDs (PIDs) and executables.
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SocketConnection:
    pid: int
    process_name: str
    protocol: str       # "TCP" or "UDP"
    local_address: str  # "192.168.3.84:63284"
    remote_address: str # "104.18.32.47:443"
    status: str         # "ESTABLISHED", "LISTEN", "TIME_WAIT"


@dataclass
class ProcessNetworkSummary:
    pid: int
    process_name: str
    established_count: int = 0
    listen_count: int = 0
    other_count: int = 0
    total_sockets: int = 0
    remote_endpoints: List[str] = field(default_factory=list)


def get_active_process_network_matrix() -> List[ProcessNetworkSummary]:
    """Capture live TCP connections grouped by process."""
    import psutil

    proc_names: Dict[int, str] = {}
    summaries: Dict[int, ProcessNetworkSummary] = {}

    try:
        conns = psutil.net_connections(kind="inet")
    except Exception as exc:
        logger.debug("psutil.net_connections failed: %s", exc)
        return []

    for c in conns:
        pid = c.pid or 0
        if pid not in proc_names:
            if pid == 0:
                pname = "System Idle / System"
            else:
                try:
                    pname = psutil.Process(pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pname = f"PID {pid}"
            proc_names[pid] = pname

        pname = proc_names[pid]

        if pid not in summaries:
            summaries[pid] = ProcessNetworkSummary(pid=pid, process_name=pname)

        s = summaries[pid]
        s.total_sockets += 1

        st = (c.status or "").upper()
        if st == "ESTABLISHED":
            s.established_count += 1
        elif st == "LISTEN":
            s.listen_count += 1
        else:
            s.other_count += 1

        if c.raddr:
            rem = f"{c.raddr.ip}:{c.raddr.port}"
            if len(s.remote_endpoints) < 5 and rem not in s.remote_endpoints:
                s.remote_endpoints.append(rem)

    results = list(summaries.values())
    # Sort by established connections descending, then total sockets descending
    results.sort(key=lambda x: (x.established_count, x.total_sockets), reverse=True)
    return results
