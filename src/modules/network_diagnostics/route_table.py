"""Full IP route table and ARP/neighbour cache. No Qt.

`network_health.collect_snapshot()` already reads the DEFAULT routes only, for
its own findings; this is the fuller picture an admin actually wants when
diagnosing "why is this going out the wrong interface" -- every route, and
which MAC address the machine believes owns each neighbouring IP.
"""
import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

#: A neighbour cache entry in one of these states is worth flagging: the ARP
#: resolution failed or has not completed, so traffic to it will stall.
_BAD_STATES = {"Unreachable", "Incomplete", "Probe"}

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
function AsList($x) { if ($null -eq $x) { @() } elseif ($x -is [array]) { $x } else { @($x) } }
$adapters = @{}
foreach ($a in Get-NetAdapter -ErrorAction SilentlyContinue) { $adapters[[string]$a.InterfaceIndex] = $a.Name }
$routes = AsList (Get-NetRoute -ErrorAction SilentlyContinue |
    Select-Object DestinationPrefix, NextHop, InterfaceIndex, RouteMetric, InterfaceMetric,
                  @{n='Protocol';e={$_.Protocol.ToString()}}, @{n='Store';e={$_.Store.ToString()}})
$neighbors = AsList (Get-NetNeighbor -ErrorAction SilentlyContinue |
    Select-Object IPAddress, LinkLayerAddress, InterfaceIndex,
                  @{n='State';e={$_.State.ToString()}})
[PSCustomObject]@{ adapters = $adapters; routes = $routes; neighbors = $neighbors } | ConvertTo-Json -Depth 4 -Compress
"""


@dataclass(frozen=True)
class Route:
    destination: str
    next_hop: str
    interface: str
    metric: int          # route metric + interface metric, Windows' own tie-break order
    protocol: str
    store: str            # "ActiveStore" (live) or "PersistentStore" (survives reboot)

    @property
    def is_default(self) -> bool:
        return self.destination in ("0.0.0.0/0", "::/0")


@dataclass(frozen=True)
class Neighbor:
    ip: str
    mac: str
    interface: str
    state: str

    @property
    def is_problem(self) -> bool:
        return self.state in _BAD_STATES


def _run_ps(script: str, timeout: int = 15) -> Optional[str]:
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("route/neighbor PowerShell call failed: %s", e)
        return None
    if done.returncode != 0:
        logger.warning("route/neighbor PowerShell rc=%s: %s", done.returncode, done.stderr.strip()[:300])
        return None
    return done.stdout


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@dataclass(frozen=True)
class RouteSnapshot:
    routes: List[Route]
    neighbors: List[Neighbor]


def read_routes_and_neighbors() -> Optional[RouteSnapshot]:
    """None if PowerShell or the NetTCPIP/NetAdapter cmdlets refuse."""
    raw = _run_ps(_SCRIPT)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError as e:
        logger.warning("route/neighbor output was not valid JSON: %s", e)
        return None
    adapters: Dict[str, str] = {str(k): v for k, v in (data.get("adapters") or {}).items()}

    def name_of(idx) -> str:
        return adapters.get(str(idx), f"if {idx}")

    routes = [Route(
        destination=r.get("DestinationPrefix") or "", next_hop=r.get("NextHop") or "",
        interface=name_of(r.get("InterfaceIndex")),
        metric=int(r.get("RouteMetric") or 0) + int(r.get("InterfaceMetric") or 0),
        protocol=r.get("Protocol") or "", store=r.get("Store") or "")
        for r in _as_list(data.get("routes"))]
    neighbors = [Neighbor(
        ip=n.get("IPAddress") or "", mac=n.get("LinkLayerAddress") or "",
        interface=name_of(n.get("InterfaceIndex")), state=n.get("State") or "")
        for n in _as_list(data.get("neighbors"))]
    routes.sort(key=lambda r: (not r.is_default, r.metric, r.destination))
    neighbors.sort(key=lambda n: (not n.is_problem, n.interface, n.ip))
    return RouteSnapshot(routes=routes, neighbors=neighbors)


def problem_neighbors(snapshot: Optional[RouteSnapshot]) -> List[Neighbor]:
    return [n for n in (snapshot.neighbors if snapshot else [])
            if n.is_problem and not n.ip.startswith(("ff02::", "224.", "239."))]
