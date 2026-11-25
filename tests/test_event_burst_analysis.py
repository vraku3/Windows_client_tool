"""Unit tests for Event Viewer Burst Analysis & Noise Filtering Intelligence."""
from datetime import datetime, timedelta

import pytest

from core.types import LogEntry
from modules.event_viewer import event_analysis as ea
from modules.event_viewer.event_query import (
    BENIGN_NOISE_XPATH,
    build_noise_filter_xpath,
    build_query,
)

NOW = datetime(2026, 11, 18, 12, 0, 0)


def _ev(
    provider: str,
    eid: int,
    level: str = "Info",
    seconds_ago: float = 0,
    message: str = "Sample event message",
    source: str = None,
    data: dict = None,
) -> LogEntry:
    raw = {"event_id": eid, "provider": provider}
    if data:
        raw["data"] = data
    return LogEntry(
        timestamp=NOW - timedelta(seconds=seconds_ago),
        source=source or provider,
        level=level,
        message=message,
        raw=raw,
    )


# ============================================================================
# 1. Burst & Storm Detection Tests (Qt-free)
# ============================================================================

def test_detect_event_storms_flags_runaway_burst():
    """Provider generating >30 events in a 60-second window must be flagged as a storm."""
    entries = []
    # 40 events in 30 seconds for NoisyApp
    for i in range(40):
        entries.append(_ev("NoisyApp", 5001, level="Warning", seconds_ago=i * 0.7))
    # 5 events for CalmService
    for i in range(5):
        entries.append(_ev("CalmService", 7000, level="Info", seconds_ago=i * 10))

    report = ea.detect_event_storms(entries, burst_threshold=30)

    assert report.has_storm is True
    assert len(report.storms) == 1
    storm = report.storms[0]
    assert storm.provider == "NoisyApp"
    assert storm.peak_burst_count >= 40
    assert storm.rate_per_minute > 30.0
    assert storm.total_count == 40
    assert storm.event_id == 5001
    assert 5001 in storm.top_event_ids
    assert report.total_burst_events == 40


def test_detect_event_storms_ignores_spaced_out_events():
    """35 events spaced evenly over 2 hours do not exceed 30 events/minute."""
    entries = []
    # 35 events over 7200 seconds (~1 event every ~205s)
    for i in range(35):
        entries.append(_ev("SlowService", 100, level="Info", seconds_ago=i * 205))

    report = ea.detect_event_storms(entries, burst_threshold=30)

    assert report.has_storm is False
    assert len(report.storms) == 0
    assert report.total_burst_events == 0


def test_detect_event_storms_boundary_conditions():
    """Threshold checks: <=30 events in 60s is not a storm; 31 events is a storm."""
    # 30 events in 60s
    borderline = [_ev("BorderlineProv", 200, seconds_ago=i * 1.9) for i in range(30)]
    report = ea.detect_event_storms(borderline, burst_threshold=30)
    assert report.has_storm is False

    # 31 events in 60s
    burst = [_ev("BurstProv", 200, seconds_ago=i * 1.8) for i in range(31)]
    report_burst = ea.detect_event_storms(burst, burst_threshold=30)
    assert report_burst.has_storm is True
    assert len(report_burst.storms) == 1
    assert report_burst.storms[0].peak_burst_count >= 31


def test_detect_event_storms_empty_and_single():
    """Empty list or single entry handles gracefully without exceptions."""
    assert ea.detect_event_storms([]).has_storm is False
    assert len(ea.detect_event_storms([]).storms) == 0

    single = [_ev("SingleProv", 100, seconds_ago=5)]
    rep = ea.detect_event_storms(single)
    assert rep.has_storm is False
    assert len(rep.noisy_providers) == 1
    assert rep.noisy_providers[0] == ("SingleProv", 1)


def test_noisy_providers_and_event_ids_ranking():
    """Top noisy providers and (provider, event_id) combinations are ranked by count."""
    entries = []
    for _ in range(50):
        entries.append(_ev("Alpha", 101, seconds_ago=100))
    for _ in range(20):
        entries.append(_ev("Alpha", 102, seconds_ago=100))
    for _ in range(40):
        entries.append(_ev("Beta", 201, seconds_ago=100))
    for _ in range(10):
        entries.append(_ev("Gamma", 301, seconds_ago=100))

    report = ea.detect_event_storms(entries, burst_threshold=100)

    assert report.noisy_providers[0] == ("Alpha", 70)
    assert report.noisy_providers[1] == ("Beta", 40)
    assert report.noisy_providers[2] == ("Gamma", 10)

    assert report.noisy_event_ids[0] == ("Alpha", 101, 50)
    assert report.noisy_event_ids[1] == ("Beta", 201, 40)
    assert report.noisy_event_ids[2] == ("Alpha", 102, 20)
    assert report.noisy_event_ids[3] == ("Gamma", 301, 10)


# ============================================================================
# 2. Benign Background Noise Filtering Tests (Qt-free)
# ============================================================================

def test_is_benign_noise_dcom_10016():
    """DCOM 10016 is classic benign noise; other DCOM IDs are not."""
    dcom_noise = _ev("Microsoft-Windows-DistributedCOM", 10016, level="Warning", message="The application-specific permission settings...")
    assert ea.is_benign_noise(dcom_noise) is True

    dcom_short = _ev("DCOM", 10016, level="Error", message="Permission settings...")
    assert ea.is_benign_noise(dcom_short) is True

    dcom_real_error = _ev("Microsoft-Windows-DistributedCOM", 10005, level="Error", message="DCOM got error 10005")
    assert ea.is_benign_noise(dcom_real_error) is False

    other_10016 = _ev("CustomService", 10016, level="Warning")
    assert ea.is_benign_noise(other_10016) is False


def test_is_benign_noise_kerberos_4768():
    """Successful Kerberos 4768 TGT requests are benign noise; failures are not."""
    success_tgt = _ev(
        "Microsoft-Windows-Security-Auditing",
        4768,
        level="Info",
        data={"ResultCode": "0x0"},
    )
    assert ea.is_benign_noise(success_tgt) is True

    failed_tgt = _ev(
        "Microsoft-Windows-Security-Auditing",
        4768,
        level="Warning",
        data={"ResultCode": "0x18"},  # Pre-auth failed (bad password)
    )
    assert ea.is_benign_noise(failed_tgt) is False

    error_tgt = _ev(
        "Microsoft-Windows-Security-Auditing",
        4768,
        level="Error",
        data={"ResultCode": "0x0"},
    )
    assert ea.is_benign_noise(error_tgt) is False

    logon_fail_4625 = _ev("Microsoft-Windows-Security-Auditing", 4625, level="Warning")
    assert ea.is_benign_noise(logon_fail_4625) is False


def test_is_benign_noise_routine_time_sync():
    """Routine Time-Service sync events are benign noise; sync errors are not."""
    sync_35 = _ev("Microsoft-Windows-Time-Service", 35, level="Info", message="The time service is now synchronizing...")
    assert ea.is_benign_noise(sync_35) is True

    sync_37 = _ev("W32Time", 37, level="Info", message="The time provider NtpClient is receiving valid time data...")
    assert ea.is_benign_noise(sync_37) is True

    sync_158 = _ev("Time-Service", 158, level="Info")
    assert ea.is_benign_noise(sync_158) is True

    time_err_134 = _ev("Microsoft-Windows-Time-Service", 134, level="Warning", message="NTP peer unreachable")
    assert ea.is_benign_noise(time_err_134) is False


def test_is_benign_noise_routine_service_and_gp():
    """SCM 7036 Info and GP 1500 Info are benign noise; service crashes (7031) are not."""
    scm_running = _ev("Service Control Manager", 7036, level="Info", message="Service entered running state")
    assert ea.is_benign_noise(scm_running) is True

    scm_crash = _ev("Service Control Manager", 7031, level="Error", message="Service terminated unexpectedly")
    assert ea.is_benign_noise(scm_crash) is False

    gp_success = _ev("Microsoft-Windows-GroupPolicy", 1500, level="Info", message="Policy applied successfully")
    assert ea.is_benign_noise(gp_success) is True


def test_filter_noise_toggle():
    """filter_noise suppresses benign chatter only when hide_noise is True."""
    dcom = _ev("Microsoft-Windows-DistributedCOM", 10016, level="Warning")
    disk = _ev("disk", 153, level="Warning", message="IO retried")
    time_sync = _ev("Microsoft-Windows-Time-Service", 35, level="Info")
    app_crash = _ev("Application Error", 1000, level="Error", message="App crashed")

    all_entries = [dcom, disk, time_sync, app_crash]

    # Without filter -> all 4 kept
    assert len(ea.filter_noise(all_entries, hide_noise=False)) == 4

    # With filter -> only disk and app_crash kept
    filtered = ea.filter_noise(all_entries, hide_noise=True)
    assert len(filtered) == 2
    assert disk in filtered
    assert app_crash in filtered
    assert dcom not in filtered
    assert time_sync not in filtered


# ============================================================================
# 3. Provider Muting & Isolating Tests (Qt-free)
# ============================================================================

def test_filter_providers_muting():
    """Temporarily mute one or more providers from the list."""
    e1 = _ev("disk", 153, level="Warning")
    e2 = _ev("Microsoft-Windows-DistributedCOM", 10016, level="Warning")
    e3 = _ev("Service Control Manager", 7031, level="Error")
    entries = [e1, e2, e3]

    # Mute DCOM
    out = ea.filter_providers(entries, muted={"Microsoft-Windows-DistributedCOM"})
    assert len(out) == 2
    assert e2 not in out

    # Case-insensitive mute
    out_ci = ea.filter_providers(entries, muted={"microsoft-windows-distributedcom"})
    assert len(out_ci) == 2
    assert e2 not in out_ci

    # Mute multiple
    out_multi = ea.filter_providers(entries, muted={"disk", "Service Control Manager"})
    assert len(out_multi) == 1
    assert out_multi[0] == e2


def test_filter_providers_isolating():
    """Isolate a single provider so ONLY that provider is shown."""
    e1 = _ev("disk", 153, level="Warning")
    e2 = _ev("DCOM", 10016, level="Warning")
    e3 = _ev("Service Control Manager", 7031, level="Error")
    entries = [e1, e2, e3]

    # Isolate disk
    isolated = ea.filter_providers(entries, isolated="disk")
    assert len(isolated) == 1
    assert isolated[0] == e1

    # Isolate case-insensitive
    isolated_ci = ea.filter_providers(entries, isolated="SERVICE CONTROL MANAGER")
    assert len(isolated_ci) == 1
    assert isolated_ci[0] == e3

    # Isolate unknown
    assert len(ea.filter_providers(entries, isolated="NonExistent")) == 0


def test_filter_providers_no_filters():
    """No muted or isolated providers returns list unchanged."""
    entries = [_ev("disk", 153)]
    assert ea.filter_providers(entries) == entries


# ============================================================================
# 4. XPath Noise Query Tests (Qt-free)
# ============================================================================

def test_event_query_noise_xpath():
    """event_query provides valid XPath noise exclusion clauses."""
    assert "Microsoft-Windows-DistributedCOM" in BENIGN_NOISE_XPATH
    assert "10016" in BENIGN_NOISE_XPATH
    assert build_noise_filter_xpath() == BENIGN_NOISE_XPATH

    q_clean = build_query(hours_back=24, exclude_noise=False)
    assert "10016" not in q_clean

    q_noisy = build_query(hours_back=24, exclude_noise=True)
    assert "10016" in q_noisy
    assert "TimeCreated" in q_noisy


# ============================================================================
# 5. UI Integration Tests (PyQt6 with qapp fixture)
# ============================================================================

def test_event_viewer_module_has_storm_and_noise_controls(qapp):
    """EventViewerModule must expose storm detector tile, storm button, and noise filter."""
    from modules.event_viewer.event_viewer_module import EventViewerModule
    from ui.glance import GlanceTile

    mod = EventViewerModule()
    pane = mod.create_widget()

    # Storm Detector tile registered in pane options
    assert mod._storm_tile is not None
    assert isinstance(mod._storm_tile, GlanceTile)
    assert mod._storm_tile.caption.text() == "STORM DETECTOR"

    # Quick Noise Filter checkbox
    noise_check = pane.extra.get("noise_check")
    assert noise_check is not None
    assert noise_check.text() == "Quick Noise Filter"
    assert noise_check.isChecked() is False

    # Storm Detector trigger button
    storm_btn = pane.extra.get("storm_btn")
    assert storm_btn is not None
    assert "Storms" in storm_btn.text()

    # Provider filter badge
    badge_btn = pane.extra.get("provider_filter_badge")
    assert badge_btn is not None
    assert badge_btn.isHidden() is True


def test_event_viewer_module_muting_and_isolating_actions(qapp):
    """One-click actions on EventViewerModule update state and trigger view refresh."""
    from modules.event_viewer.event_viewer_module import EventViewerModule

    mod = EventViewerModule()
    pane = mod.create_widget()

    # Mute provider
    mod.mute_provider("DCOM")
    assert "DCOM" in mod.muted_providers
    badge = pane.extra.get("provider_filter_badge")
    assert badge.isHidden() is False
    assert "Muted: 1" in badge.text()

    # Unmute provider
    mod.unmute_provider("DCOM")
    assert "DCOM" not in mod.muted_providers
    assert badge.isHidden() is True

    # Isolate provider
    mod.isolate_provider("Service Control Manager")
    assert mod.isolated_provider == "Service Control Manager"
    assert badge.isHidden() is False
    assert "Isolated: Service Control Manager" in badge.text()

    # Clear filters
    mod.clear_provider_filters()
    assert mod.isolated_provider is None
    assert len(mod.muted_providers) == 0
    assert badge.isHidden() is True


def test_event_viewer_module_noise_filter_toggle(qapp):
    """Toggling Quick Noise Filter updates module state."""
    from modules.event_viewer.event_viewer_module import EventViewerModule

    mod = EventViewerModule()
    pane = mod.create_widget()

    check = pane.extra["noise_check"]
    assert mod.hide_noise is False

    check.setChecked(True)
    assert mod.hide_noise is True

    check.setChecked(False)
    assert mod.hide_noise is False

    mod.set_noise_filter(True)
    assert mod.hide_noise is True
    assert check.isChecked() is True


def test_storm_indicator_reflects_bursts(qapp):
    """When entries contain an event storm, tile and button show alert styling."""
    from modules.event_viewer.event_viewer_module import EventViewerModule

    mod = EventViewerModule()
    pane = mod.create_widget()

    # 40 events in 30 seconds -> severe storm
    stormy_entries = [_ev("FloodingApp", 999, seconds_ago=i * 0.7) for i in range(40)]
    mod._summary(stormy_entries)

    assert mod._last_storm_report is not None
    assert mod._last_storm_report.has_storm is True

    assert "1 storm" in mod._storm_tile.value.text()
    assert mod._storm_tile.severity in ("error", "danger")

    storm_btn = pane.extra["storm_btn"]
    assert "⚡ Storms (1)" in storm_btn.text()


def test_storm_detector_dialog(qapp):
    """StormDetectorDialog renders storms table, top noisy providers, and handles mute/isolate."""
    from modules.event_viewer.event_viewer_module import EventViewerModule
    from modules.event_viewer.storm_detector_dialog import StormDetectorDialog

    mod = EventViewerModule()
    pane = mod.create_widget()

    # Create entries with one storm and multiple noisy providers
    entries = []
    for i in range(35):
        entries.append(_ev("RunawayDriver", 501, seconds_ago=i * 0.5))
    for i in range(10):
        entries.append(_ev("NormalService", 7000, seconds_ago=i * 10))

    report = ea.detect_event_storms(entries, burst_threshold=30)
    dlg = StormDetectorDialog(report=report, module=mod, parent=pane)

    # Check dialog tabs and rows
    assert dlg.tabs.count() == 3
    assert dlg.storm_table.rowCount() == 1
    assert dlg.storm_table.item(0, 0).text() == "RunawayDriver"
    assert "35 ev/60s" in dlg.storm_table.item(0, 1).text()

    assert dlg.prov_table.rowCount() == 2
    assert dlg.prov_table.item(0, 0).text() == "RunawayDriver"
    assert dlg.prov_table.item(1, 0).text() == "NormalService"

    # Test Mute button via action widget
    act_widget = dlg.storm_table.cellWidget(0, 5)
    mute_btn = act_widget.findChild(type(dlg.findChild(type(dlg))), "")
    # Find QPushButton for mute
    from PyQt6.QtWidgets import QPushButton
    buttons = act_widget.findChildren(QPushButton)
    assert len(buttons) >= 2
    mute_btn = buttons[0]
    isolate_btn = buttons[1]

    assert "Mute" in mute_btn.text()
    mute_btn.click()
    assert "RunawayDriver" in mod.muted_providers

    # Test Isolate button
    isolate_btn.click()
    assert mod.isolated_provider == "RunawayDriver"
    assert "RunawayDriver" not in mod.muted_providers

    # Clear filters
    dlg._clear_all_filters()
    assert mod.isolated_provider is None
    assert len(mod.muted_providers) == 0

    dlg.close()
