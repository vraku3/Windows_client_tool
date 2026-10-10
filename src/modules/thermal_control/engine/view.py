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


# ---- filtering and sorting ----------------------------------------------------------------

#: A temperature with no published limit counts as hot from here up.
HOT_C = 80.0


def _limits_for(sensor: Sensor, limits: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    return limits.get(sensor.hardware, {})


def is_hot(sensor: Sensor, limits: Dict[str, Dict[str, float]]) -> bool:
    if sensor.kind != TEMPERATURE or sensor.value is None or is_limit(sensor):
        return False
    return sensor.value >= _limits_for(sensor, limits).get("warn", HOT_C)


def is_problem(sensor: Sensor, limits: Dict[str, Dict[str, float]]) -> bool:
    """Could not be read, or at/over a critical limit. An empty fan header
    (0 RPM) is NOT a problem: five of eight are empty on this board."""
    if is_limit(sensor):
        return False
    if sensor.value is None:
        return True
    crit = _limits_for(sensor, limits).get("crit")
    return sensor.kind == TEMPERATURE and crit is not None and sensor.value >= crit


SENSOR_FILTERS = (
    ("all", "All", lambda s, lim: True),
    ("temp", "Temperatures", lambda s, lim: s.kind == TEMPERATURE),
    ("fan", "Fans", lambda s, lim: s.kind == FAN),
    ("duty", "Duty", lambda s, lim: s.kind == CONTROL),
    ("hot", "Hot", is_hot),
    ("problem", "Problems", is_problem),
)


def sensor_counts(sensors: List[Sensor]) -> Dict[str, int]:
    limits = limits_by_hardware(sensors)
    shown = [s for s in sensors if not is_limit(s)]
    return {key: sum(1 for s in shown if fn(s, limits)) for key, _l, fn in SENSOR_FILTERS}


def sensor_visible(sensor: Sensor, key: str, text: str, limits: Dict[str, Dict[str, float]]) -> bool:
    if is_limit(sensor):
        return False
    fn = next((f for k, _l, f in SENSOR_FILTERS if k == key), SENSOR_FILTERS[0][2])
    needle = (text or "").strip().lower()
    return fn(sensor, limits) and (not needle or needle in f"{sensor.hardware} {sensor.name}".lower())


# ---- the fan list ---------------------------------------------------------------------------

#: (key, label, predicate(row)) over FanRow.
HEADER_FILTERS = (
    ("all", "All", lambda r: True),
    ("connected", "Connected", lambda r: r.connected),
    ("curve", "On a curve", lambda r: r.on_curve),
    ("bios", "BIOS / driver", lambda r: not r.on_curve),
    ("pump", "Pumps", lambda r: r.pump),
    ("gpu", "GPU", lambda r: r.gpu),
)

HEADER_SORTS = (
    ("name", "Name"),
    ("rpm", "Speed (RPM)"),
    ("duty", "Duty (%)"),
    ("status", "Curve first"),
)


class FanRow:
    """One entry in the fan list, header or GPU, with what the filters and sorts need."""

    def __init__(self, key: str, name: str, rpm: Optional[float], duty: Optional[float],
                 on_curve: bool, pump: bool = False, gpu: bool = False, detail: str = "") -> None:
        self.key, self.name, self.rpm, self.duty = key, name, rpm, duty
        self.on_curve, self.pump, self.gpu, self.detail = on_curve, pump, gpu, detail

    @property
    def connected(self) -> bool:
        """Spinning, or a GPU (its fan may be parked by zero-RPM and still be there)."""
        return self.gpu or bool(self.rpm)


def header_counts(rows: List[FanRow]) -> Dict[str, int]:
    return {key: sum(1 for r in rows if fn(r)) for key, _l, fn in HEADER_FILTERS}


def filter_and_sort(rows: List[FanRow], key: str, sort: str) -> List[FanRow]:
    fn = next((f for k, _l, f in HEADER_FILTERS if k == key), HEADER_FILTERS[0][2])
    kept = [r for r in rows if fn(r)]
    if sort == "rpm":
        kept.sort(key=lambda r: (-(r.rpm or 0), r.name.lower()))
    elif sort == "duty":
        kept.sort(key=lambda r: (-(r.duty or 0), r.name.lower()))
    elif sort == "status":
        kept.sort(key=lambda r: (not r.on_curve, not r.connected, r.name.lower()))
    else:
        kept.sort(key=lambda r: (r.gpu, r.name.lower()))
    return kept


def is_header_control(sensor: Sensor) -> bool:
    """A motherboard fan header a curve may drive.

    Measured 2026-10-09: once the GPU's matching ADL is loaded in the
    process (for the ADLX fan curve), LibreHardwareMonitor ALSO sees the
    RX 7900 XTX and offers its own 'GPU Fan' control (/gpu-amd/.../control).
    Two controls for one fan fight each other, so GPU fans go only through
    ADLX (the GPU firmware's own curve) and never through a header curve."""
    return sensor.kind == CONTROL and sensor.controllable and not sensor.id.startswith("/gpu")


# ---- the few readings that matter (Dashboard, Overview, System Report) ---------------------

#: (label, kind, predicate) -- the first sensor matching each, in this order.
_KEY = (
    ("CPU (Tctl)", TEMPERATURE, lambda s: "tctl" in s.name.lower()),
    ("CPU CCD max", TEMPERATURE, lambda s: s.name.lower().startswith("ccds max")),
    ("GPU", TEMPERATURE, lambda s: s.source == "d3dkmt" and "(tm)" not in s.hardware.lower()),
    ("GPU hot spot", TEMPERATURE, lambda s: "hot spot" in s.name.lower()),
    ("VRM", TEMPERATURE, lambda s: "vrm" in s.name.lower()),
    ("Motherboard", TEMPERATURE, lambda s: s.name.lower() == "motherboard"),
    ("CPU fan", FAN, lambda s: s.name.lower() == "cpu fan #1"),
    ("Pump", FAN, lambda s: "pump" in s.name.lower() and bool(s.value)),
    ("GPU fan", FAN, lambda s: s.source == "d3dkmt"),
)


def key_readings(sensors: List[Sensor]) -> List[Tuple[str, Sensor]]:
    """The readings worth a glance, labelled; plus every drive's composite
    temperature. Missing hardware is simply absent (unelevated: GPU only)."""
    out: List[Tuple[str, Sensor]] = []
    for label, kind, match in _KEY:
        hit = next((s for s in sensors if s.kind == kind and not is_limit(s) and match(s)), None)
        if hit is not None:
            out.append((label, hit))
    for s in sensors:
        if s.kind == TEMPERATURE and s.name.lower() == "composite temperature":
            out.append((s.hardware, s))
    return out


#: Temperatures that call for attention when the hardware publishes no limit.
#: Ryzen 9000 throttles at Tctl 95 C; RDNA3 junction (hot spot) at 110 C.
DEFAULT_LIMITS = {"CPU (Tctl)": (90.0, 95.0), "CPU CCD max": (90.0, 95.0),
                  "GPU hot spot": (100.0, 108.0), "GPU": (90.0, 100.0), "VRM": (100.0, 115.0)}


def thermal_alerts(sensors: List[Sensor]) -> List[Tuple[str, str, float, float]]:
    """(severity 'warning'/'critical', label, value, limit) for every key
    temperature at or over its warning limit -- the drive's own published
    limits for drives, DEFAULT_LIMITS for the rest."""
    limits = limits_by_hardware(sensors)
    out = []
    for label, s in key_readings(sensors):
        if s.kind != TEMPERATURE or s.value is None:
            continue
        own = limits.get(s.hardware, {})
        warn, crit = (own.get("warn"), own.get("crit")) if own else DEFAULT_LIMITS.get(label, (None, None))
        if crit is not None and s.value >= crit:
            out.append(("critical", label, s.value, crit))
        elif warn is not None and s.value >= warn:
            out.append(("warning", label, s.value, warn))
    return out


# ---- fan groups: CPU / GPU / Case (the Identify cards) --------------------------------------

#: (key, label). GPU fans are the GPU's own (ADLX), never a motherboard header.
FAN_GROUPS = (("cpu", "CPU"), ("gpu", "GPU"), ("case", "Case"))
HEADER_GROUPS = (("cpu", "CPU"), ("case", "Case"), ("none", "Not in a group"))

#: How long Identify holds a fan at 100%. Measured 2026-10-10: the CPU fans
#: here need ~5 s to climb from 62% to full (1,106 -> 1,751 RPM) and the
#: RX 7900 XTX ~6 s (696 -> 3,600 RPM) -- a 5-second Identify ended just as
#: the fan got there, which is why it sounded like nothing happened.
IDENTIFY_SECONDS = 20


def default_group(name: str) -> str:
    """CPU fans and the cooler's pump are "cpu"; chassis/system fans "case"."""
    low = name.lower()
    if "cpu" in low or "pump" in low or "aio" in low:
        return "cpu"
    return "case"


def header_group(sensor: Sensor, overrides: Dict[str, str]) -> str:
    return overrides.get(sensor.id) or default_group(sensor.name)


def group_headers(sensors: List[Sensor], overrides: Dict[str, str], group: str) -> List[Sensor]:
    return [s for s in sensors if is_header_control(s) and header_group(s, overrides) == group]


def group_line(control: Sensor, sensors: List[Sensor]) -> str:
    """'CPU Fan #1 1,106 RPM', with the facts that explain what Identify will do."""
    fan = fan_for_control(control.id, sensors)
    if fan is None or fan.value is None:
        rpm = "no RPM reading"
    elif fan.value == 0:
        rpm = "0 RPM (nothing reporting speed)"
    else:
        rpm = fan.display()
    full = control.value is not None and control.value >= 98
    return f"{control.name}: {rpm}" + ("  — already at 100%" if full else "")


def group_note(group: str, members: List[Sensor], sensors: List[Sensor]) -> str:
    """What a card says under its fan list -- above all, why it may be silent."""
    if group == "case" and members and not any(
            (fan_for_control(c.id, sensors) or Sensor("", "", "", FAN, None, "")).value for c in members):
        return ("No case fan reports a speed on the motherboard (every Chassis header reads 0 RPM). "
                "Identify still drives them -- a fan without a speed wire spins up too. If your case "
                "fans run from a hub on another header, set that header's group to Case.")
    if members and all(c.value is not None and c.value >= 98 for c in members):
        return "Already at 100% under the BIOS, so Identify cannot make it louder."
    return ""
