"""Thermal control engine: curve safety rules, the controller and crash recovery,
all against a fake bridge -- no fan is touched by these tests."""
import json

import pytest

from modules.thermal_control.engine import curves as cv
from modules.thermal_control.engine import lhm_bridge
from modules.thermal_control.engine.controller import FanController
from modules.thermal_control.engine.model import CONTROL, TEMPERATURE, Sensor, is_pump

CPU = "/amdcpu/0/temperature/2"
FAN1 = "/lpc/nct6799d/0/control/1"
PUMP = "/lpc/nct6799d/0/control/3"


def _curve(**kw):
    base = dict(control_id=FAN1, source_id=CPU, points=[(40.0, 30.0), (60.0, 50.0), (80.0, 100.0)],
                enabled=True)
    base.update(kw)
    return cv.FanCurve(**base)


# ---- validation -------------------------------------------------------------------------

def test_a_sane_curve_validates():
    assert cv.validate(_curve()) == []
    assert cv.validate(cv.FanCurve(FAN1, CPU, cv.default_points())) == []
    assert cv.validate(cv.FanCurve(PUMP, CPU, cv.default_points(pump=True), pump=True)) == []


@pytest.mark.parametrize("points,fragment", [
    ([(40.0, 60.0), (60.0, 40.0)], "never slow down"),
    ([(60.0, 30.0), (40.0, 50.0)], "strictly increase"),
    ([(40.0, 30.0)], "at least two"),
    ([(40.0, 30.0), (60.0, 130.0)], "between 0% and 100%"),
])
def test_dangerous_curves_are_rejected(points, fragment):
    assert any(fragment in e for e in cv.validate(_curve(points=points)))


def test_an_invalid_curve_is_never_applied_even_if_enabled():
    bad = _curve(points=[(40.0, 60.0), (60.0, 40.0)])
    assert cv.CurveSet([bad]).enabled() == []


# ---- decisions ----------------------------------------------------------------------------

def test_interpolation_is_linear_and_flat_beyond_the_ends():
    pts = [(40.0, 30.0), (60.0, 50.0), (80.0, 100.0)]
    assert cv.interpolate(pts, 20.0) == 30.0
    assert cv.interpolate(pts, 50.0) == 40.0
    assert cv.interpolate(pts, 70.0) == 75.0
    assert cv.interpolate(pts, 99.0) == 100.0


def test_a_lost_sensor_drives_the_fan_to_full():
    d = cv.decide(_curve(), None, cv.CurveState())
    assert (d.percent, d.reason) == (100.0, "sensor lost")


def test_the_critical_temperature_overrides_the_curve():
    curve = _curve(points=[(40.0, 20.0), (95.0, 40.0)], critical_c=90.0)
    assert cv.decide(curve, 91.0, cv.CurveState()).percent == 100.0


def test_the_floor_holds_for_fans_and_higher_for_pumps():
    low = [(40.0, 0.0), (80.0, 100.0)]
    assert cv.decide(_curve(points=low), 30.0, cv.CurveState()).percent == cv.MIN_FAN_PERCENT
    pump = _curve(control_id=PUMP, points=low, pump=True)
    assert cv.decide(pump, 30.0, cv.CurveState()).percent == cv.MIN_PUMP_PERCENT


def test_up_is_immediate_down_is_gradual():
    curve, state = _curve(), cv.CurveState()
    assert cv.decide(curve, 80.0, state).percent == 100.0
    assert cv.decide(curve, 40.0, state).percent == 100.0 - cv.MAX_DOWN_STEP
    assert cv.decide(curve, 40.0, state).percent == 100.0 - 2 * cv.MAX_DOWN_STEP


def test_a_small_temperature_dip_is_held_by_hysteresis():
    curve, state = _curve(hysteresis_c=3.0), cv.CurveState()
    first = cv.decide(curve, 60.0, state).percent
    assert cv.decide(curve, 58.5, state).percent == first      # within the band: no change
    assert cv.decide(curve, 55.0, state).percent < first       # beyond it: starts to fall


def test_curves_round_trip_through_their_saved_form():
    saved = cv.CurveSet([_curve(label="CPU fan")]).to_list()
    loaded = cv.CurveSet.from_list(json.loads(json.dumps(saved)))
    assert loaded.curves[0].points == [(40.0, 30.0), (60.0, 50.0), (80.0, 100.0)]
    assert cv.CurveSet.from_list([{"nonsense": 1}]).curves == []


# ---- model ---------------------------------------------------------------------------------

def test_pumps_are_recognised_by_their_header_name():
    assert is_pump("AIO Pump") and is_pump("Water Pump") and not is_pump("CPU Fan #1")


def test_a_zero_temperature_is_a_refused_read():
    """Unelevated, LibreHardwareMonitor reports Tctl as 0.0 (measured)."""
    assert lhm_bridge.clean_value(TEMPERATURE, 0.0) is None
    assert lhm_bridge.clean_value(TEMPERATURE, float("nan")) is None
    assert lhm_bridge.clean_value(TEMPERATURE, 70.6) == 70.6
    assert lhm_bridge.clean_value(CONTROL, 0.0) == 0.0          # 0% duty is a real value


# ---- the controller, against a fake bridge ----------------------------------------------------

class FakeBridge:
    def __init__(self, temp=70.0, fail_set=False):
        self.temp, self.fail_set = temp, fail_set
        self.duty, self.released = {}, []

    def read(self):
        return [Sensor(CPU, "CPU", "Tctl", TEMPERATURE, self.temp, "lhm"),
                Sensor(FAN1, "SuperIO", "CPU Fan #1", CONTROL, 50.0, "lhm", True)]

    def set_percent(self, cid, pct):
        if self.fail_set:
            raise RuntimeError("driver said no")
        self.duty[cid] = pct

    def release(self, cid):
        self.released.append(cid)
        self.duty.pop(cid, None)

    def release_all(self):
        for cid in list(self.duty):
            self.release(cid)
        return []

    @property
    def touched(self):
        return sorted(self.duty)


def test_a_tick_applies_the_curve_and_marks_the_header_as_ours(tmp_path):
    bridge = FakeBridge(temp=70.0)
    ctl = FanController(bridge, str(tmp_path / "active.json"))
    ctl.curves.upsert(_curve())
    _sensors, decisions = ctl.tick()
    assert bridge.duty[FAN1] == 75.0 and decisions[0].reason == "curve"
    assert list(json.load(open(tmp_path / "active.json"))["controls"]) == [FAN1]


def test_a_clean_shutdown_hands_everything_back_and_removes_the_marker(tmp_path):
    bridge = FakeBridge()
    ctl = FanController(bridge, str(tmp_path / "active.json"))
    ctl.curves.upsert(_curve())
    ctl.tick()
    assert ctl.shutdown() == []
    assert bridge.released == [FAN1] and not (tmp_path / "active.json").exists()


def test_after_a_crash_the_next_start_hands_the_fans_back_first(tmp_path):
    marker = tmp_path / "active.json"
    marker.write_text(json.dumps({"pid": 1234, "controls": [FAN1, PUMP]}), encoding="utf-8")
    bridge = FakeBridge()
    ctl = FanController(bridge, str(marker))
    assert ctl.recover() == [FAN1, PUMP]
    assert bridge.released == [FAN1, PUMP] and not marker.exists()


def test_disabling_a_curve_hands_that_header_back(tmp_path):
    bridge = FakeBridge()
    ctl = FanController(bridge, str(tmp_path / "active.json"))
    ctl.curves.upsert(_curve())
    ctl.tick()
    ctl.release(FAN1)
    assert bridge.released == [FAN1] and not (tmp_path / "active.json").exists()


def test_a_failed_write_is_reported_not_raised(tmp_path):
    ctl = FanController(FakeBridge(fail_set=True), str(tmp_path / "active.json"))
    ctl.curves.upsert(_curve())
    ctl.tick()
    assert ctl.last_errors and "driver said no" in ctl.last_errors[0]


def test_a_sensor_that_vanishes_drives_its_fan_to_full(tmp_path):
    bridge = FakeBridge(temp=None)
    ctl = FanController(bridge, str(tmp_path / "active.json"))
    ctl.curves.upsert(_curve())
    ctl.tick()
    assert bridge.duty[FAN1] == 100.0


class SavingBridge(FakeBridge):
    """Captures BIOS values like the real chip does on first takeover."""
    def __init__(self):
        super().__init__()
        self.restored = []

    def saved_default(self, cid):
        return {"mode": 0x40, "pwm": 97}

    def restore_saved_default(self, cid, mode, pwm):
        self.restored.append((cid, mode, pwm))


def test_the_marker_carries_the_bios_mode_and_recovery_puts_it_back(tmp_path):
    """Measured: a new process's plain release 'restored' the stuck manual
    mode, so the captured BIOS values must survive in the marker."""
    marker = str(tmp_path / "active.json")
    first = FanController(SavingBridge(), marker)
    first.curves.upsert(_curve())
    first.tick()                                           # ...and then the app dies
    assert json.load(open(marker))["controls"][FAN1] == {"mode": 0x40, "pwm": 97}
    second_bridge = SavingBridge()
    assert FanController(second_bridge, marker).recover() == [FAN1]
    assert second_bridge.restored == [(FAN1, 0x40, 97)] and second_bridge.released == []


def test_an_old_style_marker_without_values_is_released_and_flagged(tmp_path):
    marker = tmp_path / "active.json"
    marker.write_text(json.dumps({"controls": [FAN1]}), encoding="utf-8")
    ctl = FanController(FakeBridge(), str(marker))
    assert ctl.recover() == [FAN1]
    assert "may still be fixed" in ctl.last_errors[0]
