"""Active TCP Sockets, Connection States & Ephemeral Port Exhaustion Analytics.

Monitors active TCP connections, state distribution (ESTABLISHED, CLOSE_WAIT, TIME_WAIT, SYN_SENT),
identifies application socket leaks (dangling CLOSE_WAIT connections), and calculates dynamic/ephemeral
port exhaustion risks against Windows TCPIP configuration.

Qt-free engine module.
"""
from __future__ import annotations

import collections
import logging
import os
import re
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_EPHEMERAL_START = 49152
DEFAULT_EPHEMERAL_COUNT = 16384

# Service port mappings for common outgoing ports
SERVICE_PORTS: Dict[int, str] = {
    21: "FTP",
    22: "SSH",
    25: "SMTP",
    53: "DNS",
    80: "HTTP",
    88: "Kerberos",
    135: "RPC",
    139: "NetBIOS",
    389: "LDAP",
    443: "HTTPS",
    445: "SMB",
    636: "LDAPS",
    1433: "MSSQL",
    1521: "Oracle",
    3306: "MySQL",
    3389: "RDP",
    5432: "PostgreSQL",
    5985: "WinRM",
    5986: "WinRM-S",
    6379: "Redis",
    8080: "HTTP-Alt",
    8443: "HTTPS-Alt",
}


@dataclass
class SocketConnection:
    proto: str  # TCP
    local_ip: str
    local_port: int
    foreign_ip: str
    foreign_port: int
    state: str  # ESTABLISHED, CLOSE_WAIT, TIME_WAIT, SYN_SENT, LISTEN, etc.
    pid: Optional[int]
    process_name: str
    process_path: str = ""
    username: str = ""
    is_ephemeral: bool = False
    is_leak_suspect: bool = False
    service_name: str = ""

    @property
    def local_endpoint(self) -> str:
        return f"{self.local_ip}:{self.local_port}"

    @property
    def foreign_endpoint(self) -> str:
        if not self.foreign_ip or self.foreign_ip in ("0.0.0.0", "::", "*"):
            return f"*:{self.foreign_port}" if self.foreign_port else "*:*"
        return f"{self.foreign_ip}:{self.foreign_port}"


@dataclass
class EphemeralPortRange:
    start_port: int = DEFAULT_EPHEMERAL_START
    num_ports: int = DEFAULT_EPHEMERAL_COUNT
    in_use: int = 0
    percent_used: float = 0.0
    status: str = "Normal"  # Normal, Caution, Critical

    @property
    def end_port(self) -> int:
        return self.start_port + self.num_ports - 1


@dataclass
class ProcessSocketSummary:
    process_name: str
    pid: Optional[int]
    total: int = 0
    established: int = 0
    close_wait: int = 0
    time_wait: int = 0
    syn_sent: int = 0
    listen: int = 0
    other: int = 0
    process_path: str = ""
    has_leak: bool = False


@dataclass
class SocketAnalyticsSnapshot:
    ephemeral: EphemeralPortRange = field(default_factory=EphemeralPortRange)
    state_counts: Dict[str, int] = field(default_factory=dict)
    connections: List[SocketConnection] = field(default_factory=list)
    process_summaries: List[ProcessSocketSummary] = field(default_factory=list)
    alerts: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


def parse_netsh_dynamic_ports(text: str) -> Tuple[int, int]:
    """Parse output of 'netsh int ipv4 show dynamicport tcp'.

    Example:
      Start Port      : 49152
      Number of Ports : 16384
    """
    start = DEFAULT_EPHEMERAL_START
    count = DEFAULT_EPHEMERAL_COUNT

    m_start = re.search(r"Start\s+Port\s*:\s*(\d+)", text, re.IGNORECASE)
    if m_start:
        try:
            start = int(m_start.group(1))
        except ValueError as exc:
            logger.debug("Failed parsing start port integer from netsh: %s", exc)

    m_count = re.search(r"Number\s+of\s+Ports\s*:\s*(\d+)", text, re.IGNORECASE)
    if m_count:
        try:
            count = int(m_count.group(1))
        except ValueError as exc:
            logger.debug("Failed parsing count port integer from netsh: %s", exc)

    return start, count


def get_ephemeral_port_range() -> Tuple[int, int]:
    """Query Windows dynamic/ephemeral port range via netsh."""
    try:
        proc = subprocess.run(
            ["netsh", "int", "ipv4", "show", "dynamicport", "tcp"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        if proc.returncode == 0 and proc.stdout:
            return parse_netsh_dynamic_ports(proc.stdout)
    except Exception as exc:
        logger.debug("Failed querying netsh dynamicport: %s", exc)

    return DEFAULT_EPHEMERAL_START, DEFAULT_EPHEMERAL_COUNT


def _get_process_cache() -> Dict[int, Tuple[str, str, str]]:
    cache: Dict[int, Tuple[str, str, str]] = {}
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "exe", "username"]):
            try:
                info = p.info
                cache[info["pid"]] = (
                    info.get("name") or "",
                    info.get("exe") or "",
                    info.get("username") or "",
                )
            except Exception as proc_err:
                logger.debug("Failed reading individual process info: %s", proc_err)
                continue
    except Exception as exc:
        logger.debug("Process cache failed: %s", exc)
    return cache


def parse_netstat_connections(text: str, proc_cache: Dict[int, Tuple[str, str, str]],
                              start_port: int, end_port: int) -> List[SocketConnection]:
    """Parse netstat -ano connection rows."""
    conns: List[SocketConnection] = []
    # Pattern matching TCP or UDP lines
    pattern = re.compile(
        r"^\s*(TCP|UDP)\s+"
        r"(\[[0-9a-fA-F:]+\]|\S+?):(\d+)\s+"
        r"(\[[0-9a-fA-F:]+\]|\S+?):(\d+|\*)\s+"
        r"(?:([A-Z_]+)\s+)?(\d+)\s*$",
        re.IGNORECASE,
    )

    for line in text.splitlines():
        line = line.strip()
        m = pattern.match(line)
        if not m:
            continue

        proto, local_ip, local_port_str, foreign_ip, foreign_port_str, state_raw, pid_str = m.groups()
        if proto.upper() != "TCP":
            continue

        try:
            local_port = int(local_port_str)
            foreign_port = int(foreign_port_str) if foreign_port_str != "*" else 0
            pid = int(pid_str) if pid_str else None
        except ValueError as parse_err:
            logger.debug("Failed parsing netstat port/pid: %s", parse_err)
            continue

        state = (state_raw or "UNKNOWN").upper()
        pname, ppath, puser = proc_cache.get(pid, ("", "", "")) if pid else ("", "", "")

        is_ephem = start_port <= local_port <= end_port
        is_leak = state == "CLOSE_WAIT"
        service = SERVICE_PORTS.get(foreign_port) or SERVICE_PORTS.get(local_port) or ""

        conns.append(
            SocketConnection(
                proto="TCP",
                local_ip=local_ip,
                local_port=local_port,
                foreign_ip=foreign_ip,
                foreign_port=foreign_port,
                state=state,
                pid=pid,
                process_name=pname or (f"PID {pid}" if pid else "System/Idle"),
                process_path=ppath,
                username=puser,
                is_ephemeral=is_ephem,
                is_leak_suspect=is_leak,
                service_name=service,
            )
        )

    return conns


def read_socket_analytics() -> SocketAnalyticsSnapshot:
    """Read all active TCP sockets, compute state distribution & ephemeral port exhaustion."""
    problems: List[str] = []
    alerts: List[str] = []

    start_port, num_ports = get_ephemeral_port_range()
    end_port = start_port + num_ports - 1

    proc_cache = _get_process_cache()
    connections: List[SocketConnection] = []

    # Attempt psutil reading
    try:
        import psutil
        for c in psutil.net_connections(kind="tcp"):
            try:
                local_ip = c.laddr.ip if c.laddr else ""
                local_port = c.laddr.port if c.laddr else 0
                foreign_ip = c.raddr.ip if c.raddr else ""
                foreign_port = c.raddr.port if c.raddr else 0
                state = (c.status or "UNKNOWN").upper()
                pid = c.pid

                pname, ppath, puser = proc_cache.get(pid, ("", "", "")) if pid else ("", "", "")
                is_ephem = start_port <= local_port <= end_port
                is_leak = state == "CLOSE_WAIT"
                service = SERVICE_PORTS.get(foreign_port) or SERVICE_PORTS.get(local_port) or ""

                connections.append(
                    SocketConnection(
                        proto="TCP",
                        local_ip=local_ip,
                        local_port=local_port,
                        foreign_ip=foreign_ip,
                        foreign_port=foreign_port,
                        state=state,
                        pid=pid,
                        process_name=pname or (f"PID {pid}" if pid else "System/Idle"),
                        process_path=ppath,
                        username=puser,
                        is_ephemeral=is_ephem,
                        is_leak_suspect=is_leak,
                        service_name=service,
                    )
                )
            except Exception as conn_err:
                logger.debug("Failed parsing psutil connection entry: %s", conn_err)
                continue
    except Exception as exc:
        logger.debug("psutil net_connections(tcp) failed, using netstat fallback: %s", exc)
        try:
            proc = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
                creationflags=_CREATE_NO_WINDOW,
            )
            if proc.returncode == 0 and proc.stdout:
                connections = parse_netstat_connections(proc.stdout, proc_cache, start_port, end_port)
            else:
                problems.append(f"netstat exited with code {proc.returncode}")
        except Exception as net_exc:
            problems.append(f"Could not read sockets: {net_exc}")

    # Compute Ephemeral Port Statistics
    ephemeral_in_use = sum(1 for c in connections if c.is_ephemeral)
    pct = (ephemeral_in_use / num_ports * 100.0) if num_ports > 0 else 0.0
    ephem_status = "Critical" if pct >= 80.0 else ("Caution" if pct >= 60.0 else "Normal")

    ephem_report = EphemeralPortRange(
        start_port=start_port,
        num_ports=num_ports,
        in_use=ephemeral_in_use,
        percent_used=round(pct, 1),
        status=ephem_status,
    )

    if ephem_status != "Normal":
        alerts.append(
            f"Dynamic port range exhaustion risk: {ephemeral_in_use} / {num_ports} ports in use ({pct:.1f}%). "
            "High connection churn may trigger WSAENOBUFS (10055) / WSAEADDRINUSE (10048)."
        )

    # State counts
    state_counts = collections.Counter(c.state for c in connections)

    # Group by Process
    proc_groups: Dict[str, ProcessSocketSummary] = {}
    for c in connections:
        key = f"{c.process_name}_{c.pid}"
        if key not in proc_groups:
            proc_groups[key] = ProcessSocketSummary(
                process_name=c.process_name,
                pid=c.pid,
                process_path=c.process_path,
            )
        summ = proc_groups[key]
        summ.total += 1
        st = c.state
        if st == "ESTABLISHED":
            summ.established += 1
        elif st == "CLOSE_WAIT":
            summ.close_wait += 1
            summ.has_leak = True
        elif st == "TIME_WAIT":
            summ.time_wait += 1
        elif st == "SYN_SENT":
            summ.syn_sent += 1
        elif st == "LISTEN":
            summ.listen += 1
        else:
            summ.other += 1

    summaries = list(proc_groups.values())
    summaries.sort(key=lambda s: (1 if s.has_leak else 0, s.total), reverse=True)

    # Process level alerts
    for s in summaries:
        if s.close_wait > 0:
            alerts.append(
                f"'{s.process_name}' (PID {s.pid}) has {s.close_wait} socket(s) in CLOSE_WAIT. "
                "Remote host closed connection, but local application failed to close its socket handle."
            )
        if s.time_wait > 1000:
            alerts.append(
                f"'{s.process_name}' (PID {s.pid}) holds {s.time_wait} sockets in TIME_WAIT. "
                "Indicates rapid socket turnover without TCP connection reuse."
            )
        if s.syn_sent > 50:
            alerts.append(
                f"'{s.process_name}' (PID {s.pid}) has {s.syn_sent} sockets in SYN_SENT. "
                "Outgoing handshakes are timing out (unreachable host, DNS failure, or packet drop)."
            )

    # Sort connections: Leaks first, then foreign port, then process
    connections.sort(
        key=lambda c: (
            0 if c.is_leak_suspect else 1,
            0 if c.state == "ESTABLISHED" else 1,
            c.process_name,
            c.local_port,
        )
    )

    return SocketAnalyticsSnapshot(
        ephemeral=ephem_report,
        state_counts=dict(state_counts),
        connections=connections,
        process_summaries=summaries,
        alerts=alerts,
        problems=problems,
    )
