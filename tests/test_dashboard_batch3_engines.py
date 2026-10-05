"""Benchmarks and Flight Recorder logic (no Qt)."""
import json
import time

import pytest

from modules.dashboard import benchmarks as bm
from modules.dashboard import flight_recorder as fr


# ---- benchmarks -------------------------------------------------------------------------

def test_cpu_benchmarks_return_positive_rates_and_multi_beats_single():
    single = bm.cpu_single(seconds=0.5)
    multi = bm.cpu_multi(seconds=0.5, threads=4)
    assert single.value > 10 and multi.value > 10
    assert multi.value > single.value * 1.5, (single.value, multi.value)   # threads really run in parallel


def test_memory_benchmark_reports_plausible_bandwidth():
    result = bm.memory_bandwidth(size_mb=16, passes=2)
    assert 500 < result.value < 1_000_000, result.value


def test_disk_benchmark_measures_and_cleans_up(tmp_path):
    results = bm.disk_sequential(str(tmp_path), size_mb=8)
    assert [r.unit for r in results] == ["MB/s", "MB/s"]
    assert all(r.value > 1 for r in results)
    assert list(tmp_path.iterdir()) == []                  # the temp file is gone


def test_a_cancelled_disk_run_still_removes_its_file(tmp_path):
    with pytest.raises(bm.Cancelled):
        bm.disk_sequential(str(tmp_path), cancelled=lambda: True, size_mb=8)
    assert list(tmp_path.iterdir()) == []


def test_run_all_quick_stops_when_cancelled_and_reports_partial(tmp_path):
    steps = []
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 40                              # cancel partway through
    results = bm.run_all(str(tmp_path), cancelled=cancelled, on_step=steps.append, quick=True)
    assert steps and len(results) < 5


def test_run_all_reports_an_unwritable_folder_instead_of_guessing():
    results = bm.run_all("Z:" + "/no/such/place", quick=True)
    disk = [r for r in results if r.name == "Disk"]
    assert disk and "could not run" in disk[0].detail


def test_history_saves_caps_and_compares(tmp_path):
    d = str(tmp_path)
    first = [bm.BenchResult("CPU single-thread", 100.0, "MB/s")]
    bm.save_run(d, first, "PC")
    second = [bm.BenchResult("CPU single-thread", 120.0, "MB/s")]
    change = bm.compare(bm.load_history(d), second)
    assert change == {"CPU single-thread": pytest.approx(20.0)}
    for _ in range(bm.HISTORY_KEEP + 5):
        bm.save_run(d, first)
    assert len(bm.load_history(d)) == bm.HISTORY_KEEP


def test_a_corrupt_history_file_is_reported_as_empty_not_a_crash(tmp_path):
    (tmp_path / "benchmarks.json").write_text("{not json", encoding="utf-8")
    assert bm.load_history(str(tmp_path)) == []


def _save(d, name, value):
    bm.save_run(d, [bm.BenchResult(name, value, "MB/s")])


def test_trend_flags_a_test_that_declined_every_run_in_a_row(tmp_path):
    d = str(tmp_path)
    for v in (400.0, 350.0, 300.0, 250.0):               # four straight drops
        _save(d, "Disk read (C:)", v)
    declines = bm.trend(bm.load_history(d), min_runs=3)
    assert "Disk read (C:)" in declines
    assert declines["Disk read (C:)"]["values"] == [400.0, 350.0, 300.0, 250.0]
    assert declines["Disk read (C:)"]["pct_change"] == pytest.approx(-37.5)


def test_trend_does_not_flag_a_single_bad_run_inside_a_longer_recovery(tmp_path):
    """One slow run that then recovers is noise, not a trend -- it must not
    appear just because SOME earlier pair of points happened to drop."""
    d = str(tmp_path)
    for v in (300.0, 200.0, 350.0, 360.0):               # dip then recovers twice
        _save(d, "Disk read (C:)", v)
    assert bm.trend(bm.load_history(d), min_runs=3) == {}


def test_trend_needs_enough_history_before_it_will_say_anything(tmp_path):
    d = str(tmp_path)
    for v in (300.0, 250.0):                             # one drop, not three
        _save(d, "Disk read (C:)", v)
    assert bm.trend(bm.load_history(d), min_runs=3) == {}


def test_trend_on_a_real_bench_run_round_trip_does_not_crash_or_false_flag(tmp_path):
    """Real CPU/memory/disk numbers from THIS machine, run back-to-back and
    saved through the real history file -- trend() must handle genuine
    measured noise without raising, and a mere two real runs (one comparison)
    can never clear the three-drop bar on its own."""
    d = str(tmp_path)
    first = bm.run_all(d, quick=True)
    bm.save_run(d, first)
    second = bm.run_all(d, quick=True)
    bm.save_run(d, second)
    declines = bm.trend(bm.load_history(d), min_runs=3)
    assert declines == {}


# ---- flight recorder --------------------------------------------------------------------

def _trace(n=5):
    t = fr.Trace(interval=1.0, started="2026-09-25T10:00:00", machine="PC")
    for i in range(n):
        t.samples.append({"t": float(i), "cpu": i * 10.0, "mem": 40.0, "commit": 20.0,
                          "disk": float(i), "net": 0.5, "mhz": 4000.0, "top": f"p{i}.exe", "top_cpu": 9.0})
    return t


def test_trace_roundtrips_through_a_file(tmp_path):
    path = str(tmp_path / "x.trace")
    fr.save(_trace(), path)
    loaded = fr.load(path)
    assert loaded.machine == "PC" and len(loaded.samples) == 5
    assert loaded.samples[3]["top"] == "p3.exe"


def test_a_truncated_recording_loads_up_to_its_last_good_line(tmp_path):
    path = tmp_path / "cut.trace"
    fr.save(_trace(), str(path))
    text = path.read_text(encoding="utf-8")
    path.write_text(text[:-25], encoding="utf-8")          # chop the final line in half
    assert len(fr.load(str(path)).samples) == 4


def test_a_non_trace_file_is_refused(tmp_path):
    bad = tmp_path / "bad.trace"
    bad.write_text("hello\n", encoding="utf-8")
    with pytest.raises(ValueError):
        fr.load(str(bad))
    other = tmp_path / "other.trace"
    other.write_text(json.dumps({"format": "something-else"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        fr.load(str(other))
    newer = tmp_path / "newer.trace"
    newer.write_text(json.dumps({"format": fr.FORMAT, "version": 99}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="newer"):
        fr.load(str(newer))


def test_nearest_peak_and_describe():
    t = _trace()
    assert t.nearest(2.4) == 2 and t.nearest(2.6) == 3 and t.nearest(-5) == 0 and t.nearest(99) == 4
    assert t.peak("cpu") == (4, 40.0)
    assert "p2.exe" in t.describe(2) and "CPU 20%" in t.describe(2)
    assert fr.Trace().nearest(1.0) is None and fr.Trace().peak("cpu") is None


def test_the_recorder_stamps_time_skips_the_priming_sample_and_stops_when_full(monkeypatch):
    rec = fr.Recorder(interval=1.0)
    assert rec.add(None) is True and not rec.trace.samples
    assert rec.add({"cpu": 1.0}) is True and "t" in rec.trace.samples[0]
    monkeypatch.setattr(fr, "MAX_SAMPLES", 2)
    rec.add({"cpu": 2.0})
    assert rec.add({"cpu": 3.0}) is False and rec.full and len(rec.trace.samples) == 2


def test_the_real_sampler_produces_every_panel():
    sampler = fr.Sampler(top_process=lambda: ("x.exe", 12.0), clock=lambda: 4321.0)
    assert sampler.sample() is None                          # priming
    time.sleep(0.4)
    s = sampler.sample()
    assert s and all(k in s for k, _, _ in fr.PANELS) and s["top"] == "x.exe" and s["mhz"] == 4321.0
    assert 0 <= s["cpu"] <= 100 and s["disk"] >= 0 and s["net"] >= 0
