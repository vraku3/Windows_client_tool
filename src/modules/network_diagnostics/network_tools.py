# src/modules/network_diagnostics/network_tools.py
import socket
import subprocess
import concurrent.futures
import psutil
import re
from typing import List, Tuple, Callable, Optional, Dict
import logging
logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

# Keywords that mark a netstat -s counter as an "error" style metric, used to
# highlight rows in the Network Errors card.
_ERROR_KEYWORDS = (
    "error", "fail", "reset", "discard", "unreachable",
    "exceeded", "retransmit", "no route", "unknown protocol",
)


def is_error_stat(name: str) -> bool:
    lname = name.lower()
    return any(kw in lname for kw in _ERROR_KEYWORDS)


def ping(host: str, count: int = 4) -> str:
    """Run ping and return raw output."""
    result = subprocess.run(
        ["ping", "-n", str(count), host],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=30,
    )
    return result.stdout + result.stderr


def traceroute(host: str) -> List[Tuple[int, str, str]]:
    """Run tracert and return list of (hop_num, ip, time_ms)."""
    result = subprocess.run(
        ["tracert", "-d", "-w", "1000", host],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=120,
    )
    hops = []
    for line in result.stdout.splitlines():
        # Match lines that start with a hop number, e.g.:
        #   1    <1 ms    <1 ms    <1 ms  192.168.1.1
        #   2     *        *        *     Request timed out.
        m = re.match(r"^\s*(\d+)\s+", line)
        if not m:
            continue
        hop_num = int(m.group(1))
        tokens = line.split()
        # Last token is either an IP or "out." / "out"
        ip = tokens[-1] if tokens else "*"
        # Clean up trailing period from "timed out."
        if ip.endswith("."):
            ip = ip[:-1]
        # Time: find first numeric ms value or "<1"
        time_str = "*"
        for tok in tokens[1:]:
            if tok == "*":
                time_str = "*"
                break
            if re.match(r"^[<\d]", tok) and "ms" not in tok:
                time_str = tok
                break
            if tok.endswith("ms"):
                time_str = tok
                break
        hops.append((hop_num, ip, time_str))
    return hops


def dns_lookup(host: str, record_type: str = "A") -> str:
    """Run nslookup and return raw output."""
    result = subprocess.run(
        ["nslookup", f"-type={record_type}", host],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=15,
    )
    return result.stdout + result.stderr


def scan_ports(
    host: str,
    start: int,
    end: int,
    on_progress: Optional[Callable[[int, int], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> List[Tuple[int, str]]:
    """Scan TCP ports; return list of (port, 'open'). Processes in batches of 100."""
    open_ports: List[Tuple[int, str]] = []
    total = end - start + 1
    scanned = 0

    def check_port(port: int) -> Tuple[int, bool]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        result = s.connect_ex((host, port))
        s.close()
        return port, result == 0

    ports = list(range(start, end + 1))
    batch_size = 100
    for batch_start in range(0, len(ports), batch_size):
        if is_cancelled and is_cancelled():
            break
        batch = ports[batch_start : batch_start + batch_size]
        with concurrent.futures.ThreadPoolExecutor(max_workers=100) as ex:
            for port, is_open in ex.map(check_port, batch):
                if is_open:
                    open_ports.append((port, "open"))
        scanned += len(batch)
        if on_progress:
            on_progress(scanned, total)
    return open_ports


def get_connections() -> List[dict]:
    """Return active network connections via psutil."""
    conns = []
    for c in psutil.net_connections(kind="inet"):
        try:
            laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
            raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
            try:
                pname = psutil.Process(c.pid).name() if c.pid else ""
            except Exception:
                logger.warning("Ignored Exception getting process name", exc_info=True)
                pname = ""
            conns.append(
                {
                    "local": laddr,
                    "remote": raddr,
                    "status": c.status,
                    "pid": str(c.pid or ""),
                    "process": pname,
                }
            )
        except Exception:
            logger.warning("Ignored Exception reading network connection", exc_info=True)
            continue
    return conns


def get_wifi_profiles() -> List[str]:
    """Return a list of saved Wi-Fi profile names."""
    result = subprocess.run(
        ["netsh", "wlan", "show", "profiles"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=30,
    )
    profiles = []
    for line in result.stdout.splitlines():
        if ":" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                name = parts[-1].strip()
                if name:
                    profiles.append(name)
    return profiles


def get_wifi_profile_detail(name: str) -> str:
    """Return full detail (including key) for a Wi-Fi profile."""
    result = subprocess.run(
        ["netsh", "wlan", "show", "profile", f"name={name}", "key=clear"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=30,
    )
    return result.stdout


def get_tcpip_stats() -> List[Tuple[str, str, str]]:
    """Run `netstat -s` and return a flat list of (category, name, value).

    Covers IPv4/IPv6, ICMPv4/ICMPv6, TCP, and UDP sections. Two-column
    "Received / Sent" tables (ICMP) are expanded into separate rows.
    """
    result = subprocess.run(
        ["netstat", "-s"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=15,
    )
    stats: List[Tuple[str, str, str]] = []
    category = ""
    icmp_mode = False
    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            icmp_mode = False
            continue
        # Section headers are not indented, e.g. "TCP Statistics for IPv4"
        if not line.startswith(" ") and not line.startswith("\t"):
            category = stripped
            icmp_mode = False
            continue
        # ICMP two-column header row
        if stripped.startswith("Received") and stripped.endswith("Sent"):
            icmp_mode = True
            continue
        if icmp_mode:
            # e.g. "  Destination Unreachable    12          3"
            m = re.match(r"^(.+?)\s{2,}(\d+)\s+(\d+)$", stripped)
            if m:
                name = m.group(1).strip()
                stats.append((category, f"{name} (Received)", m.group(2)))
                stats.append((category, f"{name} (Sent)", m.group(3)))
                continue
            # fall through to standard key=value handling
        # Standard "Name = value" rows
        m = re.match(r"^(.+?)\s*=\s*(.+)$", stripped)
        if m:
            stats.append((category, m.group(1).strip(), m.group(2).strip()))
    return stats


def get_adapter_error_stats() -> List[dict]:
    """Return per-adapter error/drop counters via psutil.net_io_counters(pernic=True)."""
    result = []
    counters = psutil.net_io_counters(pernic=True)
    for name, c in counters.items():
        result.append(
            {
                "Adapter": name,
                "Bytes Sent": c.bytes_sent,
                "Bytes Recv": c.bytes_recv,
                "Errors In": c.errin,
                "Errors Out": c.errout,
                "Drops In": c.dropin,
                "Drops Out": c.dropout,
            }
        )
    return result


def get_total_io_counters() -> Tuple[int, int]:
    """Return (bytes_sent, bytes_recv) totals across all adapters."""
    c = psutil.net_io_counters()
    return c.bytes_sent, c.bytes_recv


def packet_capture_unavailable_reason() -> Optional[str]:
    """Return None if packet capture is usable, else a human-readable reason."""
    try:
        import scapy.all  # noqa: F401
    except Exception:
        return (
            "Packet capture requires the optional 'scapy' package and the Npcap driver.\n"
            "Install with: pip install scapy\n"
            "Then install Npcap from https://npcap.com (enable \"WinPcap API-compatible mode\")."
        )
    return None


def capture_packets(
    duration: float = 10.0,
    iface: Optional[str] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> List[dict]:
    """Capture packets for `duration` seconds using scapy/Npcap.

    Returns a list of dicts with keys: time, src, dst, proto, length, info, is_error.
    Raises RuntimeError if scapy/Npcap is unavailable.
    """
    reason = packet_capture_unavailable_reason()
    if reason:
        raise RuntimeError(reason)

    from scapy.all import sniff, IP, IPv6, TCP, UDP, ICMP

    packets: List[dict] = []

    def _classify(pkt) -> dict:
        proto = "OTHER"
        src = dst = ""
        info = ""
        is_error = False
        ip_layer = pkt.getlayer(IP) or pkt.getlayer(IPv6)
        if ip_layer is not None:
            src = ip_layer.src
            dst = ip_layer.dst
        if pkt.haslayer(TCP):
            proto = "TCP"
            tcp = pkt.getlayer(TCP)
            flags = str(tcp.flags)
            info = f"{tcp.sport} -> {tcp.dport} [{flags}]"
            if "R" in flags:
                is_error = True
                info += " (RST)"
        elif pkt.haslayer(UDP):
            proto = "UDP"
            udp = pkt.getlayer(UDP)
            info = f"{udp.sport} -> {udp.dport}"
        elif pkt.haslayer(ICMP):
            proto = "ICMP"
            icmp = pkt.getlayer(ICMP)
            info = f"type={icmp.type} code={icmp.code}"
            if icmp.type in (3, 11):  # Destination Unreachable / Time Exceeded
                is_error = True
                info += " (error)"
        else:
            info = pkt.summary()

        return {
            "time": float(pkt.time),
            "src": src,
            "dst": dst,
            "proto": proto,
            "length": len(pkt),
            "info": info,
            "is_error": is_error,
        }

    def _on_packet(pkt):
        packets.append(_classify(pkt))

    def _stop_filter(_pkt) -> bool:
        return bool(is_cancelled and is_cancelled())

    sniff(
        timeout=duration,
        prn=_on_packet,
        store=False,
        iface=iface,
        stop_filter=_stop_filter,
    )
    return packets


_DNS_PS = ("Get-DnsClientServerAddress -AddressFamily IPv4 | "
           "Select-Object InterfaceAlias, ServerAddresses | "
           "ConvertTo-Json -Compress")


def parse_dns_servers(raw: str) -> Dict[str, str]:
    """`{adapter name: "1.1.1.1, 8.8.8.8"}` from Get-DnsClientServerAddress JSON.

    ConvertTo-Json writes ONE adapter as a bare object and several as a list,
    and an adapter with no servers has an empty (or null) ServerAddresses --
    all three shapes are normal, none of them an error."""
    import json
    if not (raw or "").strip():
        return {}
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    result: Dict[str, str] = {}
    for entry in data:
        alias = entry.get("InterfaceAlias") or ""
        servers = entry.get("ServerAddresses") or []
        if isinstance(servers, str):
            servers = [servers]
        if alias and servers:
            result[alias] = ", ".join(servers)
    return result


def get_dns_servers() -> Dict[str, str]:
    """DNS servers per adapter, by the adapter names psutil uses.

    The Adapter Info card's DNS column was always blank: the value was
    initialised to "" and never assigned. A failed read returns {} and says why
    in the log -- an unreadable DNS setting must not look like "no DNS"."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _DNS_PS],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=15,
        )
        return parse_dns_servers(proc.stdout)
    except Exception:
        logger.warning("Could not read DNS servers per adapter", exc_info=True)
        return {}


def get_adapter_info() -> List[dict]:
    """Return a list of network adapter info dicts."""
    adapters = []
    dns_by_adapter = get_dns_servers()
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()

    # Build interface-IP → gateway map via `route print`; psutil has no gateway API.
    # Key is the interface IP address (column 4 in the active-routes table).
    gateways: Dict[str, str] = {}
    try:
        route = subprocess.run(
            ["route", "print", "0.0.0.0"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=5,
        )
        for line in route.stdout.splitlines():
            parts = line.split()
            # Active Routes row: Network Dest  Netmask  Gateway  Interface  Metric
            if len(parts) >= 4 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                gateways[parts[3]] = parts[2]  # interface IP → default gateway
    except Exception:
        import logging
        _log = logging.getLogger(__name__)
        _log.debug("Could not parse default gateway from route table", exc_info=True)

    for name, addr_list in addrs.items():
        ip = mac = netmask = ""
        dns = dns_by_adapter.get(name, "")
        for a in addr_list:
            family_name = a.family.name if hasattr(a.family, "name") else str(a.family)
            upper = family_name.upper()
            if "INET" in upper and "6" not in upper:
                ip = a.address
                netmask = a.netmask or ""
            elif "LINK" in upper or "PACKET" in upper:
                mac = a.address
        stat = stats.get(name)
        adapters.append(
            {
                "Name": name,
                "IP": ip,
                "MAC": mac,
                "Netmask": netmask,
                "Gateway": gateways.get(ip, ""),
                "DNS": dns,
                "Speed": f"{stat.speed} Mbps" if stat else "",
                "Up": "Yes" if (stat and stat.isup) else "No",
            }
        )
    return adapters
