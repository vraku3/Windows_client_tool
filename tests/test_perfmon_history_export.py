"""A real gap found reading src/modules/perfmon/ in full after the History
tab and threshold-line work landed: `PerfMonStore` has been collecting real
data for a while now, but the History tab's charts were the only way to see
it -- no sysadmin-standard way to hand that data to another engineer, open
it in Excel, or get a quick "how bad did it get" number without eyeballing a
300-point downsampled line. This adds `compute_summary` (min/avg/max/count)
and `write_history_csv` (a wide-format CSV merge keyed on the store's own
per-tick timestamp), wired into the History tab as a summary label and an
"Export CSV..." button.
"""
import csv
import os
import sqlite3
import tempfile

from modules.perfmon.perfmon_collector import (
    REPLAYABLE_COUNTERS, PerfMonStore, compute_summary, write_history_csv,
)


def test_compute_summary_empty_is_none_not_zeroes():
    assert compute_summary([]) is None


def test_compute_summary_matches_hand_computed_values():
    summary = compute_summary([10.0, 20.0, 30.0])
    assert summary["min"] == 10.0
    assert summary["max"] == 30.0
    assert summary["avg"] == 20.0
    assert summary["count"] == 3


def test_write_history_csv_merges_by_exact_timestamp():
    rows_by_counter = {
        "cpu_total": [("2026-01-01T00:00:00", 10.0), ("2026-01-01T00:01:00", 20.0)],
        "memory_percent": [("2026-01-01T00:00:00", 40.0), ("2026-01-01T00:01:00", 45.0)],
        "disk_percent": [("2026-01-01T00:00:00", 5.0), ("2026-01-01T00:01:00", 5.5)],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "out.csv")
        count = write_history_csv(rows_by_counter, path)
        assert count == 2
        with open(path, newline="", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        assert reader[0] == ["timestamp", "CPU %", "Memory %", "Disk %"]
        assert reader[1] == ["2026-01-01T00:00:00", "10.00", "40.00", "5.00"]
        assert reader[2] == ["2026-01-01T00:01:00", "20.00", "45.00", "5.50"]


def test_write_history_csv_blanks_a_missing_counter_for_a_timestamp():
    """A counter that doesn't have a value at a given timestamp (e.g. one
    added to REPLAYABLE_COUNTERS after the others were already collecting)
    must get an empty cell, never a fabricated 0 that would misreport an
    idle reading that was never actually taken."""
    rows_by_counter = {
        "cpu_total": [("t1", 10.0)],
        "memory_percent": [],
        "disk_percent": [],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "out.csv")
        count = write_history_csv(rows_by_counter, path)
        assert count == 1
        with open(path, newline="", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        assert reader[1] == ["t1", "10.00", "", ""]


def test_write_history_csv_empty_range_writes_header_only():
    rows_by_counter = {c: [] for c in REPLAYABLE_COUNTERS}
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "out.csv")
        count = write_history_csv(rows_by_counter, path)
        assert count == 0
        with open(path, newline="", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        assert len(reader) == 1  # header only
        assert reader[0][0] == "timestamp"


def test_write_history_csv_round_trips_a_real_store_query():
    """End-to-end against the real `PerfMonStore` shape: store a few real
    snapshots (all counters sharing one timestamp per tick, exactly as
    `store_snapshot` does it), query them back, and export -- the same path
    the History tab's Export button drives."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_perfmon.db")
        store = PerfMonStore(db_path)
        for i in range(3):
            store.store_snapshot({
                "cpu_total": 10.0 + i,
                "memory_percent": 40.0 + i,
                "disk_percent": 5.0,
            })
        rows_by_counter = {c: store.query(c, hours_back=1) for c in REPLAYABLE_COUNTERS}
        store.close()

        csv_path = os.path.join(tmpdir, "export.csv")
        count = write_history_csv(rows_by_counter, csv_path)
        assert count == 3
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        assert len(reader) == 4  # header + 3 rows
        assert reader[1][1] == "10.00"
        assert reader[3][1] == "12.00"


def test_real_perfmon_db_on_this_machine_exports_cleanly():
    """Real-machine check against this app's own
    %APPDATA%/WindowsTweaker/perfmon.db: whatever `query()` returns for each
    replayable counter -- rows or none -- must export through
    `write_history_csv` with no exception. Read-only: a test must never
    mutate the real user's own data file."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        import pytest
        pytest.skip("no APPDATA on this machine")
    db_path = os.path.join(appdata, "WindowsTweaker", "perfmon.db")
    if not os.path.exists(db_path):
        import pytest
        pytest.skip("app has never run on this machine -- no perfmon.db yet")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        store = PerfMonStore.__new__(PerfMonStore)
        store._conn = conn
        rows_by_counter = {c: store.query(c, hours_back=168) for c in REPLAYABLE_COUNTERS}
    finally:
        conn.close()

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "real_export.csv")
        count = write_history_csv(rows_by_counter, csv_path)
        assert count >= 0  # must not raise; 0 is a valid, honest answer
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        assert reader[0] == ["timestamp", "CPU %", "Memory %", "Disk %"]
        assert len(reader) == count + 1


def test_history_summary_label_reports_no_data_before_any_rows():
    """Build the real module widget (as the UI does), feed the summary
    updater empty rows for every counter, and confirm it says "no data" for
    each rather than leaving the label blank or claiming 0.0 for all three
    -- a module that never opened the PerfMon tab has an empty perfmon.db,
    and the label must say so honestly."""
    from modules.perfmon.perfmon_module import PerfMonModule

    mod = PerfMonModule()
    mod.create_widget()
    mod._update_history_summary({c: [] for c in REPLAYABLE_COUNTERS})
    text = mod._history_summary_label.text()
    assert text.count("no data") == 3


def test_history_summary_label_shows_real_min_avg_max():
    from modules.perfmon.perfmon_module import PerfMonModule

    mod = PerfMonModule()
    mod.create_widget()
    mod._update_history_summary({
        "cpu_total": [("t1", 10.0), ("t2", 90.0)],
        "memory_percent": [],
        "disk_percent": [],
    })
    text = mod._history_summary_label.text()
    assert "min 10.0" in text
    assert "max 90.0" in text
    assert "avg 50.0" in text


def test_export_with_nothing_loaded_does_not_raise(monkeypatch):
    """Clicking Export before the History tab has ever loaded (or on an
    empty range) must show a message, not crash on an unset attribute or a
    QFileDialog call with no data behind it."""
    from modules.perfmon.perfmon_module import PerfMonModule
    from PyQt6.QtWidgets import QMessageBox

    mod = PerfMonModule()
    mod.create_widget()
    assert mod._history_raw_rows == {}

    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda *a, **k: shown.append(a) or QMessageBox.StandardButton.Ok
    ))
    mod._export_history_csv()
    assert len(shown) == 1


def test_export_writes_a_real_file_when_data_is_loaded(monkeypatch):
    """With rows loaded (as `_refresh_history` sets them), Export must
    actually write the file the save dialog names, with no dialog shown for
    the user to interact with in this headless test."""
    from modules.perfmon.perfmon_module import PerfMonModule
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    mod = PerfMonModule()
    mod.create_widget()
    mod._history_raw_rows = {
        "cpu_total": [("t1", 10.0)],
        "memory_percent": [],
        "disk_percent": [],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "chosen.csv")
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (out_path, "CSV files (*.csv)")),
        )
        informed = []
        monkeypatch.setattr(QMessageBox, "information", staticmethod(
            lambda *a, **k: informed.append(a) or QMessageBox.StandardButton.Ok
        ))
        mod._export_history_csv()
        assert os.path.exists(out_path)
        with open(out_path, newline="", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        assert reader[1] == ["t1", "10.00", "", ""]
        assert len(informed) == 1
