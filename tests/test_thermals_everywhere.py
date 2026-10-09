"""Key temperatures shared by the Dashboard Thermals tab, the Overview and System Report."""
from modules.thermal_control.engine import view
from modules.thermal_control.engine.model import CONTROL, FAN, TEMPERATURE, Sensor


def _s(sid, hw, name, kind, value, source="lhm"):
    return Sensor(sid, hw, name, kind, value, source)


ELEVATED = [
    _s("/amdcpu/0/temperature/2", "AMD Ryzen 9 9950X3D", "Core (Tctl/Tdie)", TEMPERATURE, 96.0),
    _s("/amdcpu/0/temperature/5", "AMD Ryzen 9 9950X3D", "CCDs Max (Tdie)", TEMPERATURE, 80.0),
    _s("/d3dkmt/amd-radeon-tm-graphics/temperature", "AMD Radeon(TM) Graphics", "GPU", TEMPERATURE, 61.0, "d3dkmt"),
    _s("/d3dkmt/amd-radeon-rx-7900-xtx/temperature", "AMD Radeon RX 7900 XTX", "GPU", TEMPERATURE, 55.0, "d3dkmt"),
    _s("/d3dkmt/amd-radeon-rx-7900-xtx/fan", "AMD Radeon RX 7900 XTX", "GPU fan", FAN, 1300.0, "d3dkmt"),
    _s("/gpu-amd/1/temperature/3", "AMD Radeon RX 7900 XTX", "GPU Hot Spot", TEMPERATURE, 70.0),
    _s("/lpc/nct6686d/0/temperature/2", "Nuvoton NCT6686D", "VRM MOS", TEMPERATURE, 52.0),
    _s("/lpc/nct6799d/0/fan/1", "Nuvoton NCT6799D", "CPU Fan #1", FAN, 1200.0),
    _s("/lpc/nct6799d/0/fan/3", "Nuvoton NCT6799D", "AIO Pump", FAN, 2780.0),
    _s("/lpc/nct6686d/0/fan/0", "Nuvoton NCT6686D", "Water Pump", FAN, 0.0),
    _s("/nvme/1/temperature/0", "Samsung SSD 990 PRO 2TB", "Composite Temperature", TEMPERATURE, 82.0),
    _s("/nvme/1/temperature/10", "Samsung SSD 990 PRO 2TB", "Warning Temperature", TEMPERATURE, 81.0),
    _s("/nvme/1/temperature/11", "Samsung SSD 990 PRO 2TB", "Critical Temperature", TEMPERATURE, 84.0),
    _s("/lpc/nct6799d/0/control/1", "Nuvoton NCT6799D", "CPU Fan #1", CONTROL, 64.0),
]


def test_key_readings_pick_the_discrete_gpu_the_running_pump_and_each_drive():
    labels = {label: s for label, s in view.key_readings(ELEVATED)}
    assert labels["GPU"].hardware == "AMD Radeon RX 7900 XTX"          # not the hotter iGPU
    assert labels["Pump"].name == "AIO Pump"                           # not the empty Water Pump
    assert labels["Samsung SSD 990 PRO 2TB"].value == 82.0
    assert "Warning Temperature" not in [s.name for s in labels.values()]


def test_unelevated_only_the_gpu_is_there():
    gpu_only = [s for s in ELEVATED if s.source == "d3dkmt"]
    assert [label for label, _s in view.key_readings(gpu_only)] == ["GPU", "GPU fan"]


def test_alerts_use_a_drives_own_limits_and_ryzens_95_for_tctl():
    alerts = {label: (sev, limit) for sev, label, _v, limit in view.thermal_alerts(ELEVATED)}
    assert alerts["CPU (Tctl)"] == ("critical", 95.0)
    assert alerts["Samsung SSD 990 PRO 2TB"] == ("warning", 81.0)          # 82 >= its own warn 81
    assert "GPU hot spot" not in alerts and "VRM" not in alerts


def test_the_overview_turns_alerts_into_findings_and_stays_quiet_otherwise():
    from modules.dashboard import overview_health as oh
    found = oh.judge_thermals(view.thermal_alerts(ELEVATED))
    assert {f.severity for f in found} == {oh.CRITICAL, oh.WARNING}
    assert all(f.action_module == "Thermal Control" for f in found)
    assert oh.judge_thermals([]) == []


def test_the_report_section_lists_readings_and_says_why_some_are_missing():
    from modules.system_report import report_sections as rs
    gpu_only = [s for s in ELEVATED if s.source == "d3dkmt"]
    (section,), findings = rs.thermal_sections(reader=lambda: (gpu_only, "the app is not elevated"))
    assert [r[0] for r in section.rows] == ["GPU", "GPU fan"]
    assert section.note == "Partial: the app is not elevated." and findings == []
    (_section,), findings = rs.thermal_sections(reader=lambda: (ELEVATED, ""))
    assert any(f.severity == "error" and f.title.startswith("CPU (Tctl)") for f in findings)


def test_the_dashboard_tab_fills_its_table_from_a_reading(qapp):
    from modules.dashboard.thermal_tab import ThermalTab
    tab = ThermalTab()
    readings = view.key_readings(ELEVATED)
    tab._show_readings((readings, "", view.thermal_alerts(ELEVATED)))
    assert tab._readings.rowCount() == len(readings)
    assert tab._headline.text() == "96 °C  CPU (Tctl)"
