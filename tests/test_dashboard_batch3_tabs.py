import os
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


def test_benchmarks_tab_shows_a_trend_warning_only_once_the_bar_is_cleared(qapp, tmp_path):
    import os
    from modules.dashboard import benchmarks as bm
    from modules.dashboard.benchmarks_tab import BenchmarksTab
    d = os.path.join(str(tmp_path), "benchmarks")       # BenchmarksTab._history_dir()'s own layout
    for v in (400.0, 350.0, 300.0):                       # two drops only: not enough yet
        bm.save_run(d, [bm.BenchResult("Disk read (C:)", v, "MB/s")])
    tab = BenchmarksTab()
    tab.set_app(SimpleNamespace(thread_pool=None, app_data_dir=str(tmp_path)))
    assert tab._trend_label.isHidden()
    bm.save_run(d, [bm.BenchResult("Disk read (C:)", 250.0, "MB/s")])  # the third straight drop
    fresh = BenchmarksTab()
    fresh.set_app(SimpleNamespace(thread_pool=None, app_data_dir=str(tmp_path)))
    assert not fresh._trend_label.isHidden()
    assert "Disk read (C:)" in fresh._trend_label.text()


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


def test_saved_traces_dialog_lists_opens_and_deletes(qapp, tmp_path):
    from modules.dashboard.flight_tab import SavedTracesDialog
    t = fr.Trace(interval=1.0, machine="PC")
    for i in range(4):
        t.samples.append({"t": float(i), "cpu": 1.0, "mem": 1.0, "commit": 1.0,
                          "disk": 0.0, "net": 0.0, "mhz": 1.0, "top": "x.exe", "top_cpu": 1.0})
    path = str(tmp_path / "r.trace")
    fr.save(t, path)
    dialog = SavedTracesDialog(str(tmp_path))
    assert dialog._table.rowCount() == 1
    assert "1 recording" in dialog.status.text()
    dialog._table.selectRow(0)
    assert dialog._open_btn.isEnabled() and dialog._delete_btn.isEnabled()
    dialog._open()
    assert dialog.chosen_path == path

    dialog2 = SavedTracesDialog(str(tmp_path))
    dialog2._table.selectRow(0)
    import core.confirm as confirm_mod
    orig = confirm_mod.confirm_destructive
    try:
        import modules.dashboard.flight_tab as flight_tab_mod
        flight_tab_mod.confirm_destructive = lambda *a, **k: True
        dialog2._delete()
    finally:
        flight_tab_mod.confirm_destructive = orig
    assert dialog2._table.rowCount() == 0 and not os.path.exists(path)


def test_flight_tab_opens_from_the_saved_recordings_library(qapp, tmp_path):
    tab = _loaded_tab(tmp_path)          # already saved one trace at tmp_path/t.trace
    from modules.dashboard import flight_recorder as fr_mod
    found = fr_mod.list_saved_traces(str(tmp_path))
    assert any(t.name == "t.trace" for t in found)
