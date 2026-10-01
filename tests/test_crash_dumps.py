import os
import tempfile
from datetime import datetime, timedelta

from core.types import LogEntry
from modules.crash_dumps.crash_dump_reader import (
    crash_frequency_trend,
    read_crash_dumps,
    summary_text,
)


def test_read_crash_dumps_empty_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        entries = read_crash_dumps(dump_dir=tmpdir)
        assert entries == []


def test_read_crash_dumps_with_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake .dmp file
        dmp_path = os.path.join(tmpdir, "test.dmp")
        with open(dmp_path, "wb") as f:
            f.write(b"MDMP" + b"\x00" * 28)  # Fake minidump header
        entries = read_crash_dumps(dump_dir=tmpdir)
        assert len(entries) == 1
        assert "test.dmp" in entries[0].message


def test_crash_dump_module_creates_widget():
    from modules.crash_dumps.crash_dump_module import CrashDumpModule
    mod = CrashDumpModule()
    widget = mod.create_widget()
    assert widget is not None


def test_read_nonexistent_dir():
    entries = read_crash_dumps(dump_dir="/nonexistent/path")
    assert entries == []


def _crash(days_ago: float, now: datetime, source: str = "Minidump") -> LogEntry:
    return LogEntry(timestamp=now - timedelta(days=days_ago), source=source,
                     level="Error", message="synthetic crash", raw={})


def _live(days_ago: float, now: datetime) -> LogEntry:
    return LogEntry(timestamp=now - timedelta(days=days_ago), source="LiveKernelReports\\gpu",
                     level="Warning", message="synthetic live dump", raw={})


def test_crash_frequency_trend_no_entries_is_none():
    assert crash_frequency_trend([]) is None


def test_crash_frequency_trend_ignores_live_kernel_reports():
    # Three live reports and nothing else: these are watchdog recoveries, not blue
    # screens, and must never feed the crash-frequency count.
    now = datetime(2026, 1, 1)
    entries = [_live(1, now), _live(10, now), _live(40, now)]
    assert crash_frequency_trend(entries, now=now) is None


def test_crash_frequency_trend_too_few_records_is_none():
    # Two total crashes in the app's whole lifetime is an incident, not a trend --
    # below _TREND_MIN_RECORDS (3) the function says nothing rather than alarm.
    now = datetime(2026, 1, 1)
    entries = [_crash(1, now), _crash(50, now)]
    assert crash_frequency_trend(entries, now=now) is None


def test_crash_frequency_trend_all_older_than_both_windows_is_none():
    now = datetime(2026, 1, 1)
    entries = [_crash(400, now), _crash(500, now), _crash(600, now)]
    assert crash_frequency_trend(entries, now=now) is None


def test_crash_frequency_trend_getting_more_frequent():
    now = datetime(2026, 1, 1)
    # 1 crash 45 days ago (prior window), 3 crashes in the last 10 days (recent window).
    entries = [_crash(45, now), _crash(1, now), _crash(5, now), _crash(9, now)]
    trend = crash_frequency_trend(entries, now=now)
    assert trend is not None
    assert "3 in the last 30 days" in trend
    assert "1 in the 30 days before" in trend
    assert "MORE frequent" in trend


def test_crash_frequency_trend_getting_less_frequent():
    now = datetime(2026, 1, 1)
    entries = [_crash(35, now), _crash(40, now), _crash(55, now), _crash(1, now)]
    trend = crash_frequency_trend(entries, now=now)
    assert trend is not None
    assert "1 in the last 30 days" in trend
    assert "3 in the 30 days before" in trend
    assert "LESS frequent" in trend


def test_crash_frequency_trend_steady():
    now = datetime(2026, 1, 1)
    entries = [_crash(1, now), _crash(5, now), _crash(35, now), _crash(40, now)]
    trend = crash_frequency_trend(entries, now=now)
    assert trend is not None
    assert "holding steady" in trend


def test_crash_frequency_trend_bugcheck_events_count_too():
    now = datetime(2026, 1, 1)
    entries = [
        _crash(1, now, source="BugCheck event"),
        _crash(2, now, source="MEMORY.DMP"),
        _crash(40, now, source="Minidump"),
    ]
    trend = crash_frequency_trend(entries, now=now)
    assert trend is not None
    assert "2 in the last 30 days" in trend


def test_summary_text_includes_trend_when_present():
    now = datetime.now()
    entries = [_crash(1, now), _crash(5, now), _crash(45, now)]
    text = summary_text(entries, [])
    assert "Crash frequency:" in text


def test_summary_text_omits_trend_when_not_enough_data():
    now = datetime.now()
    entries = [_crash(1, now)]
    text = summary_text(entries, [])
    assert "Crash frequency:" not in text
