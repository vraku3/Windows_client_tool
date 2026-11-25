"""Unit tests for Windows Update & Reliability Deep Analytics.

Validates:
- KB grouping (distinguishing installed, failed, and pending updates)
- Install duration extraction and failure frequency counting
- Error code causes and actionable remediations
- System Stability Index trend (30-day window, directions, stats)
- Failure cluster / burst detection across app crashes, OS faults, and hardware errors
- MTBF (Mean Time Between Failures) computation and formatting
- Glance tiles and enriched summaries in WindowsUpdateModule and ReliabilityModule
"""
from datetime import datetime, timedelta

import pytest

from core.types import LogEntry
from modules.reliability import reliability_analysis as ra
from modules.reliability import reliability_reader as rr
from modules.windows_update import wu_analysis, wu_reader


# ==============================================================================
# Helper Factories
# ==============================================================================

def _wu_entry(
    when: datetime,
    event_name: str,
    event_id: int,
    uid: str,
    hresult: int,
    product: str,
    result: str,
    message: str,
) -> LogEntry:
    level = "Error" if result.lower() in ("failed", "failure") or bool(hresult & 0x80000000) else "Info"
    return LogEntry(
        timestamp=when,
        source="Content Install",
        level=level,
        message=message,
        raw={
            "event_name": event_name,
            "event_id": event_id,
            "product": product,
            "result": result,
            "hresult": hresult,
            "update_id": uid,
        },
    )


def _rel_entry(when: datetime, source: str, level: str, message: str, event_id: int = 0) -> LogEntry:
    return LogEntry(
        timestamp=when,
        source=source,
        level=level,
        message=message,
        raw={"event_id": event_id},
    )


# ==============================================================================
# Windows Update Analytics Tests
# ==============================================================================

def test_group_updates_by_kb_success_vs_failed_vs_pending():
    t0 = datetime(2026, 10, 10, 10, 0, 0)
    entries = [
        # KB5034441 succeeded
        _wu_entry(
            t0,
            "AGENT_INSTALLING_SUCCEEDED",
            183,
            "{AAA1}",
            0,
            "Windows Security Update (KB5034441)",
            "Success",
            "Installation Successful: Windows successfully installed following update: Security Update (KB5034441)",
        ),
        # KB5025239 failed
        _wu_entry(
            t0 + timedelta(minutes=5),
            "AGENT_INSTALLING_FAILED",
            182,
            "{BBB2}",
            0x800F081F,
            "Cumulative Update (KB5025239)",
            "Failure",
            "Installation Failure: Windows failed to install following update with error 0x800f081f: Cumulative Update (KB5025239)",
        ),
        # KB5012170 pending reboot (CBS_E_PENDING 0x800F0820)
        _wu_entry(
            t0 + timedelta(minutes=10),
            "AGENT_INSTALLING_FAILED",
            182,
            "{CCC3}",
            0x800F0820,
            "Security Update (KB5012170)",
            "Failure",
            "Installation Failure: Windows failed to install following update with error 0x800f0820: Security Update (KB5012170)",
        ),
    ]

    groups = wu_reader.group_updates_by_kb(entries)
    assert "KB5034441" in groups
    assert "KB5025239" in groups
    assert "KB5012170" in groups

    assert groups["KB5034441"].status == "Installed"
    assert groups["KB5034441"].install_count == 1
    assert groups["KB5034441"].failure_count == 0

    assert groups["KB5025239"].status == "Failed"
    assert groups["KB5025239"].failure_count == 1
    assert groups["KB5025239"].last_error_code == 0x800F081F
    assert "CBS_E_SOURCE_MISSING" in groups["KB5025239"].error_cause
    assert "DISM" in groups["KB5025239"].remediation

    assert groups["KB5012170"].status == "Pending"
    assert "CBS_E_PENDING" in groups["KB5012170"].error_cause
    assert "Restart the computer" in groups["KB5012170"].remediation


def test_extract_install_durations():
    t0 = datetime(2026, 10, 10, 12, 0, 0)
    entries = [
        # KB5001 start and finish 40s later
        _wu_entry(t0, "AGENT_INSTALLING_STARTED", 181, "{U1}", 0, "Update KB5001", "Success", "Installation Started: KB5001"),
        _wu_entry(t0 + timedelta(seconds=40), "AGENT_INSTALLING_SUCCEEDED", 183, "{U1}", 0, "Update KB5001", "Success", "Installation Successful: KB5001"),
        # KB5001 second run start and finish 20s later
        _wu_entry(t0 + timedelta(minutes=5), "AGENT_INSTALLING_STARTED", 181, "{U1}", 0, "Update KB5001", "Success", "Installation Started: KB5001"),
        _wu_entry(t0 + timedelta(minutes=5, seconds=20), "AGENT_INSTALLING_SUCCEEDED", 183, "{U1}", 0, "Update KB5001", "Success", "Installation Successful: KB5001"),
    ]

    durations = wu_reader.extract_install_durations(entries)
    assert "KB5001" in durations
    assert durations["KB5001"] == [40.0, 20.0]

    groups = wu_reader.group_updates_by_kb(entries)
    assert groups["KB5001"].durations == [40.0, 20.0]
    assert groups["KB5001"].avg_duration == 30.0


def test_extract_failure_frequencies():
    t0 = datetime(2026, 10, 11, 8, 0, 0)
    entries = [
        _wu_entry(t0, "AGENT_INSTALLING_FAILED", 182, "{F1}", 0x8024200B, "KB9001", "Failure", "KB9001 failed"),
        _wu_entry(t0 + timedelta(minutes=1), "AGENT_INSTALLING_FAILED", 182, "{F1}", 0x8024200B, "KB9001", "Failure", "KB9001 failed"),
        _wu_entry(t0 + timedelta(minutes=2), "AGENT_INSTALLING_FAILED", 182, "{F1}", 0x8024200B, "KB9001", "Failure", "KB9001 failed"),
        _wu_entry(t0 + timedelta(minutes=3), "AGENT_INSTALLING_FAILED", 182, "{F2}", 0x80070005, "KB9002", "Failure", "KB9002 failed"),
    ]
    freq = wu_reader.extract_failure_frequencies(entries)
    assert freq["KB9001"] == 3
    assert freq["KB9002"] == 1


@pytest.mark.parametrize("code,expected_token,expected_rem", [
    (0x800F081F, "CBS_E_SOURCE_MISSING", "DISM /Online /Cleanup-Image /RestoreHealth"),
    (0x800F0820, "CBS_E_PENDING", "Restart the computer"),
    (0x800F0922, "CBS_E_INSTALLERS_FAILED", "System Reserved/EFI partition"),
    (0x80240016, "WU_E_INSTALL_NOT_ALLOWED", "reboot the system"),
    (0x80248007, "WU_E_DS_DATANOTFOUND", "SoftwareDistribution"),
    (0x80070643, "ERROR_INSTALL_FAILURE", "Recovery Partition"),
    (0x80073D02, "ERROR_PACKAGES_IN_USE", "Close the target application"),
    (0x80D02002, "DO_E_DOWNLOAD_NO_PROGRESS", "Delivery Optimization"),
])
def test_surface_error_causes_and_actionable_remediation(code, expected_token, expected_rem):
    cause, rem = wu_reader.get_error_remediation(code)
    assert expected_token in cause
    assert expected_rem in rem

    assert expected_rem in wu_analysis.remediation_for_code(code)


def test_wu_detail_html_contains_cause_remediation_and_kb():
    entry = _wu_entry(
        datetime(2026, 10, 15, 14, 0),
        "AGENT_INSTALLING_FAILED",
        182,
        "{U99}",
        0x80070643,
        "Update (KB5034441)",
        "Failure",
        "Installation Failure: Windows failed to install the following update with error 0x80070643: 2026-01 Security Update (KB5034441).",
    )
    html = wu_analysis.detail_html(entry)
    assert "0x80070643" in html
    assert "Actionable Remediation:" in html
    assert "Recovery Partition" in html
    assert "KB5034441" in html


def test_wu_summary_text_incorporates_durations_and_remediation():
    t0 = datetime(2026, 10, 15, 10, 0)
    entries = [
        _wu_entry(t0, "AGENT_INSTALLING_STARTED", 181, "{U1}", 0, "Update KB1", "Success", "Started KB1"),
        _wu_entry(t0 + timedelta(seconds=25), "AGENT_INSTALLING_SUCCEEDED", 183, "{U1}", 0, "Update KB1", "Success", "Succeeded KB1"),
        _wu_entry(t0 + timedelta(minutes=1), "AGENT_INSTALLING_FAILED", 182, "{U2}", 0x800F081F, "KB2", "Failure",
                  "Installation Failure: Windows failed to install the following update with error 0x800f081f: Update (KB2)."),
    ]
    summary = wu_analysis.summary_text(entries)
    assert "Avg install duration: 25s" in summary
    assert "Still failing:" in summary
    assert "KB2" in summary
    assert "0x800F081F" in summary
    assert "Action: Run 'DISM" in summary


def test_wu_module_glance_tiles(qapp):
    from modules.windows_update.wu_module import WindowsUpdateModule
    mod = WindowsUpdateModule()
    mod.create_widget()

    t0 = datetime(2026, 10, 15, 10, 0)
    entries = [
        _wu_entry(t0, "AGENT_INSTALLING_STARTED", 181, "{U1}", 0, "Update KB1", "Success", "Started KB1"),
        _wu_entry(t0 + timedelta(seconds=50), "AGENT_INSTALLING_SUCCEEDED", 183, "{U1}", 0, "Update KB1", "Success", "Succeeded KB1"),
        _wu_entry(t0 + timedelta(minutes=1), "AGENT_INSTALLING_FAILED", 182, "{U2}", 0x800F081F, "KB2", "Failure",
                  "Installation Failure: Windows failed to install the following update with error 0x800f081f: Update (KB2)."),
    ]
    summary = mod._summary(entries)
    assert "1 successful install(s)" in summary
    assert mod._installed_tile.value.text() == "1"
    assert mod._pending_tile.value.text() == "1"
    assert mod._avg_duration_tile.value.text() == "50s"
    assert mod._installed_tile.severity == "ok"
    assert mod._pending_tile.severity == "danger"


# ==============================================================================
# Reliability Analytics Tests
# ==============================================================================

def test_calculate_stability_trend_improving_declining_stable():
    t0 = datetime(2026, 10, 1, 0, 0)

    # 1. Declining trend (10.0 -> 4.5 over 30 days)
    metrics_declining = [ra.Metric(t0 + timedelta(days=i), 10.0 - (5.5 * (i / 30.0))) for i in range(31)]
    trend_dec = ra.calculate_stability_trend(metrics_declining, days=30)
    assert trend_dec.direction == "declining"
    assert trend_dec.current == 4.5
    assert trend_dec.start_score == 10.0
    assert trend_dec.delta == -5.5
    assert "Declining" in trend_dec.description

    # 2. Improving trend (3.0 -> 8.5)
    metrics_improving = [ra.Metric(t0 + timedelta(days=i), 3.0 + (5.5 * (i / 30.0))) for i in range(31)]
    trend_imp = ra.calculate_stability_trend(metrics_improving, days=30)
    assert trend_imp.direction == "improving"
    assert trend_imp.delta == 5.5
    assert "Improving" in trend_imp.description

    # 3. Stable trend (9.0 -> 9.1)
    metrics_stable = [ra.Metric(t0 + timedelta(days=i), 9.0 + (0.1 * (i % 2))) for i in range(31)]
    trend_st = ra.calculate_stability_trend(metrics_stable, days=30)
    assert trend_st.direction == "stable"
    assert "Stable" in trend_st.description

    # 4. Empty history
    trend_empty = ra.calculate_stability_trend([], days=30)
    assert trend_empty.direction == "unknown"
    assert trend_empty.current is None


def test_detect_failure_clusters_bursts():
    t0 = datetime(2026, 10, 20, 14, 0)
    entries = [
        # Burst 1: 4 Application Crashes in 10 minutes
        _rel_entry(t0, "Application Error", "Error", "Faulting application name: App1.exe, Exception code: 0xc0000005", 1000),
        _rel_entry(t0 + timedelta(minutes=2), "Application Error", "Error", "Faulting application name: App1.exe, Exception code: 0xc0000005", 1000),
        _rel_entry(t0 + timedelta(minutes=5), "Application Error", "Error", "Faulting application name: App1.exe, Exception code: 0xc0000005", 1000),
        _rel_entry(t0 + timedelta(minutes=8), "Application Hang", "Error", "The program App1.exe stopped interacting", 1002),

        # Far away event (isolated)
        _rel_entry(t0 + timedelta(days=3), "Application Error", "Error", "App2 crashed", 1000),

        # Burst 2: 3 Hardware errors within 20 minutes
        _rel_entry(t0 + timedelta(days=7), "WHEA-Logger", "Error", "A fatal hardware error has occurred.", 18),
        _rel_entry(t0 + timedelta(days=7, minutes=5), "disk", "Error", "The device, \\Device\\Harddisk0\\DR0, has a bad block.", 7),
        _rel_entry(t0 + timedelta(days=7, minutes=15), "WHEA-Logger", "Error", "A fatal hardware error has occurred.", 18),
    ]

    clusters = ra.detect_failure_clusters(entries, window_hours=1.0, min_events=3)
    assert len(clusters) == 2

    # First cluster is the 4 app crashes (sorted by count descending)
    assert clusters[0].count == 4
    assert clusters[0].category == "Application Crash"
    assert clusters[0].primary_source == "App1.exe"
    assert clusters[0].duration_minutes == 8.0

    # Second cluster is the 3 hardware errors
    assert clusters[1].count == 3
    assert clusters[1].category == "Hardware Error"


def test_calculate_mtbf():
    t0 = datetime(2026, 10, 1, 0, 0)
    # 4 failures occurring every 12 hours: 0h, 12h, 24h, 36h
    # Total span = 36h, intervals = 3, MTBF = 12.0h
    failures = [
        _rel_entry(t0, "Application Error", "Error", "crash 1", 1000),
        _rel_entry(t0 + timedelta(hours=12), "Application Error", "Error", "crash 2", 1000),
        _rel_entry(t0 + timedelta(hours=24), "Application Error", "Error", "crash 3", 1000),
        _rel_entry(t0 + timedelta(hours=36), "Application Error", "Error", "crash 4", 1000),
    ]
    mtbf = ra.calculate_mtbf(failures)
    assert mtbf == 12.0
    assert ra.format_mtbf(mtbf) == "12.0 hrs"

    # Multi-day MTBF formatting
    assert ra.format_mtbf(72.0) == "3.0 days"
    assert ra.format_mtbf(0.5) == "30 mins"

    # Single failure -> MTBF is None
    assert ra.calculate_mtbf([failures[0]]) is None
    assert ra.format_mtbf(None) == "No failures"

    # No failures -> None
    assert ra.calculate_mtbf([]) is None


def test_reliability_module_glance_tiles_and_summary(qapp):
    from modules.reliability.reliability_module import ReliabilityModule
    mod = ReliabilityModule()
    mod.create_widget()

    t0 = datetime(2026, 10, 10, 12, 0)
    mod._metrics = [
        ra.Metric(t0 - timedelta(days=20), 10.0),
        ra.Metric(t0 - timedelta(days=10), 9.0),
        ra.Metric(t0, 7.5),
    ]

    entries = [
        _rel_entry(t0 - timedelta(hours=48), "Application Error", "Error", "crash 1", 1000),
        _rel_entry(t0 - timedelta(hours=24), "Application Error", "Error", "crash 2", 1000),
        _rel_entry(t0, "Application Error", "Error", "crash 3", 1000),
    ]

    summary = mod._summary(entries)
    assert "Stability index now 7.5 / 10" in summary
    assert "30-day trend:" in summary
    assert "MTBF: 24.0 hrs" in summary

    # Check tiles
    assert mod._stability_tile.value.text() == "7.5 / 10"
    assert mod._stability_tile.severity == "ok"

    assert mod._trend_tile.value.text() == "Declining"
    assert mod._trend_tile.severity in ("warning", "danger")

    assert mod._mtbf_tile.value.text() == "24.0 hrs"
    assert mod._mtbf_tile.severity == "warning"


def test_reliability_reader_overview():
    t0 = datetime(2026, 10, 15, 12, 0)
    metrics = [ra.Metric(t0, 8.5)]
    entries = [
        _rel_entry(t0, "Application Error", "Error", "crash", 1000),
        _rel_entry(t0 + timedelta(hours=10), "Application Error", "Error", "crash 2", 1000),
    ]
    overview = rr.analyze_stability_overview(entries, metrics, days=30)
    assert overview["trend"].current == 8.5
    assert overview["total_failures"] == 2
    assert overview["mtbf_hours"] == 10.0
    assert overview["mtbf_formatted"] == "10.0 hrs"
