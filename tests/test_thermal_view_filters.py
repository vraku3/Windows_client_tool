"""Thermal Control: sensor filters/search, fan-list filters/sorts, curve presets."""
import pytest

from modules.thermal_control.engine import curves as cv
from modules.thermal_control.engine import view
from modules.thermal_control.engine.model import CONTROL, FAN, TEMPERATURE, Sensor


def _s(sid, hw, name, kind, value):
    return Sensor(sid, hw, name, kind, value, "lhm", controllable=kind == CONTROL)


SENSORS = [
    _s("/cpu/t", "AMD Ryzen 9 9950X3D", "Core (Tctl/Tdie)", TEMPERATURE, 84.0),
    _s("/nvme/t", "Samsung SSD 990 PRO 2TB", "Composite Temperature", TEMPERATURE, 82.0),
    _s("/nvme/w", "Samsung SSD 990 PRO 2TB", "Warning Temperature", TEMPERATURE, 81.0),
    _s("/nvme/c", "Samsung SSD 990 PRO 2TB", "Critical Temperature", TEMPERATURE, 84.0),
    _s("/vrm/t", "Nuvoton NCT6686D", "VRM MOS", TEMPERATURE, 52.0),
    _s("/x/t", "Nuvoton NCT6799D", "Thermistor Sensor #2", TEMPERATURE, None),
    _s("/lpc/0/fan/1", "Nuvoton NCT6799D", "CPU Fan #1", FAN, 1200.0),
    _s("/lpc/0/fan/0", "Nuvoton NCT6799D", "Chassis Fan #1", FAN, 0.0),
    _s("/lpc/0/control/1", "Nuvoton NCT6799D", "CPU Fan #1", CONTROL, 64.0),
]


def test_counts_leave_the_drive_limits_out():
    counts = view.sensor_counts(SENSORS)
    assert counts["all"] == 7 and counts["temp"] == 4 and counts["fan"] == 2 and counts["duty"] == 1


def test_hot_uses_the_drives_own_warning_limit_else_80():
    limits = view.limits_by_hardware(SENSORS)
    hot = [s.name for s in SENSORS if view.is_hot(s, limits)]
    assert hot == ["Core (Tctl/Tdie)", "Composite Temperature"]       # 84 >= 80, 82 >= warn 81


def test_problems_are_unreadable_or_critical_never_an_empty_header():
    limits = view.limits_by_hardware(SENSORS)
    problems = [s.name for s in SENSORS if view.is_problem(s, limits)]
    assert problems == ["Thermistor Sensor #2"]          # NVMe 82 < crit 84; Chassis 0 RPM is not one


def test_search_matches_hardware_or_name_and_combines_with_the_filter():
    limits = view.limits_by_hardware(SENSORS)
    shown = [s.name for s in SENSORS if view.sensor_visible(s, "all", "nuvoton fan", limits)]
    assert shown == []                                    # whole phrase, not words
    shown = [s.name for s in SENSORS if view.sensor_visible(s, "fan", "cpu", limits)]
    assert shown == ["CPU Fan #1"]
    assert not view.sensor_visible(SENSORS[2], "all", "", limits)    # a limit is never a row


ROWS = [
    view.FanRow("a", "CPU Fan #1", 1200.0, 64.0, False),
    view.FanRow("b", "AIO Pump", 2780.0, 100.0, False, pump=True),
    view.FanRow("c", "Chassis Fan #1", 0.0, 38.0, True),
    view.FanRow("gpu:x", "AMD Radeon RX 7900 XTX", None, None, False, gpu=True),
]


def test_fan_filters_count_what_they_show():
    counts = view.header_counts(ROWS)
    assert counts == {"all": 4, "connected": 3, "curve": 1, "bios": 3, "pump": 1, "gpu": 1}


@pytest.mark.parametrize("sort,first", [("name", "AIO Pump"), ("rpm", "AIO Pump"),
                                        ("duty", "AIO Pump"), ("status", "Chassis Fan #1")])
def test_fan_sorts(sort, first):
    assert view.filter_and_sort(ROWS, "all", sort)[0].name == first


def test_the_gpu_sorts_after_the_headers_by_name():
    assert view.filter_and_sort(ROWS, "all", "name")[-1].gpu


@pytest.mark.parametrize("name", [n for n, _l in cv.PRESET_LABELS])
def test_every_preset_is_a_valid_curve_for_a_fan_a_pump_and_the_gpu(name):
    fan = cv.FanCurve("c", "s", cv.preset_points(name, cv.MIN_FAN_PERCENT))
    pump = cv.FanCurve("p", "s", cv.preset_points(name, cv.MIN_PUMP_PERCENT), pump=True)
    assert cv.validate(fan) == [] and cv.validate(pump) == []
    assert min(d for _t, d in pump.points) >= cv.MIN_PUMP_PERCENT
    from modules.thermal_control.engine import adlx
    gpu = adlx.AdlxGpu("x", True, True, [(0, 0)] * 5, (23, 100), (25, 100))
    assert adlx.validate_gpu_curve(cv.preset_points(name, 23, (25, 100)), gpu) == []


def test_an_lhm_gpu_fan_control_is_never_offered_as_a_header():
    """Two controls for one fan fight; GPU fans go only through ADLX."""
    gpu_ctrl = Sensor("/gpu-amd/1/control/1", "AMD Radeon RX 7900 XTX", "GPU Fan", CONTROL, 15.0, "lhm", True)
    header = Sensor("/lpc/nct6799d/0/control/1", "Nuvoton NCT6799D", "CPU Fan #1", CONTROL, 64.0, "lhm", True)
    assert not view.is_header_control(gpu_ctrl) and view.is_header_control(header)


def test_the_service_refuses_a_header_curve_on_a_gpu_fan(qapp, tmp_path):
    from types import SimpleNamespace
    from modules.thermal_control.thermal_service import ThermalService
    svc = ThermalService(SimpleNamespace(config=None, app_data_dir=str(tmp_path), thread_pool=None),
                         str(tmp_path / "m.json"))
    curve = cv.FanCurve("/gpu-amd/1/control/1", "/amdcpu/0/temperature/2", cv.default_points(), enabled=True)
    assert any("GPU's own curve" in p for p in svc.save_curve(curve))


# ---- the Sensors panel (Qt) ------------------------------------------------------------------

def _panel(qapp):
    from modules.thermal_control.sensors_panel import SensorsPanel
    panel = SensorsPanel()
    panel.update_sensors(SENSORS)
    return panel


def test_rows_are_updated_in_place_not_rebuilt(qapp):
    """The first version cleared the tree every tick, losing sort, scroll and selection."""
    panel = _panel(qapp)
    row = panel._rows["/cpu/t"]
    panel.update_sensors([_s("/cpu/t", "AMD Ryzen 9 9950X3D", "Core (Tctl/Tdie)", TEMPERATURE, 70.0)] + SENSORS[1:])
    assert panel._rows["/cpu/t"] is row and row.text(2) == "70.0 °C"
    assert row.text(3) == "70.0" and row.text(4) == "84.0" and row.text(5) == "77.0"   # min, max, avg


def test_chips_and_search_hide_rows_and_empty_groups(qapp):
    panel = _panel(qapp)
    panel._pick("fan")
    visible = sorted(sid for sid, item in panel._rows.items() if not item.isHidden())
    assert visible == ["/lpc/0/fan/0", "/lpc/0/fan/1"]
    assert panel._groups["AMD Ryzen 9 9950X3D"].isHidden()
    panel._search.setText("chassis")
    assert [sid for sid, i in panel._rows.items() if not i.isHidden()] == ["/lpc/0/fan/0"]
    assert "Warning Temperature" not in [i.text(0) for i in panel._rows.values()]


def test_sorting_on_now_is_numeric(qapp):
    from PyQt6.QtCore import Qt
    panel = _panel(qapp)
    panel._grouped.setChecked(False)                      # flat: one sortable list
    panel._pick("fan")
    panel._tree.sortByColumn(2, Qt.SortOrder.DescendingOrder)
    tree = panel._tree
    shown = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())
             if not tree.topLevelItem(i).isHidden()]
    assert shown == ["CPU Fan #1", "Chassis Fan #1"]       # 1,200 RPM above 0, not by text
