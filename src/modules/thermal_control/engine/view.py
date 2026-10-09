"""What the Thermal Control screen shows, decided without Qt."""
from typing import Dict, List, Optional, Tuple

from .model import CONTROL, FAN, TEMPERATURE, Sensor

#: NVMe drives publish their thresholds as if they were readings ("Warning
#: Temperature 81.0", "Critical Temperature 84.0" on the 990 PROs here).
#: They are limits: never a row of their own, never a fan-curve source.
_LIMIT_NAMES = {"warning temperature": "warn", "critical temperature": "crit"}

#: Sources most people want first in the curve's sensor list.
_PREFERRED = ("tctl", "cpu", "ccd", "gpu", "vrm", "motherboard")


def is_limit(sensor: Sensor) -> bool:
    return sensor.kind == TEMPERATURE and sensor.name.lower() in _LIMIT_NAMES


def limits_by_hardware(sensors: List[Sensor]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for s in sensors:
        if is_limit(s) and s.value is not None:
            out.setdefault(s.hardware, {})[_LIMIT_NAMES[s.name.lower()]] = s.value
    return out


def groups(sensors: List[Sensor]) -> List[Tuple[str, List[Sensor]]]:
    """Readings grouped by hardware, temperatures first, limits left out."""
    order = {TEMPERATURE: 0, FAN: 1, CONTROL: 2}
    by_hw: Dict[str, List[Sensor]] = {}
    for s in sensors:
        if not is_limit(s):
            by_hw.setdefault(s.hardware, []).append(s)
    return [(hw, sorted(rows, key=lambda s: (order.get(s.kind, 9), s.name)))
            for hw, rows in by_hw.items()]


def temperature_sources(sensors: List[Sensor]) -> List[Sensor]:
    """Temperatures a curve may follow: readable now, not limits, CPU first."""
    def rank(s: Sensor) -> Tuple[int, str]:
        name = f"{s.hardware} {s.name}".lower()
        hit = next((i for i, key in enumerate(_PREFERRED) if key in name), len(_PREFERRED))
        return hit, name
    return sorted((s for s in sensors if s.kind == TEMPERATURE and not is_limit(s)), key=rank)


def fan_for_control(control_id: str, sensors: List[Sensor]) -> Optional[Sensor]:
    """The RPM reading for a header: '/lpc/x/0/control/1' -> '/lpc/x/0/fan/1'."""
    fan_id = control_id.replace("/control/", "/fan/")
    return next((s for s in sensors if s.id == fan_id), None)


def header_line(control: Sensor, sensors: List[Sensor], on_curve: bool) -> str:
    fan = fan_for_control(control.id, sensors)
    rpm = "no RPM sensor" if fan is None else (
        "0 RPM: nothing connected, or stopped" if fan.value == 0 else fan.display())
    duty = "n/a" if control.value is None else f"{control.value:.0f}%"
    return f"{control.name}  —  {duty} · {rpm} · {'curve' if on_curve else 'BIOS'}"
