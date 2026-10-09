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


def test_a_tick_after_shutdown_never_takes_a_fan_back(tmp_path):
    bridge = FakeBridge()
    ctl = FanController(bridge, str(tmp_path / "active.json"))
    ctl.curves.upsert(_curve())
    ctl.tick()
    ctl.shutdown()
    _s, decisions = ctl.tick()                       # a worker tick landing late
    assert decisions == [] and bridge.duty == {}


# ---- GPU fans (ADLX diagnosis) ------------------------------------------------------------

from modules.thermal_control.engine import adlx  # noqa: E402

_WIN = [("AMD Radeon RX 7900 XTX", "31.0.14000.58004", "2022-12-02"),
        ("AMD Radeon(TM) Graphics", "32.0.21036.18", "2025-11-12")]


def test_a_card_adlx_cannot_see_is_explained_with_its_stale_driver():
    """Measured here: the 7900 XTX on a 2022 driver Windows Update put back."""
    found = adlx.diagnose([adlx.AdlxGpu("AMD Radeon(TM) Graphics", False, False)], "", _WIN)
    xtx = next(s for s in found if "7900" in s.name)
    assert not xtx.controllable
    assert "driver 31.0.14000.58004, 2022-12-02" in xtx.reason and "no copy" in xtx.reason
    igpu = next(s for s in found if "(TM)" in s.name)
    assert "no adjustable fan" in igpu.reason


def test_a_card_with_fan_tuning_is_reported_controllable():
    gpu = adlx.AdlxGpu("AMD Radeon RX 7900 XTX", True, True, curve=[(40, 30), (90, 100)])
    (xtx, _igpu) = adlx.diagnose([gpu], "", _WIN)
    assert xtx.controllable


def test_no_adlx_at_all_is_a_reason_not_an_empty_list():
    found = adlx.diagnose(None, "AMD's ADLX library is not installed", _WIN)
    assert all(not s.controllable and "not installed" in s.reason for s in found)


def test_non_amd_adapters_are_ignored():
    assert adlx.diagnose([], "", [("Microsoft Basic Display Adapter", "10.0", "2006-06-21")]) == []


def test_real_adlx_reads_without_crashing():
    gpus, reason = adlx.read_gpus()
    assert gpus is not None or reason


# ---- GPU fan curves (ADLX), no real GPU touched ------------------------------------------------

_XTX = "AMD Radeon RX 7900 XTX"
_FACTORY = [(30, 23), (50, 38), (63, 53), (76, 68), (85, 100)]


def _xtx(curve=None, factory=True):
    return adlx.AdlxGpu(_XTX, True, factory, list(curve or _FACTORY), (23, 100), (25, 100), True)


def test_the_copy_matching_the_discrete_cards_driver_is_chosen():
    """Measured: System32's ADLX (iGPU driver) cannot see the 7900 XTX; the
    copy in the card's own 2022 driver package can."""
    copies = [(r"C:\DS\u0199522\B025498", "32.0.21036.18"), (r"C:\DS\u0386350\B386336", "31.0.14000.58004")]
    assert adlx.choose_copy(_WIN, copies) == r"C:\DS\u0386350\B386336"
    assert adlx.choose_copy(_WIN, []) == adlx.SYSTEM_COPY


@pytest.mark.parametrize("points,fragment", [
    ([(30, 23), (50, 40), (63, 53), (76, 68)], "exactly 5"),
    ([(30, 50), (50, 40), (63, 53), (76, 68), (85, 100)], "never slow down"),
    ([(30, 10), (50, 40), (63, 53), (76, 68), (85, 100)], "between 23% and 100%"),
    ([(20, 23), (50, 40), (63, 53), (76, 68), (85, 100)], "between 25 and 100"),
])
def test_gpu_curves_outside_the_cards_limits_are_refused(points, fragment):
    assert any(fragment in e for e in adlx.validate_gpu_curve(points, _xtx()))


def test_the_real_factory_curve_is_valid():
    assert adlx.validate_gpu_curve(_FACTORY, _xtx()) == []


class _FakeAdlx:
    """Stands in for the real calls; the GPU state lives here."""
    def __init__(self, curve=None, factory=True):
        self.gpu = _xtx(curve, factory)
        self.calls = []

    def install(self, monkeypatch):
        monkeypatch.setattr(adlx, "windows_gpus", lambda: _WIN)
        monkeypatch.setattr(adlx, "adlx_copies", lambda: [])
        monkeypatch.setattr(adlx, "read_gpus", lambda d: ([self.gpu], ""))
        monkeypatch.setattr(adlx, "set_curve", self.set_curve)
        monkeypatch.setattr(adlx, "reset_to_factory", self.reset)

    def set_curve(self, name, points, d):
        self.calls.append(("set", [tuple(p) for p in points]))
        self.gpu.curve, self.gpu.at_factory = [tuple(map(int, p)) for p in points], False
        return self.gpu.curve

    def reset(self, name, d):
        self.calls.append(("reset",))
        self.gpu.curve, self.gpu.at_factory = list(_FACTORY), True
        return True


def _service(tmp_path, store):
    from types import SimpleNamespace
    from modules.thermal_control.thermal_service import ThermalService
    cfg = SimpleNamespace(get=lambda k, d=None: store.get(k, d), set=lambda k, v: store.__setitem__(k, v))
    svc = ThermalService(SimpleNamespace(config=cfg, app_data_dir=str(tmp_path), thread_pool=None),
                         str(tmp_path / "m.json"))
    svc._check_gpu_fans()
    return svc


def test_applying_remembers_the_factory_state_and_back_to_factory_resets(qapp, tmp_path, monkeypatch):
    fake = _FakeAdlx()
    fake.install(monkeypatch)
    store = {}
    svc = _service(tmp_path, store)
    mine = [(30, 23), (50, 45), (63, 60), (76, 80), (85, 100)]
    assert svc.apply_gpu_curve(_XTX, mine) == []
    assert store["modules.thermal_control.gpu_before"][_XTX]["factory"] is True
    svc.gpu_back_to_factory(_XTX)
    assert fake.calls[-1] == ("reset",) and store["modules.thermal_control.gpu_curves"] == {}


def test_a_gpu_that_had_other_tuning_gets_its_own_curve_back_not_a_full_reset(qapp, tmp_path, monkeypatch):
    theirs = [(30, 25), (50, 40), (63, 55), (76, 70), (85, 100)]
    fake = _FakeAdlx(curve=theirs, factory=False)
    fake.install(monkeypatch)
    svc = _service(tmp_path, {})
    svc.apply_gpu_curve(_XTX, [(30, 30), (50, 45), (63, 60), (76, 80), (85, 100)])
    svc.gpu_back_to_factory(_XTX)
    assert ("reset",) not in fake.calls and fake.calls[-1] == ("set", theirs)


def test_a_saved_curve_the_driver_dropped_is_reapplied_at_start(qapp, tmp_path, monkeypatch):
    fake = _FakeAdlx()                                   # the driver is back at factory
    fake.install(monkeypatch)
    mine = [[30, 23], [50, 45], [63, 60], [76, 80], [85, 100]]
    svc = _service(tmp_path, {"modules.thermal_control.gpu_curves": {_XTX: mine}})
    assert fake.calls == [("set", [tuple(p) for p in mine])]
    assert "re-applied" in svc.gpu_status(_XTX).reason


def test_the_exit_net_still_hands_fans_back_after_qt_is_gone(qapp, tmp_path):
    """atexit runs after Qt deleted the service's timer; shutdown() raised on
    the timer and never reached the fans."""
    from types import SimpleNamespace
    from PyQt6 import sip
    from modules.thermal_control.thermal_service import ThermalService
    svc = ThermalService(SimpleNamespace(config=None, app_data_dir=str(tmp_path), thread_pool=None),
                         str(tmp_path / "m.json"))
    bridge = FakeBridge()
    svc.controller = FanController(bridge, str(tmp_path / "m.json"))
    svc.controller.curves.upsert(_curve())
    svc.controller.tick()
    sip.delete(svc._timer)
    svc._atexit()
    assert bridge.released == [FAN1]


def test_identify_spins_up_then_hands_a_bare_header_back(tmp_path):
    bridge = FakeBridge()
    ctl = FanController(bridge, str(tmp_path / "active.json"))
    ctl.boost(FAN1)
    assert bridge.duty[FAN1] == 100.0 and (tmp_path / "active.json").exists()     # crash-safe
    ctl.end_boost(FAN1)
    assert bridge.released == [FAN1] and not (tmp_path / "active.json").exists()


def test_identify_on_a_curved_header_returns_to_the_curve_not_the_bios(tmp_path):
    bridge = FakeBridge(temp=50.0)
    ctl = FanController(bridge, str(tmp_path / "active.json"))
    ctl.curves.upsert(_curve())
    ctl.tick()
    ctl.boost(FAN1)
    ctl.end_boost(FAN1)
    assert bridge.released == []
    ctl.tick()
    assert bridge.duty[FAN1] == 40.0                     # the curve at 50 C, straight away
