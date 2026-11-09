"""Tests for Crash and Freeze Root Cause Analysis (RCA) engine."""
from datetime import datetime, timedelta

from core.types import LogEntry
from modules.crash_dumps.rca_engine import (
    correlate_incidents,
    parse_kernel_power_41,
    parse_whea_record,
)


def test_kernel_power_41_power_cut():
    now = datetime(2026, 11, 1, 14, 30, 0)
    data = {
        "BugcheckCode": "0",
        "BugcheckParameter1": "0x0",
        "BugcheckParameter2": "0x0",
        "BugcheckParameter3": "0x0",
        "BugcheckParameter4": "0x0",
        "PowerButtonTimestamp": "0",
        "SleepInProgress": "0",
    }
    rec = parse_kernel_power_41(data, now)
    assert rec.is_power_cut is True
    assert rec.bugcheck_code == 0
    assert "Abrupt Power Loss" in rec.diagnosis
    assert "PSU" in rec.cause or "power" in rec.cause


def test_kernel_power_41_with_bugcheck():
    now = datetime(2026, 11, 1, 15, 0, 0)
    data = {
        "BugcheckCode": "0x124",
        "BugcheckParameter1": "0x0",
        "BugcheckParameter2": "0xffffd000",
        "BugcheckParameter3": "0x0",
        "BugcheckParameter4": "0x0",
    }
    rec = parse_kernel_power_41(data, now)
    assert rec.is_power_cut is False
    assert rec.bugcheck_code == 0x124
    assert "WHEA_UNCORRECTABLE_ERROR" in rec.diagnosis


def test_whea_record_parsing():
    now = datetime(2026, 11, 1, 16, 0, 0)
    data = {
        "ProcessorAPICID": "4",
        "Bank": "2",
        "MCi_STATUS": "0xBE00000000800400",
        "ErrorSource": "3",
    }
    rec = parse_whea_record(19, data, "A corrected hardware error has occurred. APIC ID: 4", now)
    assert rec.event_id == 19
    assert rec.apic_id == 4
    assert rec.bank == 2
    assert rec.is_fatal is False
    assert "APIC ID 4" in rec.description


def test_correlate_incidents_deduplicates_temporal_cluster():
    t0 = datetime(2026, 11, 1, 12, 0, 0)
    t1 = t0 + timedelta(seconds=15)  # 15s later, same incident

    entry_kp = LogEntry(
        timestamp=t0,
        source="Microsoft-Windows-Kernel-Power",
        level="Critical",
        message="System rebooted without cleanly shutting down",
        raw={"event_id": 41, "data": {"BugcheckCode": "0x133", "BugcheckParameter1": "1"}},
    )
    entry_wer = LogEntry(
        timestamp=t1,
        source="BugCheck event",
        level="Error",
        message="System restarted after bugcheck DPC_WATCHDOG_VIOLATION",
        raw={"bugcheck_code": "0x133", "bug_check": "DPC_WATCHDOG_VIOLATION"},
    )

    overview = correlate_incidents([entry_kp, entry_wer], window_seconds=60)
    assert overview.total_incidents == 1
    assert overview.bsod_count == 1
    incident = overview.incidents[0]
    assert "DPC_WATCHDOG_VIOLATION" in incident.bugcheck_label
    assert len(incident.evidence_sources) == 2


def test_correlate_incidents_flags_failing_core():
    t0 = datetime(2026, 11, 1, 10, 0, 0)
    entries = []
    for i in range(5):
        entries.append(LogEntry(
            timestamp=t0 + timedelta(minutes=i * 10),
            source="Microsoft-Windows-WHEA-Logger",
            level="Warning",
            message=f"Corrected hardware error on APIC ID 6, occurrence {i}",
            raw={"event_id": 19, "data": {"ProcessorAPICID": "6", "Bank": "1"}},
        ))

    overview = correlate_incidents(entries)
    assert overview.whea_count == 5
    assert overview.core_whea_counts[6] == 5
    assert "APIC ID 6" in overview.suspect_summary


def test_decode_bugcheck_params():
    from modules.crash_dumps.rca_engine import decode_bugcheck_params
    lines_133 = decode_bugcheck_params(0x133, (0, 0x1F4, 0, 0))
    assert any("Single DPC watchdog timeout" in l for l in lines_133)

    lines_9f = decode_bugcheck_params(0x9F, (3, 0xFFFF0000, 0, 0))
    assert any("blocking a power IRP" in l for l in lines_9f)

    lines_124 = decode_bugcheck_params(0x124, (0, 0, 0, 0))
    assert any("Machine Check Exception" in l for l in lines_124)

    lines_d1 = decode_bugcheck_params(0xD1, (0x1000, 2, 1, 0x2000))
    assert any("DISPATCH_LEVEL" in l for l in lines_d1)
    assert any("Write" in l for l in lines_d1)

