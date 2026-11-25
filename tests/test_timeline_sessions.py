"""Unit tests for Reboot Session Correlation & Flight Recorder in TimelineModule."""
from datetime import datetime, timedelta
from typing import List

import pytest
from PyQt6.QtWidgets import QApplication

from core.flight_recorder import (
    SessionBlock,
    correlate_sessions,
    filter_entries_by_session,
    format_uptime,
    timeline_summary_text,
)
from core.types import LogEntry
from modules.timeline.timeline_module import TimelineModule, _detail_html


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_format_uptime():
    assert format_uptime(None) == "Unknown"
    assert format_uptime(timedelta(seconds=-10)) == "Unknown"
    assert format_uptime(timedelta(seconds=45)) == "45s"
    assert format_uptime(timedelta(minutes=30, seconds=15)) == "30m"
    assert format_uptime(timedelta(hours=4, minutes=15)) == "4h 15m"
    assert format_uptime(timedelta(days=2, hours=3, minutes=45)) == "2d 3h 45m"


def test_correlate_single_active_session():
    t0 = datetime(2026, 11, 18, 10, 0, 0)
    entries = [
        # Clustered boot events (KG 12 and EL 6005)
        LogEntry(
            timestamp=t0,
            source="Windows System",
            level="Info",
            message="System Boot: Operating system started (Kernel-General 12)",
            raw={"category": "system_state", "event_id": 12, "event_type": "boot"},
        ),
        LogEntry(
            timestamp=t0 + timedelta(seconds=15),
            source="Windows System",
            level="Info",
            message="System Boot: Windows Event Log service started (6005)",
            raw={"category": "system_state", "event_id": 6005, "event_type": "boot"},
        ),
        # Machine changes during active session
        LogEntry(
            timestamp=t0 + timedelta(minutes=30),
            source="WinClientTool (tweaks)",
            level="Info",
            message="Applied telemetry disable tweak",
            raw={"category": "tool_action", "event": "apply_tweak", "detail": {"tweak_id": "telemetry"}},
        ),
        LogEntry(
            timestamp=t0 + timedelta(hours=1),
            source="Windows Update",
            level="Info",
            message="Windows Update Installed: KB5044284",
            raw={"category": "update", "event_id": 19},
        ),
        LogEntry(
            timestamp=t0 + timedelta(hours=2),
            source="Driver Manager (PnP)",
            level="Info",
            message="Driver Installed/Updated: Realtek Audio",
            raw={"category": "driver", "event_id": 20001},
        ),
        LogEntry(
            timestamp=t0 + timedelta(hours=3),
            source="Software Installer (MSI)",
            level="Info",
            message="Application Installed: Notepad++",
            raw={"category": "software", "event_id": 1033},
        ),
        LogEntry(
            timestamp=t0 + timedelta(hours=4),
            source="System Restore",
            level="Info",
            message="Restore Point Created: Daily Checkpoint",
            raw={"category": "restore_point", "event_id": 8193},
        ),
    ]

    sessions = correlate_sessions(entries)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.session_id == 0
    assert s.clean_shutdown is None
    assert s.is_crash is False
    assert s.shutdown_type == "Active"
    assert s.start_time == t0
    assert s.end_time is None
    assert s.uptime is not None

    # Verify correlated change categories
    assert len(s.tweaks) == 1
    assert s.tweaks[0].message == "Applied telemetry disable tweak"
    assert len(s.windows_updates) == 1
    assert len(s.driver_updates) == 1
    assert len(s.software_installs) == 1
    assert len(s.restore_points) == 1
    assert s.change_summary["total_changes"] == 5
    assert len(s.all_changes) == 5

    # Verify annotation on entry.raw
    assert entries[2].raw["session_id"] == 0
    assert entries[2].raw["session_is_crash"] is False
    assert entries[2].raw["session_shutdown_type"] == "Active"

    # Verify to_dict serialization
    d = s.to_dict()
    assert d["session_id"] == 0
    assert d["is_crash"] is False
    assert d["change_summary"]["total_changes"] == 5


def test_correlate_clean_reboot_session():
    t_prev = datetime(2026, 11, 16, 9, 0, 0)
    t_curr = datetime(2026, 11, 17, 8, 30, 0)

    entries = [
        # Session 1: Previous session
        LogEntry(
            timestamp=t_prev,
            source="Windows System",
            level="Info",
            message="System Boot: Operating system started (Kernel-General 12)",
            raw={"category": "system_state", "event_id": 12, "event_type": "boot"},
        ),
        LogEntry(
            timestamp=t_prev + timedelta(hours=2),
            source="WinClientTool (tweaks)",
            level="Info",
            message="Applied gaming optimization tweak",
            raw={"category": "tool_action", "event": "apply_tweak"},
        ),
        LogEntry(
            timestamp=t_prev + timedelta(hours=8),
            source="Windows System",
            level="Info",
            message="Clean Shutdown: Windows Event Log service stopped (6006)",
            raw={"category": "system_state", "event_id": 6006, "event_type": "shutdown_clean"},
        ),
        LogEntry(
            timestamp=t_prev + timedelta(hours=8, seconds=10),
            source="Windows System",
            level="Info",
            message="Clean Shutdown: Operating system shutting down (Kernel-General 13)",
            raw={"category": "system_state", "event_id": 13, "event_type": "shutdown_clean"},
        ),

        # Session 0: Current session
        LogEntry(
            timestamp=t_curr,
            source="Windows System",
            level="Info",
            message="System Boot: Operating system started (Kernel-General 12)",
            raw={"category": "system_state", "event_id": 12, "event_type": "boot"},
        ),
        LogEntry(
            timestamp=t_curr + timedelta(minutes=15),
            source="Driver Manager (PnP)",
            level="Info",
            message="Driver Installed: NVIDIA Graphics Driver",
            raw={"category": "driver", "event_id": 20001},
        ),
    ]

    sessions = correlate_sessions(entries)
    assert len(sessions) == 2

    # Session 0 is Current
    s0 = sessions[0]
    assert s0.session_id == 0
    assert s0.clean_shutdown is None
    assert s0.shutdown_type == "Active"
    assert len(s0.driver_updates) == 1
    assert len(s0.tweaks) == 0

    # Session 1 is Previous
    s1 = sessions[1]
    assert s1.session_id == 1
    assert s1.clean_shutdown is True
    assert s1.is_crash is False
    assert s1.shutdown_type == "Clean Shutdown"
    assert s1.uptime == timedelta(hours=8, seconds=10)
    assert len(s1.tweaks) == 1
    assert len(s1.driver_updates) == 0


def test_correlate_dirty_shutdown_crash_session_kp41():
    t_prev = datetime(2026, 11, 15, 12, 0, 0)
    t_curr = datetime(2026, 11, 16, 9, 0, 0)

    entries = [
        # Session 1: Started at t_prev
        LogEntry(
            timestamp=t_prev,
            source="Windows System",
            level="Info",
            message="System Boot: Operating system started (Kernel-General 12)",
            raw={"category": "system_state", "event_id": 12, "event_type": "boot"},
        ),
        LogEntry(
            timestamp=t_prev + timedelta(hours=1),
            source="WinClientTool (tweaks)",
            level="Info",
            message="Applied network throttling tweak",
            raw={"category": "tool_action", "event": "apply_tweak"},
        ),
        LogEntry(
            timestamp=t_prev + timedelta(hours=2),
            source="Driver Manager (PnP)",
            level="Info",
            message="Driver Installed: Test Driver",
            raw={"category": "driver", "event_id": 20001},
        ),
        # System abruptly lost power at ~t_prev + 3h (NO clean shutdown 13/6006 logged)

        # Session 0: Reboot at t_curr with Kernel-Power 41 and Event 6008
        LogEntry(
            timestamp=t_curr,
            source="Windows System",
            level="Info",
            message="System Boot: Operating system started (Kernel-General 12)",
            raw={"category": "system_state", "event_id": 12, "event_type": "boot"},
        ),
        LogEntry(
            timestamp=t_curr + timedelta(seconds=2),
            source="Windows System",
            level="Critical",
            message="Dirty Shutdown / Power Loss: System rebooted without cleanly shutting down (Kernel-Power 41)",
            raw={"category": "system_state", "event_id": 41, "event_type": "dirty_shutdown", "data": {"BugcheckCode": "0"}},
        ),
        LogEntry(
            timestamp=t_curr + timedelta(seconds=5),
            source="Windows System",
            level="Error",
            message="Unexpected Shutdown: Previous shutdown was not clean (Event 6008)",
            raw={"category": "system_state", "event_id": 6008, "event_type": "unexpected_shutdown"},
        ),
        LogEntry(
            timestamp=t_curr + timedelta(minutes=10),
            source="Windows Update",
            level="Info",
            message="Windows Update Installed: KB5044284",
            raw={"category": "update", "event_id": 19},
        ),
    ]

    sessions = correlate_sessions(entries)
    assert len(sessions) == 2

    s_crash = sessions[1]
    assert s_crash.session_id == 1
    assert s_crash.clean_shutdown is False
    assert s_crash.is_crash is True
    assert "Kernel-Power 41" in s_crash.shutdown_type or "Dirty Shutdown" in s_crash.shutdown_type

    # The changes made before the crash belong to s_crash
    assert len(s_crash.tweaks) == 1
    assert len(s_crash.driver_updates) == 1

    # Filter by "Session before Crash" returns s_crash entries
    crashed_entries = filter_entries_by_session(entries, "Session before Crash", sessions)
    assert len(crashed_entries) > 0
    crashed_messages = [e.message for e in crashed_entries]
    assert "Applied network throttling tweak" in crashed_messages
    assert "Driver Installed: Test Driver" in crashed_messages
    assert "Windows Update Installed: KB5044284" not in crashed_messages


def test_correlate_bsod_session_with_bugcheck():
    t_prev = datetime(2026, 11, 14, 8, 0, 0)
    t_curr = datetime(2026, 11, 14, 11, 0, 0)

    entries = [
        LogEntry(
            timestamp=t_prev,
            source="Windows System",
            level="Info",
            message="System Boot: Operating system started (Kernel-General 12)",
            raw={"category": "system_state", "event_id": 12, "event_type": "boot"},
        ),
        LogEntry(
            timestamp=t_prev + timedelta(minutes=30),
            source="WinClientTool (tweaks)",
            level="Info",
            message="Modified registry key",
            raw={"category": "tool_action", "event": "apply_tweak"},
        ),
        LogEntry(
            timestamp=t_curr,
            source="Windows System",
            level="Info",
            message="System Boot: Operating system started (Kernel-General 12)",
            raw={"category": "system_state", "event_id": 12, "event_type": "boot"},
        ),
        LogEntry(
            timestamp=t_curr + timedelta(seconds=1),
            source="Windows System",
            level="Critical",
            message="System Crash (BSOD): Kernel-Power 41 (Bugcheck 0x0000001E)",
            raw={"category": "system_state", "event_id": 41, "event_type": "bsod", "data": {"BugcheckCode": "0x0000001E"}},
        ),
    ]

    sessions = correlate_sessions(entries)
    assert len(sessions) == 2
    s1 = sessions[1]
    assert s1.is_crash is True
    assert s1.clean_shutdown is False
    assert "BSOD" in s1.shutdown_type or "0x0000001E" in s1.shutdown_type


def test_filter_entries_by_session_all_modes():
    t2 = datetime(2026, 11, 10, 8, 0, 0)
    t1 = datetime(2026, 11, 11, 8, 0, 0)
    t0 = datetime(2026, 11, 12, 8, 0, 0)

    entries = [
        # Session 2 (oldest): Clean shutdown
        LogEntry(timestamp=t2, source="Windows System", level="Info", message="Boot 2", raw={"event_id": 12, "category": "system_state"}),
        LogEntry(timestamp=t2 + timedelta(hours=1), source="WinClientTool", level="Info", message="Tweak in Session 2", raw={"category": "tool_action", "event": "tweak"}),
        LogEntry(timestamp=t2 + timedelta(hours=4), source="Windows System", level="Info", message="Clean Shutdown 2", raw={"event_id": 13, "category": "system_state"}),

        # Session 1 (middle): Crashed
        LogEntry(timestamp=t1, source="Windows System", level="Info", message="Boot 1", raw={"event_id": 12, "category": "system_state"}),
        LogEntry(timestamp=t1 + timedelta(hours=1), source="WinClientTool", level="Info", message="Tweak in Session 1", raw={"category": "tool_action", "event": "tweak"}),
        # crashed before t0

        # Session 0 (newest): Current Active with KP41 from session 1
        LogEntry(timestamp=t0, source="Windows System", level="Info", message="Boot 0", raw={"event_id": 12, "category": "system_state"}),
        LogEntry(timestamp=t0 + timedelta(seconds=2), source="Windows System", level="Critical", message="KP41", raw={"event_id": 41, "category": "system_state", "event_type": "dirty_shutdown"}),
        LogEntry(timestamp=t0 + timedelta(hours=1), source="WinClientTool", level="Info", message="Tweak in Session 0", raw={"category": "tool_action", "event": "tweak"}),
    ]

    sessions = correlate_sessions(entries)
    assert len(sessions) == 3

    # All Sessions
    all_res = filter_entries_by_session(entries, "All Sessions", sessions)
    assert len(all_res) == len(entries)

    # Current Session (Session 0)
    curr_res = filter_entries_by_session(entries, "Current Session", sessions)
    curr_msgs = [e.message for e in curr_res]
    assert "Tweak in Session 0" in curr_msgs
    assert "Tweak in Session 1" not in curr_msgs
    assert "Tweak in Session 2" not in curr_msgs

    # Previous Session (Session 1)
    prev_res = filter_entries_by_session(entries, "Previous Session", sessions)
    prev_msgs = [e.message for e in prev_res]
    assert "Tweak in Session 1" in prev_msgs
    assert "Tweak in Session 0" not in prev_msgs

    # Session before Crash (Session 1 crashed)
    crash_res = filter_entries_by_session(entries, "Session before Crash", sessions)
    crash_msgs = [e.message for e in crash_res]
    assert "Tweak in Session 1" in crash_msgs
    assert "Tweak in Session 0" not in crash_msgs

    # By numeric Session ID
    s2_res = filter_entries_by_session(entries, "Session 2", sessions)
    s2_msgs = [e.message for e in s2_res]
    assert "Tweak in Session 2" in s2_msgs
    assert "Tweak in Session 1" not in s2_msgs


def test_no_crash_filter_returns_empty():
    t0 = datetime(2026, 11, 18, 10, 0, 0)
    entries = [
        LogEntry(timestamp=t0, source="Windows System", level="Info", message="Boot", raw={"event_id": 12, "category": "system_state"}),
        LogEntry(timestamp=t0 + timedelta(hours=1), source="WinClientTool", level="Info", message="Tweak", raw={"category": "tool_action"}),
    ]
    res = filter_entries_by_session(entries, "Session before Crash")
    assert res == []


def test_timeline_module_ui_and_dropdown(qapp):
    mod = TimelineModule()
    widget = mod.create_widget()
    assert widget is not None

    # Verify session combo was added to the toolbar
    combo = mod.session_combo
    assert combo is not None
    assert combo.count() == 4
    items = [combo.itemText(i) for i in range(combo.count())]
    assert items == ["All Sessions", "Current Session", "Previous Session", "Session before Crash"]

    # Verify properties
    assert isinstance(mod.sessions, list)
    assert mod.current_session is None

    # Simulate entries loading
    t_prev = datetime(2026, 11, 17, 8, 0, 0)
    t_curr = datetime(2026, 11, 18, 8, 0, 0)
    mock_entries = [
        LogEntry(timestamp=t_prev, source="Windows System", level="Info", message="Boot 1", raw={"event_id": 12, "category": "system_state"}),
        LogEntry(timestamp=t_prev + timedelta(hours=1), source="WinClientTool", level="Info", message="Tweak 1", raw={"category": "tool_action", "event": "tweak"}),
        LogEntry(timestamp=t_curr, source="Windows System", level="Info", message="Boot 0", raw={"event_id": 12, "category": "system_state"}),
        LogEntry(timestamp=t_curr + timedelta(seconds=1), source="Windows System", level="Critical", message="KP41", raw={"event_id": 41, "category": "system_state"}),
        LogEntry(timestamp=t_curr + timedelta(hours=1), source="WinClientTool", level="Info", message="Tweak 0", raw={"category": "tool_action", "event": "tweak"}),
    ]
    mod._sessions = correlate_sessions(mock_entries)
    assert len(mod.sessions) == 2
    assert mod.current_session.session_id == 0

    # Test programmatically setting session filter
    mod.set_session_filter("Previous Session")
    assert combo.currentText() == "Previous Session"
    filtered = mod._apply_session_filter(mock_entries)
    assert any(e.message == "Tweak 1" for e in filtered)
    assert not any(e.message == "Tweak 0" for e in filtered)

    # Test filter by "Session before Crash"
    mod.set_session_filter("Session before Crash")
    assert combo.currentText() == "Session before Crash"
    filtered_crash = mod._apply_session_filter(mock_entries)
    assert any(e.message == "Tweak 1" for e in filtered_crash)

    # Test reset to "All Sessions"
    mod.set_session_filter("All Sessions")
    assert len(mod._apply_session_filter(mock_entries)) == len(mock_entries)


def test_timeline_summary_and_detail_html():
    t_boot = datetime(2026, 11, 18, 9, 0, 0)
    entry = LogEntry(
        timestamp=t_boot + timedelta(hours=1),
        source="WinClientTool (tweaks)",
        level="Warning",
        message="Disabled diagnostic telemetry",
        raw={
            "category": "tool_action",
            "session_id": 1,
            "session_shutdown_type": "Dirty Shutdown / Power Loss (Kernel-Power 41)",
            "session_uptime_str": "3h 45m",
            "session_is_crash": True,
            "detail": {"tweak_id": "disable_telemetry"},
        },
    )

    html = _detail_html(entry)
    assert "Reboot Session:" in html
    assert "Session 1" in html
    assert "Kernel-Power 41" in html
    assert "3h 45m" in html
    assert "CRASH" in html

    summary = timeline_summary_text([entry], [])
    assert "session(s) tracked" in summary
    assert "Disabled diagnostic telemetry" in summary or "1 total change" in summary
