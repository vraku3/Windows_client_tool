"""Energy and thermal readers: real where the hardware has them, honest where not."""
import time
from types import SimpleNamespace

import pytest

from modules.dashboard import energy as en
from modules.dashboard import pdh_util
from modules.dashboard import thermal as th
from modules.dashboard import power as pw


def test_pdh_lists_instances_of_a_real_object_and_none_for_a_missing_one():
    assert "_total" in [n.lower() for n in pdh_util.list_instances("Processor Information")]
    assert pdh_util.list_instances("No Such Counter Object") == []


def test_a_missing_counter_is_an_error_naming_it():
    with pytest.raises(OSError, match="Nope"):
        pdh_util.Query({"x": pdh_util.counter_path("Processor Information", "_Total", "Nope")})


def test_energy_meter_reads_real_watts_or_says_there_is_none():
    try:
        meter = en.EnergyMeter()
    except OSError as e:
        assert "energy meter" in str(e)
        pytest.skip("no energy meter on this hardware")
    try:
        assert meter.read() is None                       # priming sample
        time.sleep(1.2)
        reading = meter.read()
    finally:
        meter.close()
    assert reading is not None
    watts = reading.package_w if reading.package_w is not None else reading.cores_total_w
    assert 0.5 < watts < 700, watts                       # a real CPU, not zero and not a unit slip
    assert meter.watt_hours > 0


def test_process_energy_is_a_share_of_package_power_and_sums_to_it():
    rows = [("a.exe", 1, 30.0), ("b.exe", 2, 10.0), ("idle.exe", 3, 0.0)]
    out = en.estimate_process_watts(rows, 40.0)
    assert [r[0] for r in out] == ["a.exe", "b.exe"]
    assert out[0][3] == pytest.approx(30.0) and sum(r[3] for r in out) == pytest.approx(40.0)
    assert en.estimate_process_watts(rows, None) == [] and en.estimate_process_watts([], 40.0) == []


def test_kelvin_conversion():
    assert th.kelvin_to_celsius(273.15) == pytest.approx(0.0)
    assert th.kelvin_to_celsius(323.15) == pytest.approx(50.0)


def test_thermal_reader_either_reads_zones_or_reports_none_published():
    try:
        reader = th.ThermalReader()
    except OSError as e:
        assert "no thermal zones" in str(e)
        return
    try:
        reader.read()
        time.sleep(0.3)
        zones = reader.read()
    finally:
        reader.close()
    assert zones is None or all(-50 < z.celsius < 150 for z in zones)


def test_pressure_flags_a_real_limit_and_reports_the_clock_ratio():
    freqs = [pw.CoreFreq(0, 3000, 4000, 3000), pw.CoreFreq(1, 4000, 4000, 4000)]
    state = th.pressure(freqs, [4400, 3600])
    assert state["limited_cores"] == [0] and state["clock_ratio"] == pytest.approx(1.0)
    text = th.describe_pressure(state)
    assert "limiting 1 core" in text and "100% of nominal" in text
    quiet = th.describe_pressure(th.pressure([pw.CoreFreq(0, 4000, 4000, 4000)], None))
    assert "not limiting" in quiet and "Effective clock" not in quiet
