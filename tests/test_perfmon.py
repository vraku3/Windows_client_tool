import os
import sqlite3
import tempfile
from datetime import datetime
from modules.perfmon.perfmon_collector import (
    REPLAYABLE_COUNTERS, PerfMonStore, collect_snapshot, downsample,
)
from modules.perfmon.perfmon_alerts import AlertRule
from modules.perfmon.perfmon_search_provider import PerfMonSearchProvider
from core.search_provider import SearchQuery
from core.types import LogEntry


def test_collect_snapshot():
    snapshot = collect_snapshot()
    assert "cpu_total" in snapshot
    assert "memory_percent" in snapshot
    assert "disk_percent" in snapshot
    assert isinstance(snapshot["cpu_total"], float)


def test_store_and_query():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_perfmon.db")
        store = PerfMonStore(db_path)
        store.store_snapshot({"cpu_total": 50.0, "memory_percent": 60.0})
        results = store.query("cpu_total", hours_back=1)
        assert len(results) == 1
        assert results[0][1] == 50.0
        store.close()


def test_store_cleanup():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_perfmon.db")
        store = PerfMonStore(db_path)
        store.store_snapshot({"cpu_total": 50.0})
        store.cleanup_old(days=0)  # Delete everything
        results = store.query("cpu_total", hours_back=24)
        assert len(results) == 0
        store.close()


def test_alert_rule_no_trigger():
    rule = AlertRule(counter="cpu_total", operator=">", threshold=90, duration_sec=5)
    result = rule.check(50.0)  # Below threshold
    assert result is None


def test_alert_rule_immediate_no_fire():
    rule = AlertRule(counter="cpu_total", operator=">", threshold=90, duration_sec=300)
    result = rule.check(95.0)  # Above threshold but not long enough
    assert result is None  # Duration not met yet


def test_alert_rule_disabled():
    rule = AlertRule(counter="cpu_total", operator=">", threshold=90, duration_sec=0, enabled=False)
    result = rule.check(95.0)
    assert result is None


def test_alert_rule_fires_after_duration():
    rule = AlertRule(counter="cpu_total", operator=">", threshold=90, duration_sec=0)
    # duration_sec=0 means fire immediately when threshold crossed
    result = rule.check(95.0)
    assert result is not None
    assert "cpu_total" in result


def test_search_provider():
    sp = PerfMonSearchProvider()
    sp.add_alert(LogEntry(
        timestamp=datetime(2026, 3, 25, 12, 0),
        source="PerfMon",
        level="Warning",
        message="cpu_total > 90 for 300s",
    ))
    results = sp.search(SearchQuery(text="cpu"))
    assert len(results) == 1


def test_perfmon_module_creates_widget():
    from modules.perfmon.perfmon_module import PerfMonModule
    mod = PerfMonModule()
    widget = mod.create_widget()
    assert widget is not None
    assert mod._dashboard is not None


def test_downsample_leaves_short_series_untouched():
    rows = [("t%d" % i, float(i)) for i in range(50)]
    assert downsample(rows, max_points=300) == rows


def test_downsample_thins_a_long_series_to_the_cap_and_keeps_both_ends():
    rows = [("t%d" % i, float(i)) for i in range(10_080)]  # a real 7-day query
    thinned = downsample(rows, max_points=300)
    assert len(thinned) <= 300
    assert thinned[0] == rows[0]
    assert thinned[-1] == rows[-1]
    # Order is preserved -- a chart drawing this out of order would zig-zag.
    values = [v for _ts, v in thinned]
    assert values == sorted(values)


def test_downsample_empty_series():
    assert downsample([], max_points=300) == []


def test_store_query_over_a_real_multi_day_range_matches_what_was_stored():
    """The History tab's real query shape: several minutes' worth of samples
    across the three replayable counters, queried back over a 7-day window."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_perfmon.db")
        store = PerfMonStore(db_path)
        for i in range(5):
            store.store_snapshot({
                "cpu_total": 10.0 + i,
                "memory_percent": 40.0 + i,
                "disk_percent": 5.0,
            })
        for counter in REPLAYABLE_COUNTERS:
            rows = store.query(counter, hours_back=168)
            assert len(rows) == 5
            thinned = downsample(rows)
            assert thinned == rows  # well under the 300-point cap
        store.close()


def test_real_perfmon_db_on_this_machine_opens_and_queries_cleanly():
    """Real-machine check: this app's own %APPDATA%/WindowsTweaker/perfmon.db
    (created by every PerfMonModule.on_start) must open under the exact
    schema `PerfMonStore` expects and answer `query()` for every counter the
    History tab offers, with no exception -- whether or not it currently
    holds any rows (it only ever gets samples while the PerfMon tab was open,
    so an empty result here is a fact about usage, not a bug)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        import pytest
        pytest.skip("no APPDATA on this machine")
    db_path = os.path.join(appdata, "WindowsTweaker", "perfmon.db")
    if not os.path.exists(db_path):
        import pytest
        pytest.skip("app has never run on this machine -- no perfmon.db yet")

    # Read-only: a test must never mutate the real user's own data file.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        store = PerfMonStore.__new__(PerfMonStore)
        store._conn = conn
        for counter in REPLAYABLE_COUNTERS:
            rows = store.query(counter, hours_back=168)
            assert isinstance(rows, list)
            downsample(rows)  # must not raise regardless of row count
    finally:
        conn.close()
