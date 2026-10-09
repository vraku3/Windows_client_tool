"""Fan curves and the rules that keep them safe. No Qt, no hardware.

A curve maps one temperature sensor to one fan header's duty. Everything
here is pure so every safety rule is tested without touching a fan:

- a curve may never LOWER the fan as temperature rises (rejected);
- a floor: never below MIN_FAN_PERCENT, or MIN_PUMP_PERCENT for a pump;
- at or above the curve's critical temperature the fan goes to 100%;
- a source that cannot be read -- missing, None, refused -- is 100%, never
  "keep the last value": a sensor that stopped answering is exactly when
  nobody knows how hot it is;
- up immediately, down gradually (MAX_DOWN_STEP per tick) with a hysteresis
  band, so a fan does not hunt up and down around one point.
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_FAN_PERCENT = 20.0
MIN_PUMP_PERCENT = 70.0
DEFAULT_CRITICAL_C = 90.0
DEFAULT_HYSTERESIS_C = 3.0
MAX_DOWN_STEP = 3.0          # percent per tick (one tick a second)


@dataclass
class FanCurve:
    control_id: str
    source_id: str
    points: List[Tuple[float, float]]            # (°C, %), temperatures increasing
    enabled: bool = False
    pump: bool = False
    critical_c: float = DEFAULT_CRITICAL_C
    hysteresis_c: float = DEFAULT_HYSTERESIS_C
    label: str = ""

    @property
    def floor(self) -> float:
        return MIN_PUMP_PERCENT if self.pump else MIN_FAN_PERCENT

    def to_dict(self) -> dict:
        return {"control_id": self.control_id, "source_id": self.source_id,
                "points": [list(p) for p in self.points], "enabled": self.enabled,
                "pump": self.pump, "critical_c": self.critical_c,
                "hysteresis_c": self.hysteresis_c, "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> "FanCurve":
        return cls(d["control_id"], d["source_id"], [tuple(map(float, p)) for p in d["points"]],
                   bool(d.get("enabled", False)), bool(d.get("pump", False)),
                   float(d.get("critical_c", DEFAULT_CRITICAL_C)),
                   float(d.get("hysteresis_c", DEFAULT_HYSTERESIS_C)), d.get("label", ""))


def default_points(pump: bool = False) -> List[Tuple[float, float]]:
    """A sane starting curve: quiet at idle, full speed well before critical."""
    if pump:
        return [(30.0, 70.0), (60.0, 85.0), (75.0, 100.0)]
    return [(35.0, 25.0), (50.0, 35.0), (65.0, 55.0), (75.0, 80.0), (85.0, 100.0)]


def validate(curve: FanCurve) -> List[str]:
    """Why this curve must not be applied; [] when it is safe."""
    errors = []
    pts = curve.points
    if len(pts) < 2:
        errors.append("a curve needs at least two points")
    temps = [t for t, _ in pts]
    duties = [d for _, d in pts]
    if any(b <= a for a, b in zip(temps, temps[1:])):
        errors.append("temperatures must strictly increase from point to point")
    if any(b < a for a, b in zip(duties, duties[1:])):
        errors.append("the fan may never slow down as the temperature rises")
    if any(not 0.0 <= d <= 100.0 for d in duties):
        errors.append("fan speeds must be between 0% and 100%")
    if any(not 0.0 <= t <= 120.0 for t in temps):
        errors.append("temperatures must be between 0 and 120 °C")
    if not curve.source_id:
        errors.append("no temperature sensor is chosen")
    if curve.critical_c <= (temps[0] if temps else 0):
        errors.append("the critical temperature is below the start of the curve")
    return errors


def interpolate(points: List[Tuple[float, float]], temp: float) -> float:
    """Duty at `temp`: linear between points, flat beyond the ends."""
    if temp <= points[0][0]:
        return points[0][1]
    for (t0, d0), (t1, d1) in zip(points, points[1:]):
        if temp <= t1:
            return d0 + (d1 - d0) * (temp - t0) / (t1 - t0)
    return points[-1][1]


@dataclass
class Decision:
    control_id: str
    percent: float
    reason: str                 # "curve", "critical", "sensor lost", "floor"


@dataclass
class CurveState:
    """Per-curve memory between ticks: the temperature the curve last acted
    on (for hysteresis) and the duty it last set (for the down-step)."""
    temp_used: Optional[float] = None
    duty: Optional[float] = None


def decide(curve: FanCurve, temp: Optional[float], state: CurveState) -> Decision:
    """One tick for one curve. Mutates `state`."""
    if temp is None:
        state.temp_used, state.duty = None, 100.0
        return Decision(curve.control_id, 100.0, "sensor lost")
    if temp >= curve.critical_c:
        state.temp_used, state.duty = temp, 100.0
        return Decision(curve.control_id, 100.0, "critical")
    used = temp
    if state.temp_used is not None and state.temp_used - curve.hysteresis_c < temp < state.temp_used:
        used = state.temp_used                  # small fall: hold, so the fan does not hunt
    target = max(curve.floor, interpolate(curve.points, used))
    if state.duty is not None and target < state.duty:
        target = max(target, state.duty - MAX_DOWN_STEP)
    state.temp_used, state.duty = used, round(target, 1)
    return Decision(curve.control_id, state.duty, "floor" if target == curve.floor else "curve")


@dataclass
class CurveSet:
    curves: List[FanCurve] = field(default_factory=list)

    def enabled(self) -> List[FanCurve]:
        return [c for c in self.curves if c.enabled and not validate(c)]

    def for_control(self, control_id: str) -> Optional[FanCurve]:
        return next((c for c in self.curves if c.control_id == control_id), None)

    def upsert(self, curve: FanCurve) -> None:
        self.curves = [c for c in self.curves if c.control_id != curve.control_id] + [curve]

    def to_list(self) -> List[dict]:
        return [c.to_dict() for c in self.curves]

    @classmethod
    def from_list(cls, rows: List[dict]) -> "CurveSet":
        out = cls()
        for row in rows or []:
            try:
                out.curves.append(FanCurve.from_dict(row))
            except (KeyError, TypeError, ValueError) as e:
                # A malformed saved curve is dropped, never half-applied.
                logger.warning("dropping an unreadable saved fan curve %r: %s", row, e)
        return out


def step(curve_set: CurveSet, temps: Dict[str, Optional[float]],
         states: Dict[str, CurveState]) -> List[Decision]:
    """One control tick across every enabled, valid curve."""
    out = []
    for curve in curve_set.enabled():
        state = states.setdefault(curve.control_id, CurveState())
        out.append(decide(curve, temps.get(curve.source_id), state))
    return out


# ---- presets -------------------------------------------------------------------------------

#: Five points each, so every preset also fits a GPU (which takes exactly five).
PRESETS = {
    "silent": [(40.0, 20.0), (55.0, 30.0), (68.0, 48.0), (78.0, 72.0), (86.0, 100.0)],
    "balanced": [(35.0, 25.0), (50.0, 35.0), (65.0, 55.0), (75.0, 80.0), (85.0, 100.0)],
    "performance": [(30.0, 35.0), (45.0, 50.0), (58.0, 70.0), (68.0, 90.0), (76.0, 100.0)],
    "full": [(30.0, 100.0), (40.0, 100.0), (50.0, 100.0), (60.0, 100.0), (70.0, 100.0)],
}
PRESET_LABELS = (("silent", "Silent"), ("balanced", "Balanced"),
                 ("performance", "Performance"), ("full", "Full speed"))


def preset_points(name: str, floor: float = 0.0,
                  temp_range: Optional[Tuple[float, float]] = None) -> List[Tuple[float, float]]:
    """A preset fitted to one fan: never below its floor (pumps, a GPU's own
    minimum) and inside a GPU's allowed temperature range."""
    lo_t, hi_t = temp_range or (0.0, 120.0)
    out = []
    for t, d in PRESETS[name]:
        t = min(max(t, lo_t), hi_t)
        if out and t <= out[-1][0]:
            t = out[-1][0] + 1
        out.append((t, max(d, floor)))
    return out
