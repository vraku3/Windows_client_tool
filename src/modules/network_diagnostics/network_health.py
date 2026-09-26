"""Network health findings: collect the machine's network state, probe it, and
turn both into a list of plain-language findings.  Qt-free.

Three layers, so each can be tested alone:

* ``collect_snapshot()``  reads configuration (one PowerShell call, the hosts
  file, proxy registry values).  Reads only.
* ``run_probes()``        actively tests reachability (gateway ping, per-server
  DNS timing, path MTU, IPv6, clock skew).  Every probe reports its own
  failure; none raises.
* ``evaluate()``          pure function of the two above.

Rule: a read or probe that could not run is an ``unknown`` finding carrying the
reason, never a pass and never a fail.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import logging
import os
import re
import socket
import struct
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from modules.network_diagnostics import dns_client
from core.windows_utils import system32

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
OK, INFO, WARNING, ERROR, UNKNOWN = "ok", "info", "warning", "error", "unknown"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, UNKNOWN: 2, INFO: 3, OK: 4}

DNS_TEST_NAME = "www.microsoft.com"
DNS_SLOW_MS = 200.0
MTU_TEST_PAYLOAD = 1472  # 1472 + 28 header bytes = 1500
NTP_HOST = "time.windows.com"

HOSTS_PATH = os.path.join(system32(), "drivers", "etc", "hosts")

# Names where a hosts-file redirect to a real address deserves a warning.
WELL_KNOWN_SUFFIXES = (
    "microsoft.com", "windowsupdate.com", "live.com", "office.com", "office365.com",
    "google.com", "googleapis.com", "github.com", "cloudflare.com", "amazonaws.com",
    "apple.com", "azure.com", "windows.com", "msftconnecttest.com",
)

_PS_SNAPSHOT = r"""
$ErrorActionPreference='Stop'
function Try-Get($b){ try { & $b } catch { $null } }
$o=[ordered]@{}
$o.adapters=@(Try-Get { Get-NetAdapter -IncludeHidden:$false | Select-Object Name,InterfaceDescription,Status,MacAddress,LinkSpeed,InterfaceIndex,MediaType,FullDuplex,DriverVersionString,DriverProvider,@{n='DriverDate';e={if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')}}} })
$o.addresses=@(Try-Get { Get-NetIPAddress | Select-Object InterfaceIndex,IPAddress,AddressFamily,PrefixLength,PrefixOrigin,AddressState })
$o.routes=@(Try-Get { Get-NetRoute | Where-Object { $_.DestinationPrefix -in '0.0.0.0/0','::/0' } | Select-Object InterfaceIndex,DestinationPrefix,NextHop,RouteMetric,InterfaceMetric })
$o.dns=@(Try-Get { Get-DnsClientServerAddress | Select-Object InterfaceIndex,AddressFamily,ServerAddresses })
$o.ifaces=@(Try-Get { Get-NetIPInterface | Select-Object InterfaceIndex,AddressFamily,Dhcp,InterfaceMetric,NlMtu })
$o.dhcp=@(Try-Get { Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'DHCPEnabled=True' | Select-Object InterfaceIndex,DHCPServer,@{n='LeaseObtained';e={if($_.DHCPLeaseObtained){$_.DHCPLeaseObtained.ToString('o')}}},@{n='LeaseExpires';e={if($_.DHCPLeaseExpires){$_.DHCPLeaseExpires.ToString('o')}}} })
$o | ConvertTo-Json -Depth 4 -Compress
"""


def _run(cmd: List[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          creationflags=CREATE_NO_WINDOW, timeout=timeout)


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def parse_snapshot_json(text: str) -> Dict[str, list]:
    """PowerShell's ConvertTo-Json collapses 1-element arrays; normalise."""
    raw = json.loads(text)
    out: Dict[str, list] = {}
    for key, val in raw.items():
        out[key] = [x for x in _as_list(val) if x is not None]
    for row in out.get("dns", []):
        row["ServerAddresses"] = _as_list(row.get("ServerAddresses"))
    return out


def read_winhttp_proxy() -> Optional[str]:
    """Returns the proxy string, "" for direct access, None if unreadable."""
    try:
        r = _run(["netsh", "winhttp", "show", "proxy"], 15)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("winhttp proxy read failed: %s", e)
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"Proxy Server\(s\)\s*:\s*(.+)", r.stdout)
    if m:
        return m.group(1).strip()
    return "" if "Direct access" in r.stdout else None


def read_user_proxy() -> Optional[Dict[str, Any]]:
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            def val(name, default=None):
                try:
                    return winreg.QueryValueEx(k, name)[0]
                except FileNotFoundError:
                    return default
            return {"enabled": bool(val("ProxyEnable", 0)), "server": val("ProxyServer", ""),
                    "pac": val("AutoConfigURL", ""), "auto_detect": bool(val("AutoDetect", 0))}
    except OSError as e:
        logger.warning("user proxy read failed: %s", e)
        return None


def parse_hosts(text: str) -> List[Dict[str, str]]:
    entries = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        parts = line.split()
        if len(parts) >= 2:
            for name in parts[1:]:
                entries.append({"address": parts[0], "name": name.lower()})
    return entries


def read_hosts() -> Optional[List[Dict[str, str]]]:
    try:
        with open(HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            return parse_hosts(f.read())
    except FileNotFoundError:
        return []  # Windows treats an absent hosts file as "no overrides"
    except OSError as e:
        logger.warning("hosts read failed: %s", e)
        return None


def collect_snapshot() -> Dict[str, Any]:
    """Read configuration. ``errors`` lists what could not be read and why."""
    snap: Dict[str, Any] = {"errors": [], "taken": datetime.now().isoformat(timespec="seconds")}
    try:
        r = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SNAPSHOT], 60)
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError((r.stderr or "no output").strip()[:200])
        snap.update(parse_snapshot_json(r.stdout))
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
        logger.warning("network snapshot failed: %s", e)
        snap["errors"].append(f"Adapter/route/DNS configuration could not be read: {e}")
    snap["winhttp_proxy"] = read_winhttp_proxy()
    snap["user_proxy"] = read_user_proxy()
    snap["hosts"] = read_hosts()
    return snap


# ---------------------------------------------------------------------------
# Views over the snapshot
# ---------------------------------------------------------------------------
def is_up(adapter: Dict[str, Any]) -> bool:
    return str(adapter.get("Status", "")).lower() == "up"


def _family(v: Any) -> int:
    """AddressFamily arrives as 2 (IPv4) / 23 (IPv6) or a string."""
    if isinstance(v, str):
        return 2 if v.lower() in ("ipv4", "2") else 23
    return int(v or 0)


def up_indexes(snap: Dict[str, Any]) -> set:
    return {a.get("InterfaceIndex") for a in snap.get("adapters", []) if is_up(a)}


def adapter_name(snap: Dict[str, Any], index: Any) -> str:
    for a in snap.get("adapters", []):
        if a.get("InterfaceIndex") == index:
            return str(a.get("Name"))
    return f"interface {index}"


def default_routes(snap: Dict[str, Any], family: int = 2) -> List[Dict[str, Any]]:
    """Default routes on adapters that are up, best (lowest total metric) first."""
    up = up_indexes(snap)
    rows = [r for r in snap.get("routes", [])
            if r.get("InterfaceIndex") in up and ("." in str(r.get("DestinationPrefix"))) == (family == 2)]
    return sorted(rows, key=lambda r: (r.get("RouteMetric") or 0) + (r.get("InterfaceMetric") or 0))


def is_apipa(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("169.254.0.0/16")
    except ValueError:
        return False


def is_global_v6(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 6 and not (a.is_link_local or a.is_loopback or a.is_multicast
                                   or a in ipaddress.ip_network("fec0::/10")) and a.is_global


def dns_servers_in_use(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Unique (server, adapter) for DNS servers on adapters that are up."""
    up = up_indexes(snap)
    seen, out = set(), []
    for row in snap.get("dns", []):
        if row.get("InterfaceIndex") not in up:
            continue
        for s in row.get("ServerAddresses", []):
            if s not in seen:
                seen.add(s)
                out.append({"server": s, "adapter": adapter_name(snap, row.get("InterfaceIndex")),
                            "index": row.get("InterfaceIndex")})
    return out


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------
def ping_once(host: str, timeout_ms: int = 1500, size: Optional[int] = None,
              dont_fragment: bool = False) -> Dict[str, Any]:
    """Returns {ok, rtt_ms, frag_needed, error}. ok False with error None = no reply."""
    cmd = ["ping", "-n", "1", "-w", str(timeout_ms)]
    if size is not None:
        cmd += ["-l", str(size)]
    if dont_fragment:
        cmd.append("-f")
    cmd.append(host)
    try:
        r = _run(cmd, timeout=max(5, timeout_ms // 1000 + 5))
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "rtt_ms": None, "frag_needed": False, "error": str(e)}
    out = r.stdout
    m = re.search(r"time[=<]\s*(\d+)\s*ms", out)
    return {"ok": bool(m) and "TTL=" in out, "rtt_ms": float(m.group(1)) if m else None,
            "frag_needed": "needs to be fragmented" in out, "error": None}


def discover_path_mtu(host: str = "1.1.1.1", lo: int = 548, hi: int = MTU_TEST_PAYLOAD) -> Dict[str, Any]:
    """Binary-search the largest DF payload that passes. {ok, mtu, error}.

    A timeout is not a "too big" answer: if a size gets neither a reply nor a
    fragmentation-needed message the search stops with an error rather than
    guessing.  ``mtu`` is payload + 28 (IPv4 + ICMP headers).
    """
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        r = ping_once(host, 1500, mid, True)
        if r["error"]:
            return {"ok": False, "mtu": None, "error": r["error"]}
        if r["ok"]:
            best, lo = mid, mid + 1
        elif r["frag_needed"]:
            hi = mid - 1
        else:
            return {"ok": False, "mtu": None, "error": f"no reply at payload {mid} (neither answer nor fragmentation notice)"}
    if best is None:
        return {"ok": False, "mtu": None, "error": "even the smallest test packet was refused"}
    return {"ok": True, "mtu": best + 28, "error": None}


def sntp_offset(host: str = NTP_HOST, timeout: float = 3.0) -> Dict[str, Any]:
    """Clock offset in seconds (positive = local clock is ahead). {ok, offset_s, error}."""
    pkt = b"\x1b" + 47 * b"\0"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            t0 = time.time()
            s.sendto(pkt, (host, 123))
            data, _ = s.recvfrom(512)
            t3 = time.time()
    except (OSError, socket.timeout) as e:
        return {"ok": False, "offset_s": None, "error": f"{type(e).__name__}: {e}"}
    if len(data) < 48:
        return {"ok": False, "offset_s": None, "error": "short NTP reply"}
    secs, frac = struct.unpack("!II", data[40:48])
    server = secs - 2208988800 + frac / 2 ** 32
    return {"ok": True, "offset_s": (t0 + t3) / 2 - server, "error": None}


def tcp_reachable(host: str, port: int, timeout: float = 2.5) -> Dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "error": None}
    except OSError as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _mtu_probe() -> Dict[str, Any]:
    first = ping_once("1.1.1.1", 2000, MTU_TEST_PAYLOAD, True)
    if first["error"] or first["ok"] or not first["frag_needed"]:
        return dict(first, mtu=1500 if first["ok"] else None)
    found = discover_path_mtu()
    return dict(first, mtu=found.get("mtu"), search_error=found.get("error"))


def _probe_tasks(snap: Dict[str, Any]) -> Dict[str, Callable[[], Any]]:
    tasks: Dict[str, Callable[[], Any]] = {}
    routes = default_routes(snap, 2)
    for gw in {r.get("NextHop") for r in routes if r.get("NextHop") not in (None, "0.0.0.0")}:
        tasks[f"gw:{gw}"] = (lambda g=gw: ping_once(g))
    for d in dns_servers_in_use(snap):
        s = d["server"]
        tasks[f"dns:{s}"] = (lambda x=s: dns_client.query(x, DNS_TEST_NAME, "A", 2.0))
    if routes:
        tasks["mtu"] = _mtu_probe
    tasks["ntp"] = sntp_offset
    if any(is_global_v6(a.get("IPAddress", "")) and a.get("InterfaceIndex") in up_indexes(snap)
           for a in snap.get("addresses", [])):
        tasks["v6"] = lambda: tcp_reachable("2606:4700:4700::1111", 53)
    return tasks


def run_probes(snap: Dict[str, Any], is_cancelled: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    """Run every applicable probe in parallel; results keyed like ``gw:<ip>``."""
    tasks = _probe_tasks(snap)
    results: Dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(fn): key for key, fn in tasks.items()}
        for fut in concurrent.futures.as_completed(futs):
            key = futs[fut]
            try:
                results[key] = fut.result()
            except Exception as e:  # noqa: BLE001 - a probe must never sink the sweep
                logger.warning("probe %s crashed: %s", key, e)
                results[key] = {"ok": False, "error": f"probe crashed: {e}"}
            if is_cancelled():
                break
    return results


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    id: str
    severity: str
    title: str
    detail: str = ""
    fix: Optional[str] = None  # a key understood by the tab, e.g. "flush_dns"
    items: List[str] = field(default_factory=list)


def _f_gateway(snap: Dict[str, Any], probes: Dict[str, Any]) -> List[Finding]:
    if not snap.get("routes") and not snap.get("adapters"):
        return []
    routes = default_routes(snap, 2)
    if not routes:
        return [Finding("gateway", ERROR, "No default gateway on any connected adapter",
                        "No IPv4 default route exists on an adapter that is up, so nothing outside the local subnet is reachable.")]
    out = []
    for gw in dict.fromkeys(r.get("NextHop") for r in routes):
        p = probes.get(f"gw:{gw}")
        if p is None:
            continue
        if p.get("error"):
            out.append(Finding(f"gw:{gw}", UNKNOWN, f"Gateway {gw}: ping could not run", p["error"]))
        elif p["ok"]:
            out.append(Finding(f"gw:{gw}", OK, f"Gateway {gw} reachable", f"Replied in {p['rtt_ms']:.0f} ms."))
        else:
            out.append(Finding(f"gw:{gw}", ERROR, f"Gateway {gw} does not answer ping",
                               "No ICMP reply. The gateway may be down or may block ping; if browsing works the latter is likely."))
    return out


def _f_multi_route(snap: Dict[str, Any]) -> List[Finding]:
    routes = default_routes(snap, 2)
    ifaces = list(dict.fromkeys(r["InterfaceIndex"] for r in routes))
    if len(ifaces) < 2:
        return []
    best = routes[0]
    lines = [f"{adapter_name(snap, r['InterfaceIndex'])} via {r['NextHop']} metric "
             f"{(r.get('RouteMetric') or 0) + (r.get('InterfaceMetric') or 0)}" for r in routes]
    return [Finding("multi-route", INFO, f"{len(ifaces)} default routes; {adapter_name(snap, best['InterfaceIndex'])} wins",
                    "Windows sends internet traffic out of the lowest total metric. Normal with Ethernet plus Wi-Fi, "
                    "but a surprise if traffic is leaving over the wrong network.", items=lines)]


def _f_apipa(snap: Dict[str, Any]) -> List[Finding]:
    up = up_indexes(snap)
    bad = [adapter_name(snap, a["InterfaceIndex"]) + " " + a["IPAddress"] for a in snap.get("addresses", [])
           if a.get("InterfaceIndex") in up and _family(a.get("AddressFamily")) == 2 and is_apipa(a.get("IPAddress", ""))]
    if not bad:
        return []
    return [Finding("apipa", ERROR, "Connected adapter has a 169.254.x.x (APIPA) address",
                    "DHCP failed, so Windows assigned itself a link-local address. The adapter cannot reach the network.",
                    fix="renew_dhcp", items=bad)]


def _f_dns(snap: Dict[str, Any], probes: Dict[str, Any]) -> List[Finding]:
    servers = dns_servers_in_use(snap)
    if not snap.get("dns"):
        return []
    if not servers:
        return [Finding("dns", ERROR, "No DNS server configured on any connected adapter")]
    out, times, failed = [], [], []
    for d in servers:
        r = probes.get(f"dns:{d['server']}")
        if r is None:
            continue
        if not r.ok or not r.resolved:
            why = r.error or f"answered {r.rcode} with no address"
            failed.append(f"{d['server']} ({d['adapter']}): {why}")
        else:
            times.append((d["server"], r.rtt_ms, d["adapter"]))
    if failed and not times:
        out.append(Finding("dns", ERROR, f"No DNS server resolved {DNS_TEST_NAME}",
                           "Name resolution is broken for every configured server.", items=failed))
    elif failed:
        out.append(Finding("dns-partial", WARNING, f"{len(failed)} of {len(servers)} DNS servers failed",
                           "Clients fall back to the next server only after a timeout, so every lookup can stall.", items=failed))
    if times:
        slow = [f"{s} ({a}): {t:.0f} ms" for s, t, a in times if t > DNS_SLOW_MS]
        line = ", ".join(f"{s} {t:.0f} ms" for s, t, _a in times)
        if slow:
            out.append(Finding("dns-slow", WARNING, f"Slow DNS response (over {DNS_SLOW_MS:.0f} ms)", "", fix=None, items=slow))
        else:
            out.append(Finding("dns", OK, "DNS resolving", line))
    dep = [f"{d['server']} ({d['adapter']})" for d in servers if d["server"].lower().startswith("fec0:")]
    if dep:
        out.append(Finding("dns-sitelocal", INFO, "Deprecated site-local DNS addresses (fec0:0:0:ffff::)",
                           "Windows' placeholder for IPv6 DNS; nothing answers there. Harmless if IPv4 DNS works.", items=dep))
    return out


def _f_proxy(snap: Dict[str, Any]) -> List[Finding]:
    out, notes = [], []
    wh = snap.get("winhttp_proxy")
    if wh is None:
        out.append(Finding("proxy-winhttp", UNKNOWN, "WinHTTP proxy could not be read"))
    elif wh:
        notes.append(f"WinHTTP (services, Windows Update): {wh}")
    u = snap.get("user_proxy")
    if u is None:
        out.append(Finding("proxy-user", UNKNOWN, "User proxy settings could not be read"))
    else:
        if u["enabled"] and u["server"]:
            notes.append(f"User proxy: {u['server']}")
        if u["pac"]:
            notes.append(f"Auto-config script: {u['pac']}")
    if notes:
        out.append(Finding("proxy", WARNING, "A proxy is configured",
                           "Traffic may be routed through a proxy; a stale one is a classic cause of 'internet works for ping only'.", items=notes))
    elif wh is not None and u is not None:
        out.append(Finding("proxy", OK, "No proxy configured"))
    return out


def _is_null_addr(a: str) -> bool:
    return a in ("0.0.0.0", "::", "127.0.0.1", "::1")


def _f_hosts(snap: Dict[str, Any]) -> List[Finding]:
    hosts = snap.get("hosts")
    if hosts is None:
        return [Finding("hosts", UNKNOWN, "Hosts file could not be read")]
    real = [e for e in hosts if not _is_null_addr(e["address"]) and e["name"] != "localhost"]
    hit = [f"{e['name']} -> {e['address']}" for e in real if e["name"].endswith(WELL_KNOWN_SUFFIXES)]
    out = []
    if hit:
        out.append(Finding("hosts-wellknown", WARNING, "Hosts file redirects a well-known name to a real address",
                           "Whatever the DNS servers say, this machine ignores it for these names.", items=hit))
    elif real:
        out.append(Finding("hosts", INFO, f"Hosts file has {len(real)} custom mapping(s)",
                           items=[f"{e['name']} -> {e['address']}" for e in real[:20]]))
    blocks = [e for e in hosts if _is_null_addr(e["address"]) and e["name"] != "localhost"]
    if blocks:
        out.append(Finding("hosts-blocks", INFO, f"Hosts file blocks {len(blocks)} name(s) (0.0.0.0 / 127.0.0.1)"))
    if not real and not blocks:
        out.append(Finding("hosts", OK, "Hosts file has no overrides"))
    return out


def _f_dhcp(snap: Dict[str, Any], now: datetime) -> List[Finding]:
    up, out = up_indexes(snap), []
    for row in snap.get("dhcp", []):
        if row.get("InterfaceIndex") not in up or not row.get("LeaseExpires"):
            continue
        try:
            exp = datetime.fromisoformat(row["LeaseExpires"]).replace(tzinfo=None)
        except ValueError:
            logger.debug("unparseable lease %r", row["LeaseExpires"])
            continue
        left = (exp - now).total_seconds()
        name = adapter_name(snap, row["InterfaceIndex"])
        if left < 0:
            out.append(Finding(f"dhcp:{name}", ERROR, f"{name}: DHCP lease expired {exp:%Y-%m-%d %H:%M}", fix="renew_dhcp"))
        elif left < 3600:
            out.append(Finding(f"dhcp:{name}", WARNING, f"{name}: DHCP lease expires in {int(left // 60)} min", fix="renew_dhcp"))
    return out


def _f_links(snap: Dict[str, Any]) -> List[Finding]:
    out = []
    for a in snap.get("adapters", []):
        if not is_up(a) or "802.3" not in str(a.get("MediaType", "")):
            continue
        speed = str(a.get("LinkSpeed", ""))
        m = re.match(r"([\d.]+)\s*([MG])bps", speed)
        mbps = float(m.group(1)) * (1000 if m and m.group(2) == "G" else 1) if m else None
        if a.get("FullDuplex") is False:
            out.append(Finding(f"duplex:{a['Name']}", WARNING, f"{a['Name']} is running half duplex",
                               "Usually a duplex mismatch with the switch port (one side hard-set). Expect collisions and poor throughput."))
        if mbps is not None and mbps <= 100:
            out.append(Finding(f"speed:{a['Name']}", WARNING, f"{a['Name']} linked at only {speed}",
                               "A gigabit adapter negotiating 100 Mbps or less usually means a bad cable, port or a forced setting."))
    return out


def _f_mtu(snap: Dict[str, Any], probes: Dict[str, Any]) -> List[Finding]:
    out: List[Finding] = []
    m = probes.get("mtu")
    if m is not None:
        if m.get("error"):
            out.append(Finding("mtu", UNKNOWN, "Path MTU test could not run", m["error"]))
        elif m["frag_needed"]:
            mtu = m.get("mtu")
            owner = [adapter_name(snap, i["InterfaceIndex"]) for i in snap.get("ifaces", [])
                     if i.get("InterfaceIndex") in up_indexes(snap) and _family(i.get("AddressFamily")) == 2
                     and i.get("NlMtu") == mtu]
            if mtu and owner:
                out.append(Finding("mtu", INFO, f"Path MTU {mtu}, set by {owner[0]}",
                                   "Matches that adapter's own MTU (typical of a VPN/tunnel carrying the traffic), so it is expected."))
                return out
            out.append(Finding("mtu", WARNING, f"Path MTU to the internet is {mtu}" if mtu else "Path MTU to the internet is below 1500",
                               "Below 1500 is expected over a VPN/PPPoE/tunnel, but a wrong value (or blocked ICMP) makes sites stall after "
                               "connecting. Compare with the adapter MTU." + (f" (search stopped: {m['search_error']})" if m.get("search_error") else "")))
        elif m["ok"]:
            out.append(Finding("mtu", OK, "Path MTU 1500 to the internet"))
        else:
            out.append(Finding("mtu", UNKNOWN, "Path MTU test got no reply", "1.1.1.1 did not answer ping, so the MTU is unmeasured."))
    return out


def _f_time_v6(probes: Dict[str, Any]) -> List[Finding]:
    out: List[Finding] = []
    n = probes.get("ntp")
    if n is not None:
        if not n["ok"]:
            out.append(Finding("time", UNKNOWN, f"Clock skew could not be measured ({NTP_HOST})", n["error"] or ""))
        else:
            off = n["offset_s"]
            sev = ERROR if abs(off) > 300 else WARNING if abs(off) > 5 else OK
            out.append(Finding("time", sev, f"Clock is {abs(off):.1f} s {'ahead of' if off > 0 else 'behind'} network time" if sev != OK else "Clock in sync",
                               "Kerberos and TLS fail beyond about 5 minutes of skew." if sev != OK else f"Offset {off:+.2f} s vs {NTP_HOST}."))
    v6 = probes.get("v6")
    if v6 is not None and not v6["ok"]:
        out.append(Finding("ipv6", WARNING, "IPv6 address present but IPv6 internet unreachable", v6["error"] or "",
                           items=["Apps preferring IPv6 will stall before falling back to IPv4."]))
    elif v6 is not None:
        out.append(Finding("ipv6", OK, "IPv6 internet reachable"))
    return out


def evaluate(snap: Dict[str, Any], probes: Dict[str, Any], now: Optional[datetime] = None) -> List[Finding]:
    now = now or datetime.now()
    out: List[Finding] = [Finding("read", UNKNOWN, "Could not read some configuration", e) for e in snap.get("errors", [])]
    for part in (_f_gateway(snap, probes), _f_multi_route(snap), _f_apipa(snap), _f_dns(snap, probes),
                 _f_proxy(snap), _f_hosts(snap), _f_dhcp(snap, now), _f_links(snap), _f_mtu(snap, probes), _f_time_v6(probes)):
        out.extend(part)
    out.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return out


def summarize(findings: List[Finding]) -> str:
    bad = sum(f.severity == ERROR for f in findings)
    warn = sum(f.severity == WARNING for f in findings)
    unk = sum(f.severity == UNKNOWN for f in findings)
    if not (bad or warn or unk):
        return "No problems found"
    parts = [f"{n} {w}" for n, w in ((bad, "error(s)"), (warn, "warning(s)"), (unk, "could not be checked")) if n]
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Report for a ticket
# ---------------------------------------------------------------------------
def _adapter_lines(snap: Dict[str, Any]) -> List[str]:
    lines = []
    for a in snap.get("adapters", []):
        ips = [x["IPAddress"] for x in snap.get("addresses", []) if x.get("InterfaceIndex") == a.get("InterfaceIndex")]
        lines.append(f"  {a.get('Name')} [{a.get('Status')}] {a.get('MacAddress')} {a.get('LinkSpeed')} "
                     f"driver {a.get('DriverVersionString')}{' (' + a['DriverDate'] + ')' if a.get('DriverDate') else ''} -{a.get('InterfaceDescription')}")
        if ips:
            lines.append("      " + ", ".join(ips))
    return lines


def build_report(snap: Dict[str, Any], probes: Dict[str, Any], findings: List[Finding], host: str = "") -> str:
    host = host or os.environ.get("COMPUTERNAME", "")
    lines = [f"Network diagnostics report - {host} - {snap.get('taken')}", "", f"Summary: {summarize(findings)}", "", "Findings"]
    for f in findings:
        lines.append(f"  [{f.severity.upper()}] {f.title}" + (f" - {f.detail}" if f.detail else ""))
        lines += [f"      {i}" for i in f.items]
    lines += ["", "Adapters", *_adapter_lines(snap), "", "Default routes"]
    lines += [f"  {adapter_name(snap, r['InterfaceIndex'])}: {r['DestinationPrefix']} via {r['NextHop']} metric "
              f"{(r.get('RouteMetric') or 0) + (r.get('InterfaceMetric') or 0)}" for r in default_routes(snap, 2)]
    lines += ["", "DNS servers"]
    for d in dns_servers_in_use(snap):
        r = probes.get(f"dns:{d['server']}")
        t = f"{r.rtt_ms:.0f} ms" if r is not None and r.rtt_ms is not None else (r.error if r is not None else "not probed")
        lines.append(f"  {d['server']} ({d['adapter']}): {t}")
    return "\n".join(lines)
