"""Tests for event storm detection and duplicate event collapse."""
from datetime import datetime, timedelta
import pytest

from core.event_storm import detect_event_storms, collapse_repetitive_events
from core.types import LogEntry


def _entry(timestamp: datetime, source: str, level: str, message: str, **raw) -> LogEntry:
    return LogEntry(timestamp=timestamp, source=source, level=level, message=message, raw=raw)


def test_detect_event_storms_flags_high_frequency_bursts():
    t0 = datetime(2026, 11, 19, 10, 0, 0)
    # 12 events within 30 seconds
    entries = [
        _entry(t0 + timedelta(seconds=i * 2), "Service Control Manager", "Error", "The Spooler service terminated unexpectedly.", event_id=7031)
        for i in range(12)
    ]

    storms = detect_event_storms(entries, threshold_count=8, window_seconds=60)
    assert len(storms) == 1
    assert storms[0].source == "Service Control Manager"
    assert storms[0].event_id == 7031
    assert storms[0].total_count == 12
    assert storms[0].events_per_second > 0


def test_collapse_repetitive_events_suppresses_noise():
    t0 = datetime(2026, 11, 19, 11, 0, 0)
    # 10 identical events
    entries = [
        _entry(t0 + timedelta(seconds=i * 3), "Microsoft-Windows-DistributedCOM", "Warning", "The application-specific permission settings do not grant Local Activation", event_id=10016)
        for i in range(10)
    ]

    stream = collapse_repetitive_events(entries, max_consecutive=2)
    # Should keep first 2 + 1 summary entry = 3 total entries
    assert len(stream.entries) == 3
    assert stream.collapsed_count == 8
    assert "STORM COLLAPSED: 8 duplicate event(s) suppressed" in stream.entries[2].message
    assert stream.entries[2].raw.get("is_storm_summary") is True


def test_no_storm_on_normal_traffic():
    t0 = datetime(2026, 11, 19, 12, 0, 0)
    # Distinct events across time
    entries = [
        _entry(t0 + timedelta(minutes=i), f"Source{i}", "Info", f"Normal message {i}", event_id=100 + i)
        for i in range(5)
    ]

    storms = detect_event_storms(entries, threshold_count=8, window_seconds=60)
    assert len(storms) == 0

    stream = collapse_repetitive_events(entries)
    assert len(stream.entries) == 5
    assert stream.collapsed_count == 0
