"""Listening ports & bound sockets reader.

Enumerates TCP listening sockets and bound UDP endpoints, resolving process names,
PIDs, full executable paths, and classifying network exposure scope:
- Loopback (127.0.0.1, ::1) — accessible only on this machine
- All Interfaces (0.0.0.0, ::) — exposed to the local network / all adapters
- Specific Interface IP

Qt-free engine module.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

WELL_KNOWN_PORTS: Dict[int, str] = {
    20: "FTP Data",
    21: "FTP Control",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    67: "DHCP Server",
    68: "DHCP Client",
    69: "TFTP",
    80: "HTTP",
    88: "Kerberos",
    110: "POP3",
    123: "NTP",
    135: "RPC / Endpoint Mapper",
    137: "NetBIOS Name",
    138: "NetBIOS Datagram",
    139: "NetBIOS Session",
    143: "IMAP",
    161: "SNMP",
    389: "LDAP",
    443: "HTTPS",
    445: "SMB",
    465: "SMTPS",
    500: "IKE (IPsec)",
    514: "Syslog",
    587: "SMTP Submission",
    636: "LDAPS",
    993: "IMAPS",
    995: "POP3S",
    1433: "Microsoft SQL Server",
    1434: "Microsoft SQL Monitor",
    1521: "Oracle DB",
    2049: "NFS",
    2869: "UPnP / SSDP",
    3000: "Dev Server (Node/React)",
    3306: "MySQL",
    3389: "RDP (Remote Desktop)",
    5000: "Flask / Dev Server",
    5040: "Windows Connected Devices",
    5353: "mDNS",
    5432: "PostgreSQL",
    5985: "WinRM (HTTP)",
    5986: "WinRM (HTTPS)",
    6379: "Redis",
    7680: "WUDO (Delivery Optimization)",
    8000: "Dev Server",
    8080: "HTTP Proxy / Alt",
    8443: "HTTPS Alt",
    9090: "Prometheus / Cockpit",
    27017: "MongoDB",
}

_SYSTEM_PROCESS_NAMES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe",
    "services.exe", "lsass.exe", "svchost.exe", "spoolsv.exe",
    "fontdrvhost.exe", "dasHost.exe", "sihost.exe", "taskhostw.exe",
    "explorer.exe", "dwm.exe", "SearchIndexer.exe",
}


@dataclass(frozen=True)
class ListeningPort:
    proto: str              # "TCP" or "UDP"
    ip: str                 # "0.0.0.0", "127.0.0.1", "::", etc.
    port: int
    scope: str              # "Loopback", "All Interfaces", "Specific IP"
    service_name: str       # e.g. "HTTPS (443)", "RDP (3389)" or ""
    pid: Optional[int]
    process_name: str
    process_path: str
    username: str
    is_system: bool

    @property
    def endpoint(self) -> str:
        if ":" in self.ip and not self.ip.startswith("["):
            return f"[{self.ip}]:{self.port}"
        return f"{self.ip}:{self.port}"

    @property
    def is_public(self) -> bool:
        return self.scope == "All Interfaces"

    @property
    def is_loopback(self) -> bool:
        return self.scope == "Loopback"


def classify_scope(ip: str) -> str:
    cleaned = (ip or "").strip("[]").lower()
    if cleaned in ("127.0.0.1", "::1", "localhost") or cleaned.startswith("127."):
        return "Loopback"
    if cleaned in ("0.0.0.0", "::", ""):
        return "All Interfaces"
    return "Specific IP"


def get_service_name(port: int, proto: str = "TCP") -> str:
    known = WELL_KNOWN_PORTS.get(port)
    if known:
        return f"{known} ({port})"
    try:
        name = socket.getservbyport(port, proto.lower())
        return f"{name.upper()} ({port})"
    except (OSError, ValueError):
        return str(port)


def is_system_process(name: str, path: str = "") -> bool:
    low_name = (name or "").strip().lower()
    low_path = (path or "").strip().lower()
    if low_name in _SYSTEM_PROCESS_NAMES:
        return True
    windir = os.environ.get("SystemRoot", r"C:\Windows").lower()
    if low_path and low_path.startswith(windir):
        return True
    return False


def _get_process_cache() -> Dict[int, Tuple[str, str, str]]:
    """Cached mapping of PID -> (name, exe_path, username)."""
    cache: Dict[int, Tuple[str, str, str]] = {}
    cache[0] = ("System Idle Process", "", "NT AUTHORITY\\SYSTEM")
    cache[4] = ("System", "", "NT AUTHORITY\\SYSTEM")
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "exe", "username"]):
            try:
                info = proc.info
                pid = info.get("pid")
                if pid is not None:
                    name = info.get("name") or ""
                    exe = info.get("exe") or ""
                    user = info.get("username") or ""
                    cache[pid] = (name, exe, user)
            except Exception as exc:
                logger.debug("Error reading proc info for %s: %s", getattr(proc, "pid", None), exc)
                continue
    except Exception as exc:
        logger.debug("psutil process_iter error: %s", exc)
    return cache


def parse_netstat_output(text: str, proc_cache: Optional[Dict[int, Tuple[str, str, str]]] = None) -> List[ListeningPort]:
    """Parse output from `netstat -ano`."""
    if proc_cache is None:
        proc_cache = _get_process_cache()

    line_re = re.compile(
        r"^\s*(TCP|UDP)\s+(\S+):(\d+)\s+(\S+)(?:\s+(LISTENING))?\s+(\d+)",
        re.MULTILINE | re.IGNORECASE,
    )
    ports: List[ListeningPort] = []
    seen = set()

    for m in line_re.finditer(text or ""):
        proto, ip, port_str, _foreign, state, pid_str = m.groups()
        proto = proto.upper()
        if proto == "TCP" and (not state or state.upper() != "LISTENING"):
            continue

        try:
            port = int(port_str)
            pid = int(pid_str) if pid_str else None
        except ValueError as exc:
            logger.debug("Parsing port/pid (%s, %s): %s", port_str, pid_str, exc)
            continue

        key = (proto, ip, port, pid)
        if key in seen:
            continue
        seen.add(key)

        pname, ppath, puser = proc_cache.get(pid, ("", "", "")) if pid else ("", "", "")
        scope = classify_scope(ip)
        service = get_service_name(port, proto)
        sys_proc = is_system_process(pname, ppath)

        ports.append(
            ListeningPort(
                proto=proto,
                ip=ip,
                port=port,
                scope=scope,
                service_name=service,
                pid=pid,
                process_name=pname or (f"PID {pid}" if pid else "Unknown"),
                process_path=ppath,
                username=puser,
                is_system=sys_proc,
            )
        )

    # Sort ports: Public first, then port number ascending
    ports.sort(key=lambda p: (0 if p.is_public else 1, p.port, p.proto))
    return ports


def read_listening_ports() -> Tuple[List[ListeningPort], List[str]]:
    """Read all listening TCP and bound UDP ports.

    Uses psutil if available, falling back to `netstat -ano`.
    Never raises; returns (ports, problems).
    """
    problems: List[str] = []
    proc_cache = _get_process_cache()

    # Try psutil first
    try:
        import psutil
        ports: List[ListeningPort] = []
        seen = set()

        for c in psutil.net_connections(kind="inet"):
            try:
                proto = "TCP" if c.type == socket.SOCK_STREAM else "UDP"
                # For TCP, only include listening sockets
                if proto == "TCP" and c.status != "LISTEN":
                    continue
                # For UDP, local address must be bound
                if not c.laddr:
                    continue

                ip = c.laddr.ip
                port = c.laddr.port
                pid = c.pid

                key = (proto, ip, port, pid)
                if key in seen:
                    continue
                seen.add(key)

                pname, ppath, puser = proc_cache.get(pid, ("", "", "")) if pid else ("", "", "")
                if not pname and pid:
                    try:
                        p = psutil.Process(pid)
                        pname = p.name()
                        ppath = p.exe()
                        puser = p.username()
                        proc_cache[pid] = (pname, ppath, puser)
                    except Exception:
                        pname = f"PID {pid}"

                scope = classify_scope(ip)
                service = get_service_name(port, proto)
                sys_proc = is_system_process(pname, ppath)

                ports.append(
                    ListeningPort(
                        proto=proto,
                        ip=ip,
                        port=port,
                        scope=scope,
                        service_name=service,
                        pid=pid,
                        process_name=pname or (f"PID {pid}" if pid else "Unknown"),
                        process_path=ppath,
                        username=puser,
                        is_system=sys_proc,
                    )
                )
            except Exception as exc:
                logger.debug("Error reading proc info for %s: %s", getattr(proc, "pid", None), exc)
                continue

        if ports:
            ports.sort(key=lambda p: (0 if p.is_public else 1, p.port, p.proto))
            return ports, problems
    except Exception as exc:
        logger.debug("psutil net_connections failed, trying netstat fallback: %s", exc)

    # Fallback to netstat -ano
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
            ports = parse_netstat_output(proc.stdout, proc_cache)
            return ports, problems
        problems.append(f"netstat exited with code {proc.returncode}: {proc.stderr.strip()[:150]}")
    except Exception as exc:
        problems.append(f"Could not read listening ports: {exc}")

    return [], problems
