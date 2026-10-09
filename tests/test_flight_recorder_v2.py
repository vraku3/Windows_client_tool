"""Flight Recorder v2: gaps, who-was-using-it, new programs, crash-safe writes,
and the rolling 7-day history."""
import json
import os
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from modules.dashboard import flight_recorder as fr
from modules.dashboard.flight_recorder import ProcRow


def _rows(*names_cpu):
    return [ProcRow(name, pid, cpu, mem, io) for pid, (name, cpu, mem, io) in enumerate(names_cpu, 1)]


# ---- gaps ----------------------------------------------------------------------------

def test_a_missing_reading_stays_a_gap_not_zero():
    t = fr.Trace(samples=[{"t": 0.0, "mhz": 4000.0}, {"t": 1.0, "mhz": None}, {"t": 2.0}])
    assert t.series("mhz") == [4000.0, None, None]
    assert t.peak("mhz") == (0, 4000.0)
    assert fr.Trace(samples=[{"t": 0.0, "gpu": None}]).peak("gpu") is None


def test_describe_says_na_for_a_gap_rather_than_zero():
    t = fr.Trace(started="2026-10-09T10:00:00", samples=[{"t": 5.0, "cpu": 12.0, "mhz": None}])
    text = t.describe(0)
    assert "n/a MHz" in text and "2026-10-09 10:00:05" in text


def test_a_sampler_with_no_clock_records_none_not_zero():
    sampler = fr.Sampler()
    sampler.sample()
    time.sleep(0.2)
    s = sampler.sample()
    assert s["mhz"] is None and s["gpu"] is None and s["power"] is None
    assert s["at"] > 0


# ---- who was using it ----------------------------------------------------------------

def test_processes_are_ranked_per_resource_with_units():
    rows = _rows(("chrome.exe", 20.0, 600 * 1048576, 2 * 1048576),
                 ("WowB.exe", 3.0, 3500 * 1048576, 12 * 1048576),
                 ("idle.exe", None, None, None))
    ranked = fr.rank_processes(rows, gpu_by_pid={1: 19.4, 2: 16.0})
    assert [r[0] for r in ranked["cpu"]] == ["chrome.exe", "WowB.exe"]          # unmeasured left out
    assert ranked["mem"][0] == ["WowB.exe", 2, 3500.0]                         # MB
    assert ranked["io"][0] == ["WowB.exe", 2, 12.0]                            # MB/s
    assert ranked["gpu"][0] == ["chrome.exe", 1, 19.4]


def test_without_gpu_counters_there_is_no_gpu_ranking_at_all():
    assert "gpu" not in fr.rank_processes(_rows(("a.exe", 1.0, 1, 1)), gpu_by_pid=None)


def test_the_idle_process_is_never_ranked():
    rows = [ProcRow("System Idle Process", 0, 90.0, 0, 0.0)] + _rows(("a.exe", 5.0, 1, 0.0))
    assert [r[0] for r in fr.rank_processes(rows)["cpu"]] == ["a.exe"]


def test_a_v1_sample_still_answers_who_from_its_single_top_process():
    t = fr.Trace(samples=[{"t": 0.0, "top": "p.exe", "top_cpu": 9.0}])
    assert t.top_at(0) == {"cpu": [["p.exe", 0, 9.0]]}


# ---- new programs ---------------------------------------------------------------------

def test_a_program_appearing_after_the_baseline_is_new_by_name_not_pid():
    batches = [_rows(("a.exe", 1.0, 1, 0.0)),
               _rows(("a.exe", 1.0, 1, 0.0), ("b.exe", 1.0, 1, 0.0)),        # still baseline
               _rows(("a.exe", 1.0, 1, 0.0), ("b.exe", 1.0, 1, 0.0), ("c.exe", 1.0, 1, 0.0)),
               _rows(("a.exe", 1.0, 1, 0.0), ("c.exe", 1.0, 1, 0.0), ("C.EXE", 1.0, 1, 0.0))]
    it = iter(batches)
    sampler = fr.Sampler(processes=lambda: next(it))
    news = [(s or {}).get("new") for s in (sampler.sample() for _ in batches)]
    assert news == [None, None, ["c.exe"], None]


def test_events_list_every_new_program_with_its_sample():
    t = fr.Trace(samples=[{"t": 0.0}, {"t": 1.0, "new": ["setup.exe"]}, {"t": 2.0, "new": ["x.exe", "y.exe"]}])
    assert t.events() == [(1, "setup.exe"), (2, "x.exe"), (2, "y.exe")]


# ---- crash-safe recording ----------------------------------------------------------------

def test_a_recording_is_on_disk_while_it_is_still_running(tmp_path):
    """v1 kept everything in memory until Stop; a crash lost it all."""
    path = str(tmp_path / "live.trace")
    rec = fr.Recorder(1.0, "PC", path)
    rec.add({"cpu": 1.0})
    rec.add({"cpu": 2.0})
    loaded = fr.load(path)                      # read while the recorder is still open
    assert [s["cpu"] for s in loaded.samples] == [1.0, 2.0]
    rec.close()


def test_a_v2_file_is_read_and_a_v1_file_still_loads(tmp_path):
    path = tmp_path / "old.trace"
    path.write_text(json.dumps({"format": fr.FORMAT, "version": 1, "interval": 1.0}) + "\n"
                    + json.dumps({"t": 0.0, "cpu": 5.0, "mhz": 0.0, "top": "a.exe"}) + "\n", encoding="utf-8")
    assert fr.load(str(path)).samples[0]["top"] == "a.exe"


# ---- rolling history ------------------------------------------------------------------

class _Clock:
    def __init__(self, when):
        self.when = when

    def __call__(self):
        return self.when


def test_history_rolls_to_a_new_file_at_midnight(tmp_path):
    clock = _Clock(datetime(2026, 10, 9, 23, 59, 50))
    history = fr.RollingHistory(str(tmp_path), "PC", clock=clock)
    history.add({"cpu": 1.0})
    first = history.path
    clock.when = datetime(2026, 10, 10, 0, 0, 5)
    history.add({"cpu": 2.0})
    second = history.path
    assert second != first and "history-20261010" in second
    history.close()
    assert len(fr.load(first).samples) == 1 and len(fr.load(second).samples) == 1


def test_pruning_keeps_seven_days_and_never_touches_a_users_recording(tmp_path):
    for day in ("20261001", "20261002", "20261003", "20261009"):
        (tmp_path / f"history-{day}-080000.trace").write_text("x", encoding="utf-8")
    (tmp_path / "recording-20260101-100000.trace").write_text("x", encoding="utf-8")
    (tmp_path / "my notes.trace").write_text("x", encoding="utf-8")
    removed = fr.prune_history(str(tmp_path), keep_days=7, now=datetime(2026, 10, 9, 12, 0))
    assert sorted(os.path.basename(p) for p in removed) == ["history-20261001-080000.trace",
                                                             "history-20261002-080000.trace"]
    left = sorted(os.listdir(tmp_path))
    assert "recording-20260101-100000.trace" in left and "my notes.trace" in left
    assert "history-20261003-080000.trace" in left


def test_pruning_a_missing_directory_is_nothing():
    assert fr.prune_history("C:\\does\\not\\exist\\flight") == []


# ---- the real machine -------------------------------------------------------------------

def test_real_live_sources_record_who_was_using_the_machine():
    from modules.dashboard.flight_sources import LiveSources
    sources = LiveSources()
    try:
        sampler = sources.sampler()
        sampler.sample()
        time.sleep(1.0)
        s = sampler.sample()
    finally:
        sources.close()
    assert s["procs"]["cpu"] or s["procs"]["mem"]
    assert s["procs"]["mem"][0][2] > 0                   # MB, from the real snapshot
    for key in ("mhz", "gpu", "power"):                  # a real reading or an honest gap
        assert s[key] is None or s[key] >= 0


# ---- the tab ----------------------------------------------------------------------------

def _who_trace():
    procs = {"cpu": [["chrome.exe", 1, 20.0], ["dwm.exe", 2, 3.5]], "mem": [["WowB.exe", 3, 3500.0]],
             "io": [["WowB.exe", 3, 12.73]]}
    return fr.Trace(started="2026-10-09T10:00:00", samples=[
        {"t": 0.0, "cpu": 10.0, "mhz": 4400.0, "procs": procs},
        {"t": 1.0, "cpu": 30.0, "mhz": None, "procs": procs, "new": ["setup.exe"]},
        {"t": 2.0, "cpu": 20.0, "mhz": 4500.0, "procs": procs}])


def test_the_moment_table_names_who_was_using_each_resource(qapp):
    from modules.dashboard.flight_tab import moment_rows
    rows = moment_rows(_who_trace(), 0)
    assert rows[0] == ["chrome.exe   20.0%", "WowB.exe   3,500 MB", "not recorded", "WowB.exe   12.73 MB/s"]
    assert rows[1][0] == "dwm.exe   3.5%" and rows[1][2] == ""


def test_programs_started_around_the_cursor_are_listed(qapp):
    from modules.dashboard.flight_tab import new_near
    assert new_near(_who_trace(), 0) == ["setup.exe"]
    assert new_near(_who_trace(), 2, window=0) == []


def test_the_tab_draws_gaps_and_fills_the_moment_table(qapp):
    from modules.dashboard.flight_tab import FlightTab
    tab = FlightTab()
    tab.resize(1100, 800)
    tab._trace = _who_trace()
    tab._cursor = 1
    tab._redraw()
    tab.grab()                                            # paints every chart, gap included
    assert tab._charts["mhz"]._values == [4400.0, None, 4500.0]
    assert tab._moment.item(0, 0).text() == "chrome.exe   20.0%"
    assert "setup.exe" in tab._new_label.text()
    assert tab._charts["cpu"]._events == [1]


def test_rolling_history_starts_with_the_app_only_when_turned_on(qapp, tmp_path):
    from modules.dashboard.flight_tab import FlightModule
    off = FlightModule()
    off.on_start(SimpleNamespace(config=None, app_data_dir=str(tmp_path), thread_pool=None))
    assert not off.history.running
    config = SimpleNamespace(get=lambda key, default=None: key == "modules.dashboard.flight.history",
                             set=lambda key, value: None)
    on = FlightModule()
    on.on_start(SimpleNamespace(config=config, app_data_dir=str(tmp_path), thread_pool=None))
    try:
        assert on.history.running
        on.history._tick()                                 # inline: no pool
        on.history._tick()
        files = os.listdir(tmp_path / "flight" / "history")
        assert len(files) == 1 and files[0].startswith("history-")
        assert len(fr.load(on.history.path).samples) >= 1
    finally:
        on.on_stop()
    assert not on.history.running


def test_the_checkbox_turns_the_history_on_and_remembers_it(qapp, tmp_path):
    from modules.dashboard.flight_tab import FlightModule
    stored = {}
    config = SimpleNamespace(get=lambda key, default=None: stored.get(key, default),
                             set=lambda key, value: stored.__setitem__(key, value))
    module = FlightModule()
    module.on_start(SimpleNamespace(config=config, app_data_dir=str(tmp_path), thread_pool=None))
    tab = module.create_widget()
    try:
        assert not tab._history_box.isChecked()
        tab._history_box.setChecked(True)
        assert module.history.running and stored["modules.dashboard.flight.history"] is True
        tab._history_box.setChecked(False)
        assert not module.history.running and stored["modules.dashboard.flight.history"] is False
    finally:
        module.on_stop()


# ---- temperatures -------------------------------------------------------------------------

def test_temperature_panels_are_recorded_and_a_missing_one_is_a_gap():
    sampler = fr.Sampler(extra={"cpu_temp": lambda: None, "gpu_temp": lambda: 52.0})
    sampler.sample()
    time.sleep(0.2)
    s = sampler.sample()
    assert s["cpu_temp"] is None and s["gpu_temp"] == 52.0
    assert {"cpu_temp", "gpu_temp"} <= {key for key, _t, _u in fr.PANELS}


def test_the_gpu_temperature_prefers_the_card_with_a_fan(monkeypatch):
    from modules.dashboard.flight_sources import LiveSources
    from modules.thermal_control.engine import gpu_kmt
    from modules.thermal_control.engine.model import FAN, TEMPERATURE, Sensor
    monkeypatch.setattr(gpu_kmt, "read_gpus", lambda: [
        Sensor("/d3dkmt/igpu/temperature", "AMD Radeon(TM) Graphics", "GPU", TEMPERATURE, 61.0, "d3dkmt"),
        Sensor("/d3dkmt/dgpu/temperature", "AMD Radeon RX 7900 XTX", "GPU", TEMPERATURE, 52.0, "d3dkmt"),
        Sensor("/d3dkmt/dgpu/fan", "AMD Radeon RX 7900 XTX", "GPU fan", FAN, 1300.0, "d3dkmt")])
    assert LiveSources.gpu_temp() == 52.0          # the card, not the hotter integrated GPU
