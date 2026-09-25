"""Energy: real package and per-core power where Windows exposes an energy
meter, and an honest "not available" everywhere else. No Qt.

Source: the `Energy Meter` performance counters, which Windows fills from the
CPU's RAPL interface (Intel, and recent AMD -- present on this Ryzen). `Power`
is in milliwatts. There is NO per-process power reading in Windows; the
per-process figure here is an ESTIMATE -- the process's share of CPU time
applied to package power -- and it is labelled as one everywhere it is shown.
"""
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import pdh_util

logger = logging.getLogger(__name__)

OBJECT = "Energy Meter"
_PKG = re.compile(r"^rapl_package(\d+)_pkg$", re.IGNORECASE)
_CORE = re.compile(r"^rapl_package(\d+)_core(\d+)_core$", re.IGNORECASE)


@dataclass
class EnergyReading:
    package_w: Optional[float]                    # None: no package instance
    cores_w: Dict[int, float] = field(default_factory=dict)

    @property
    def cores_total_w(self) -> float:
        return sum(self.cores_w.values())


def discover() -> Dict[str, List[str]]:
    """{'pkg': [instance...], 'core': [instance...]} present on this machine."""
    found = {"pkg": [], "core": []}
    for name in pdh_util.list_instances(OBJECT):
        if _PKG.match(name):
            found["pkg"].append(name)
        elif _CORE.match(name):
            found["core"].append(name)
    return found


class EnergyMeter:
    """Reads package + per-core power. Raises OSError if there is no meter."""

    def __init__(self) -> None:
        inst = discover()
        if not inst["pkg"] and not inst["core"]:
            raise OSError("this hardware does not expose an energy meter to Windows")
        paths = {n: pdh_util.counter_path(OBJECT, n, "Power") for n in inst["pkg"] + inst["core"]}
        self._query = pdh_util.Query(paths)
        self._inst = inst
        self.joules = 0.0
        self._last_t: Optional[float] = None

    def read(self) -> Optional[EnergyReading]:
        raw = self._query.read()
        now = time.monotonic()
        if raw is None:
            self._last_t = now
            return None
        pkg = sum(raw[n] for n in self._inst["pkg"] if n in raw) / 1000.0 if self._inst["pkg"] else None
        cores = {}
        for n in self._inst["core"]:
            if n in raw:
                cores[int(_CORE.match(n).group(2))] = raw[n] / 1000.0
        reading = EnergyReading(pkg, cores)
        watts = pkg if pkg is not None else reading.cores_total_w
        if self._last_t is not None:
            self.joules += watts * (now - self._last_t)       # integrate power over time
        self._last_t = now
        return reading

    @property
    def watt_hours(self) -> float:
        return self.joules / 3600.0

    def close(self) -> None:
        self._query.close()


def estimate_process_watts(rows: List[Tuple[str, int, float]], package_w: float,
                           total_cpu: float = None) -> List[Tuple[str, int, float, float]]:
    """(name, pid, cpu %) -> (name, pid, cpu %, ESTIMATED watts), biggest first.

    Watts = package power x (this process's CPU / all processes' CPU). It says
    who is responsible for the heat, not what an instrument measured, and it
    ignores idle power: the package draws watts with nothing running.
    """
    total = total_cpu if total_cpu is not None else sum(cpu for _, _, cpu in rows)
    if total <= 0 or package_w is None:
        return []
    out = [(name, pid, cpu, package_w * cpu / total) for name, pid, cpu in rows if cpu > 0]
    return sorted(out, key=lambda r: r[3], reverse=True)
