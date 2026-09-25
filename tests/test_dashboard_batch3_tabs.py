import time
from types import SimpleNamespace

from modules.dashboard import flight_recorder as fr


def test_benchmarks_tab_runs_quick_and_remembers_the_run(qapp, tmp_path):
    from modules.dashboard.benchmarks_tab import BenchmarksTab
    tab = BenchmarksTab()
    tab.set_app(SimpleNamespace(thread_pool=None, app_data_dir=str(tmp_path)))
    tab._quick.setChecked(True)
    tab._run()
    assert tab._table.rowCount() >= 5
    assert tab._table.item(0, 1).text().endswith("MB/s")
    assert "history" in tab.status.text()
    tab._run()                                            # second run gets a "vs last run" figure
    assert tab._table.item(0, 2).text().endswith("%")
    fresh = BenchmarksTab()
    fresh.set_app(SimpleNamespace(thread_pool=None, app_data_dir=str(tmp_path)))
    assert fresh._table.rowCount() >= 5                   # previous results are shown on open


def _loaded_tab(tmp_path):
    from modules.dashboard.flight_tab import FlightTab
    t = fr.Trace(interval=1.0, machine="PC")
    for i in range(30):
        t.samples.append({"t": float(i), "cpu": float(i * 3 % 100), "mem": 40.0, "commit": 20.0,
                          "disk": 1.0, "net": 0.1, "mhz": 4000.0, "top": "x.exe", "top_cpu": 5.0})
    path = str(tmp_path / "t.trace")
    fr.save(t, path)
    tab = FlightTab()
    assert tab.open_path(path)
    return tab


def test_flight_tab_opens_scrubs_and_replays(qapp, tmp_path):
    tab = _loaded_tab(tmp_path)
    assert tab._slider.maximum() == 29 and "x.exe" in tab._readout.text()
    tab._cursor_requested(0.5)
    assert 13 <= tab._cursor <= 16
    tab._slider.setValue(20)
    assert tab._cursor == 20 and "+0m20s" in tab._readout.text()
    tab._cursor = 0
    tab._speed.setCurrentIndex(3)                         # 64x
    tab._play_btn.setChecked(True)
    for _ in range(3):
        tab._play_tick()
    assert tab._cursor > 0
    tab._play_btn.setChecked(False)


def test_flight_tab_refuses_a_bad_file_and_says_so(qapp, tmp_path):
    from modules.dashboard.flight_tab import FlightTab
    bad = tmp_path / "nope.trace"
    bad.write_text("not a trace\n", encoding="utf-8")
    tab = FlightTab()
    assert tab.open_path(str(bad)) is False and "Could not open" in tab.status.text()


def test_flight_tab_records_live_and_autosaves(qapp, tmp_path):
    from modules.dashboard.flight_tab import FlightTab
    tab = FlightTab()
    tab.set_app(SimpleNamespace(thread_pool=None, app_data_dir=str(tmp_path)))
    tab._rec_btn.setChecked(True)
    for _ in range(3):
        time.sleep(0.4)
        tab.refresh()
    tab._rec_btn.setChecked(False)
    assert tab._path and tab._path.endswith(".trace")
    saved = fr.load(tab._path)
    assert len(saved.samples) >= 2 and all(k in saved.samples[0] for k, _, _ in fr.PANELS)
