"""Thermals: real temperatures where Windows has any, and the honest proxies
where it does not. No Qt.

Windows only knows a temperature if the firmware publishes ACPI thermal zones
(the `Thermal Zone Information` counter, in Kelvin). Many desktop boards --
including the ASRock X870E this was built on -- publish none, and reading CPU
die temperature then needs a vendor driver this app deliberately does not
ship. So this reports zones when they exist, says plainly when they do not,
and shows two real (non-temperature) pressure signals alongside:

- Windows' own per-core frequency LIMIT dropping below the maximum (an actual
  throttle flag from the OS), and
- the effective-to-nominal clock ratio.

It never invents a temperature.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

from . import pdh_util

logger = logging.getLogger(__name__)

OBJECT = "Thermal Zone Information"


@dataclass(frozen=True)
class Zone:
    name: str
    celsius: float


def kelvin_to_celsius(kelvin: float) -> float:
    return kelvin - 273.15


def zone_instances() -> List[str]:
    return [n for n in pdh_util.list_instances(OBJECT) if n.lower() != "_total"]


class ThermalReader:
    """Raises OSError when the firmware publishes no thermal zones."""

    def __init__(self) -> None:
        names = zone_instances()
        if not names:
            raise OSError("no thermal zones are published by this machine's firmware")
        self._query = pdh_util.Query({n: pdh_util.counter_path(OBJECT, n, "Temperature") for n in names})

    def read(self) -> Optional[List[Zone]]:
        raw = self._query.read()
        if raw is None:
            return None
        zones = [Zone(name, kelvin_to_celsius(k)) for name, k in raw.items() if k > 0]
        return sorted(zones, key=lambda z: -z.celsius)

    def close(self) -> None:
        self._query.close()


def pressure(freqs, effective: Optional[List[int]]) -> dict:
    """The two proxy signals from `power.CoreFreq` rows and effective MHz."""
    limited = [f.index for f in freqs if f.throttled]
    ratio = None
    if effective:
        nominal = sum(f.max_mhz for f in freqs) or 0
        ratio = sum(effective) / nominal if nominal else None
    return {"limited_cores": limited, "clock_ratio": ratio}


def describe_pressure(state: dict) -> str:
    limited = state["limited_cores"]
    lines = []
    if limited:
        lines.append(f"Windows is limiting {len(limited)} core(s) below their maximum clock "
                     "(a real throttle: power, thermal or policy).")
    else:
        lines.append("Windows is not limiting any core below its maximum clock.")
    ratio = state["clock_ratio"]
    if ratio is not None:
        lines.append(f"Effective clock is {ratio * 100:.0f}% of nominal "
                     "(above 100% means boosting; a low figure under load can mean a power or "
                     "thermal limit, but idle cores also read low).")
    return "\n".join(lines)
