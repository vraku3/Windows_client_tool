"""Identify: CPU / GPU / Case groups, 20 s at 100%, and everything put back.

Measured 2026-10-10 on the real machine (elevated header spin, unelevated
ADLX): CPU Fan #1 1,106 -> 1,751 RPM and the RX 7900 XTX 696 -> 3,600 RPM,
each taking ~5-6 s to get there -- which is why a 5-second Identify sounded
like nothing. All four Chassis headers read 0 RPM; the AIO Pump header was
already at 100% under the BIOS.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from modules.thermal_control.engine import adlx, view  # noqa: E402
from modules.thermal_control.engine.controller import FanController  # noqa: E402
from modules.thermal_control.engine.model import CONTROL, FAN, Sensor  # noqa: E402
from test_thermal_control_engine import _FACTORY, _XTX, FakeBridge, _FakeAdlx, _service  # noqa: E402

CPU1 = "/lpc/nct6799d/0/control/1"
PUMP = "/lpc/nct6799d/0/control/3"
CH1 = "/lpc/nct6799d/0/control/0"


def headers():
    return [Sensor(CPU1, "NCT6799D", "CPU Fan #1", CONTROL, 61.6, "lhm", True),
            Sensor(CPU1.replace("control", "fan"), "NCT6799D", "CPU Fan #1", FAN, 1106.0, "lhm"),
            Sensor(PUMP, "NCT6799D", "AIO Pump", CONTROL, 100.0, "lhm", True),
            Sensor(PUMP.replace("control", "fan"), "NCT6799D", "AIO Pump", FAN, 2778.0, "lhm"),
            Sensor(CH1, "NCT6799D", "Chassis Fan #1", CONTROL, 34.1, "lhm", True),
            Sensor(CH1.replace("control", "fan"), "NCT6799D", "Chassis Fan #1", FAN, 0.0, "lhm"),
            Sensor("/gpu-amd/0/control/1", "RX 7900 XTX", "GPU Fan", CONTROL, 30.0, "lhm", True)]


# ---- groups --------------------------------------------------------------------------------

def test_identify_lasts_twenty_seconds():
    assert view.IDENTIFY_SECONDS == 20


def test_cpu_fans_and_the_pump_are_cpu_chassis_fans_are_case():
    assert [view.default_group(n) for n in ("CPU Fan #2", "AIO Pump", "Water Pump", "Chassis Fan #3")] == \
        ["cpu", "cpu", "cpu", "case"]


def test_groups_follow_the_names_unless_moved_and_never_take_a_gpu_header():
    s = headers()
    assert [c.id for c in view.group_headers(s, {}, "cpu")] == [CPU1, PUMP]
    assert [c.id for c in view.group_headers(s, {}, "case")] == [CH1]
    moved = {CPU1: "case"}                       # e.g. a case-fan hub on CPU_FAN
    assert [c.id for c in view.group_headers(s, moved, "case")] == [CPU1, CH1]
    assert view.group_headers(s, {CH1: "none"}, "case") == []


def test_the_case_card_explains_why_it_may_be_silent():
    s = headers()
    note = view.group_note("case", view.group_headers(s, {}, "case"), s)
    assert "0 RPM" in note and "hub" in note


def test_a_pump_already_at_full_speed_is_called_out():
    s = headers()
    assert "already at 100%" in view.group_line(s[2], s)
    pump_only = [s[2]]
    assert "cannot make it louder" in view.group_note("cpu", pump_only, s)


# ---- headers through the service -----------------------------------------------------------

def _header_service(qapp, tmp_path):
    from modules.thermal_control.thermal_service import ThermalService
    store = {}
    cfg = SimpleNamespace(get=lambda k, d=None: store.get(k, d), set=lambda k, v: store.__setitem__(k, v))
    svc = ThermalService(SimpleNamespace(config=cfg, app_data_dir=str(tmp_path), thread_pool=None),
                         str(tmp_path / "m.json"))
    bridge = FakeBridge()
    svc.controller = FanController(bridge, str(tmp_path / "m.json"))
    svc.sensors = headers()
    return svc, bridge, store


def test_a_group_goes_to_100_for_20_seconds_and_stop_puts_it_back(qapp, tmp_path):
    svc, bridge, _store = _header_service(qapp, tmp_path)
    started, problems = svc.identify_group("cpu")
    assert started == ["CPU Fan #1", "AIO Pump"] and problems == []
    assert bridge.duty == {CPU1: 100.0, PUMP: 100.0}
    left = svc.identifying()
    assert set(left) == {CPU1, PUMP} and all(18 <= v <= 20 for v in left.values())
    svc.stop_identify()
    assert svc.identifying() == {} and sorted(bridge.released) == sorted([CPU1, PUMP])


def test_moving_a_header_to_case_is_remembered(qapp, tmp_path):
    svc, bridge, store = _header_service(qapp, tmp_path)
    svc.set_group(CPU1, "case")
    assert store["modules.thermal_control.fan_groups"] == {CPU1: "case"}
    started, _p = svc.identify_group("case")
    assert started == ["CPU Fan #1", "Chassis Fan #1"]
    svc.stop_identify()


def test_clicking_again_restarts_the_clock_instead_of_stacking(qapp, tmp_path):
    svc, _bridge, _store = _header_service(qapp, tmp_path)
    svc.identify(CPU1)
    svc.identify(CPU1)
    assert list(svc.identifying()) == [CPU1]
    svc.stop_identify()


def test_without_the_hardware_the_reason_is_given(qapp, tmp_path):
    svc, _bridge, _store = _header_service(qapp, tmp_path)
    svc.controller = None
    svc.reason = "PawnIO driver not installed"
    started, problems = svc.identify_group("cpu")
    assert started == [] and problems                       # never a silent nothing


# ---- the GPU (ADLX) ------------------------------------------------------------------------

def _gpu(monkeypatch, tmp_path, store, factory=True, curve=None, zero=True):
    fake = _FakeAdlx(curve=curve, factory=factory)
    fake.gpu.zero_rpm = zero
    fake.install(monkeypatch)

    def set_zero(name, on, d):
        fake.calls.append(("zero", on))
        fake.gpu.zero_rpm = on
    monkeypatch.setattr(adlx, "set_zero_rpm", set_zero)
    return fake, _service(tmp_path, store)


def test_gpu_identify_runs_flat_out_with_zero_rpm_off_then_back_to_factory(qapp, tmp_path, monkeypatch):
    store = {}
    fake, svc = _gpu(monkeypatch, tmp_path, store)
    assert svc.identify_gpu(_XTX) == ""
    assert ("zero", False) in fake.calls
    assert fake.gpu.curve == [(t, 100) for t, _s in _FACTORY]
    assert store["modules.thermal_control.gpu_identify_restore"][_XTX]["factory"] is True
    svc.stop_identify()
    assert fake.calls[-1] == ("reset",) and fake.gpu.curve == _FACTORY
    assert store["modules.thermal_control.gpu_identify_restore"] == {}


def test_gpu_with_its_own_curve_gets_that_curve_and_zero_rpm_back(qapp, tmp_path, monkeypatch):
    theirs = [(30, 25), (50, 40), (63, 55), (76, 70), (85, 100)]
    store = {}
    fake, svc = _gpu(monkeypatch, tmp_path, store, factory=False, curve=theirs, zero=True)
    svc.identify_gpu(_XTX)
    svc.stop_identify()
    assert ("reset",) not in fake.calls
    assert fake.gpu.curve == theirs and fake.gpu.zero_rpm is True


def test_a_saved_curve_is_not_reapplied_over_a_running_identify(qapp, tmp_path, monkeypatch):
    mine = [[30, 23], [50, 45], [63, 60], [76, 80], [85, 100]]
    store = {"modules.thermal_control.gpu_curves": {_XTX: mine}}
    fake, svc = _gpu(monkeypatch, tmp_path, store)
    svc.identify_gpu(_XTX)
    assert fake.gpu.curve == [(t, 100) for t, _s in [tuple(p) for p in mine]]   # still flat out
    svc.stop_identify()


def test_an_identify_interrupted_by_a_crash_is_put_back_at_the_next_start(qapp, tmp_path, monkeypatch):
    flat = [(t, 100) for t, _s in _FACTORY]
    store = {"modules.thermal_control.gpu_identify_restore":
             {_XTX: {"factory": True, "curve": [list(p) for p in _FACTORY], "zero": True}}}
    fake = _FakeAdlx(curve=flat, factory=False)              # the card was left flat out
    fake.install(monkeypatch)
    monkeypatch.setattr(adlx, "set_zero_rpm", lambda *a: fake.calls.append(("zero",) + a[1:2]))
    svc = _service(tmp_path, store)
    assert ("reset",) in fake.calls and fake.gpu.curve == _FACTORY
    assert store["modules.thermal_control.gpu_identify_restore"] == {}
    assert svc.gpu_status(_XTX) is not None


def test_the_gpu_card_is_in_the_gpu_group(qapp, tmp_path, monkeypatch):
    fake, svc = _gpu(monkeypatch, tmp_path, {})
    started, problems = svc.identify_group("gpu")
    assert started == [_XTX] and problems == []
    svc.stop_identify()


def test_the_bar_counts_down_and_offers_stop(qapp, tmp_path, monkeypatch):
    from modules.thermal_control.fan_groups_bar import FanGroupsBar
    fake, svc = _gpu(monkeypatch, tmp_path, {})
    bar = FanGroupsBar(svc)
    bar.refresh([])
    _fans, _note, button = bar._cards["gpu"]
    assert button.isEnabled() and button.text().startswith("Identify (20 s")
    bar._clicked("gpu")
    assert button.text().startswith("Stop (")
    bar._clicked("gpu")
    assert button.text().startswith("Identify (") and svc.identifying() == {}
    assert not bar._cards["cpu"][2].isEnabled()             # no hardware open in this test
