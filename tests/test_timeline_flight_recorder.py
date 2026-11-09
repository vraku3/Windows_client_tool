"""Tests for Flight Recorder timeline aggregator and TimelineModule."""
import tempfile
from datetime import datetime
from pathlib import Path

from core.flight_recorder import (
    _entry_from_internal_item,
    read_flight_recorder_entries,
    timeline_summary_text,
)
from core.timeline import record
from core.types import LogEntry
from modules.timeline.timeline_module import TimelineModule, _detail_html


def test_entry_from_internal_item():
    item = {
        "ts": "2026-11-01T12:00:00",
        "source": "tweaks",
        "event": "apply_tweak",
        "summary": "Applied telemetry tweak",
        "detail": {"tweak_id": "telemetry_disable"},
        "severity": "warning",
    }
    entry = _entry_from_internal_item(item)
    assert entry.source == "WinClientTool (tweaks)"
    assert entry.level == "Warning"
    assert entry.message == "Applied telemetry tweak"
    assert entry.raw["internal"] is True
    assert entry.raw["detail"]["tweak_id"] == "telemetry_disable"


def test_read_flight_recorder_internal_timeline():
    with tempfile.TemporaryDirectory() as tmpdir:
        record(tmpdir, "updates", "wu_install", "Installed KB5044284", {"kb": "KB5044284"}, "info")
        record(tmpdir, "game_mode", "session_start", "Game Mode ON", {}, "info")

        entries, notes = read_flight_recorder_entries(app_data_dir=tmpdir, days=30)
        assert len(entries) >= 2
        messages = [e.message for e in entries]
        assert "Game Mode ON" in messages
        assert "Installed KB5044284" in messages


def test_timeline_summary_text():
    entries = [
        LogEntry(timestamp=datetime.now(), source="Windows Update", level="Info", message="Update installed", raw={}),
        LogEntry(timestamp=datetime.now(), source="Driver Manager (PnP)", level="Info", message="Driver updated", raw={}),
    ]
    summary = timeline_summary_text(entries, [])
    assert "2 total change event(s) recorded" in summary
    assert "Windows Update" in summary


def test_timeline_detail_html():
    entry = LogEntry(
        timestamp=datetime(2026, 11, 1, 14, 0, 0),
        source="WinClientTool (debloat)",
        level="Info",
        message="Removed Xbox app",
        raw={"category": "tool_action", "detail": {"package": "Microsoft.XboxApp"}},
    )
    html = _detail_html(entry)
    assert "WinClientTool (debloat)" in html
    assert "Microsoft.XboxApp" in html


def test_timeline_module_creation():
    mod = TimelineModule()
    assert mod.name == "Timeline"
    assert mod.icon == "⏱️"
    pane_opts = mod.pane_options()
    assert "detail_enricher" in pane_opts
    assert "summarizer" in pane_opts

