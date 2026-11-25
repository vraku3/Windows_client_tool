"""Tests for Diagnostic Bridge and DetailPanel action integration."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from core.diagnostic_bridge import match_suggested_fix
from core.types import LogEntry
from ui.detail_panel import DetailPanel


def test_match_suggested_fix_audio():
    fix = match_suggested_fix("AudioSrv", "Audio service stopped unexpectedly")
    assert fix is not None
    assert fix.title == "Restart Audio Engine"
    assert fix.target_module == "Quick Fix"


def test_match_suggested_fix_spooler():
    fix = match_suggested_fix("PrintService", "Spooler job failed on queue")
    assert fix is not None
    assert fix.title == "Clear Print Queue"
    assert fix.target_module == "Quick Fix"


def test_match_suggested_fix_gpu_crash():
    fix = match_suggested_fix("Display", "Display driver nvlddmkm stopped responding and has successfully recovered")
    assert fix is not None
    assert fix.title == "Open Driver Manager"
    assert fix.target_module == "Driver Manager"


def test_match_suggested_fix_unknown():
    assert match_suggested_fix("RandomSource", "Everything is fine") is None


def test_detail_panel_shows_action_button(qapp):
    panel = DetailPanel()
    entry = LogEntry(
        timestamp=datetime.now(),
        source="AudioEndpointBuilder",
        level="Error",
        message="Endpoint builder device timeout",
    )
    panel.show_entry(entry)
    assert not panel._action_btn.isHidden()
    assert "Restart Audio Engine" in panel._action_btn.text()
    assert panel._action_target == "Quick Fix"

    # Non-matching entry hides button
    clean_entry = LogEntry(
        timestamp=datetime.now(),
        source="MyCustomApp",
        level="Info",
        message="Regular event",
    )
    panel.show_entry(clean_entry)
    assert panel._action_btn.isHidden()
    assert panel._action_target == ""
