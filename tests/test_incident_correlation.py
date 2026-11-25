"""Tests for cross-log incident correlation, spike detection, and causal cascades."""
from datetime import datetime, timedelta
import pytest

from core.incident_correlation import (
    correlate_incident_window,
    EventSpike,
    CascadePattern,
    RootCauseCandidate,
)
from core.types import LogEntry


def _entry(timestamp: datetime, source: str, level: str, message: str, **raw) -> LogEntry:
    return LogEntry(timestamp=timestamp, source=source, level=level, message=message, raw=raw)


def test_correlate_incident_window_slices_within_bounds():
    t0 = datetime(2026, 11, 19, 14, 30, 0)
    e_inside1 = _entry(t0 - timedelta(minutes=2), "EventViewer", "Warning", "Disk latency high")
    e_inside2 = _entry(t0 + timedelta(minutes=3), "CBS", "Error", "0x800f081f payload missing")
    e_outside_before = _entry(t0 - timedelta(minutes=10), "System", "Info", "Old event")
    e_outside_after = _entry(t0 + timedelta(minutes=10), "System", "Info", "Later event")

    sources = {
        "Event Viewer": [e_inside1, e_outside_before],
        "CBS Log": [e_inside2, e_outside_after],
    }

    result = correlate_incident_window(anchor_time=t0, entries_by_source=sources, window_minutes=5)
    assert result.total_events == 2
    assert result.error_count == 1
    assert result.warning_count == 1
    assert [e.message for e in result.events] == ["Disk latency high", "0x800f081f payload missing"]


def test_correlate_precursor_chain_detection():
    t_anchor = datetime(2026, 11, 19, 14, 30, 0)
    # Precursor 45 seconds before the crash
    precursor_warn = _entry(t_anchor - timedelta(seconds=45), "Display", "Warning", "Display driver nvlddmkm stopped responding and recovered (Event 4101)")
    # Critical crash at anchor time
    crash_event = _entry(t_anchor, "BugCheck", "Critical", "BugCheck 0x00000116 VIDEO_TDR_FAILURE", event_id=1001)

    sources = {
        "Event Viewer": [precursor_warn, crash_event],
    }

    result = correlate_incident_window(anchor_time=t_anchor, entries_by_source=sources, window_minutes=5, precursor_seconds=120)
    assert len(result.precursors) == 1
    assert result.precursors[0].source == "Display"
    assert "4101" in result.precursors[0].message


def test_gpu_tdr_to_kernel_crash_cascade():
    t0 = datetime(2026, 11, 19, 12, 0, 0)
    gpu_tdr = _entry(t0 - timedelta(seconds=10), "Display", "Warning", "Display driver nvlddmkm timed out", event_id=4101)
    bsod = _entry(t0, "BugCheck", "Critical", "The computer has rebooted from a bugcheck 0x00000116", event_id=1001)

    result = correlate_incident_window(
        anchor_time=t0,
        entries_by_source={"Logs": [gpu_tdr, bsod]},
        window_minutes=5,
    )

    cascade_names = [c.name for c in result.cascades]
    assert "GPU TDR -> Kernel Crash" in cascade_names
    assert len(result.root_causes) > 0
    assert "GPU Driver Hang" in result.root_causes[0].title
    assert result.root_causes[0].confidence == "High"


def test_storage_degradation_cascade():
    t0 = datetime(2026, 11, 19, 9, 15, 0)
    disk_retry = _entry(t0 - timedelta(seconds=30), "disk", "Warning", "The IO operation at logical block address was retried", event_id=153)
    ntfs_corrupt = _entry(t0 - timedelta(seconds=5), "ntfs", "Error", "The file system structure on volume C: is corrupt", event_id=55)
    app_crash = _entry(t0, "Application Error", "Error", "Faulting application name: sqlservr.exe, exception code 0xc0000006", event_id=1000)

    result = correlate_incident_window(
        anchor_time=t0,
        entries_by_source={"Events": [disk_retry, ntfs_corrupt, app_crash]},
        window_minutes=5,
    )

    cascade_names = [c.name for c in result.cascades]
    assert "Storage Degradation -> System Corruption" in cascade_names
    assert "Disk Storage Subsystem Fault" in result.root_causes[0].title


def test_cbs_to_windows_update_cascade():
    t0 = datetime(2026, 11, 19, 16, 45, 0)
    cbs_err = _entry(t0 - timedelta(minutes=1), "CBS", "Error", "Failed to find package payload, error 0x800f081f (CBS_E_SOURCE_MISSING)")
    wu_err = _entry(t0, "WindowsUpdateClient", "Error", "Installation Failure: Windows failed to install update KB5044284", event_id=20)

    result = correlate_incident_window(
        anchor_time=t0,
        entries_by_source={"Servicing": [cbs_err, wu_err]},
        window_minutes=5,
    )

    cascade_names = [c.name for c in result.cascades]
    assert "Component Store Corruption -> Windows Update Failure" in cascade_names
    assert "CBS Component Store Corruption" in result.root_causes[0].title


def test_event_velocity_spike_detection():
    t0 = datetime(2026, 11, 19, 18, 0, 0)
    # Generate 20 events in the same minute
    burst = [
        _entry(t0 + timedelta(seconds=i * 2), "DCOM", "Warning", f"DCOM 10016 permission warning {i}", event_id=10016)
        for i in range(20)
    ]

    result = correlate_incident_window(
        anchor_time=t0,
        entries_by_source={"DCOM": burst},
        window_minutes=5,
    )

    assert len(result.spikes) >= 1
    assert result.spikes[0].count == 20
    assert result.spikes[0].top_sources[0][0] == "DCOM"
