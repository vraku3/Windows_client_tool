"""Unit tests for Diagnostic Incident Packaging and Reporting."""
import csv
import io
import json
import os
import zipfile
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from core.diagnostic_packager import (
    analyze_diagnostics,
    create_incident_package,
    generate_report_html,
    generate_system_summary_txt,
    get_system_info,
    normalize_entry,
    sanitize_pii,
)
from core.types import LogEntry


# ---------------------------------------------------------------------------
# PII Sanitization Tests
# ---------------------------------------------------------------------------

def test_sanitize_pii_user_paths():
    sample = r"Failed to open C:\Users\john_doe\AppData\Local\Temp\crash.log and D:\Users\Alice.Smith\file.txt"
    sanitized = sanitize_pii(sample)
    assert r"C:\Users\<REDACTED>" in sanitized
    assert "john_doe" not in sanitized
    assert "Alice.Smith" not in sanitized


def test_sanitize_pii_domain_accounts():
    sample = r"Access denied for CORP\johndoe while querying registry"
    sanitized = sanitize_pii(sample)
    assert r"<DOMAIN>\<USER>" in sanitized
    assert "CORP\\johndoe" not in sanitized


def test_sanitize_pii_ipv4():
    sample = "Connected to 192.168.1.150 and 10.0.0.22, but skipped 127.0.0.1 and 0.0.0.0"
    sanitized = sanitize_pii(sample)
    assert "192.168.1.150" not in sanitized
    assert "10.0.0.22" not in sanitized
    assert "10.x.x.x" in sanitized
    assert "127.0.0.1" in sanitized
    assert "0.0.0.0" in sanitized


def test_sanitize_pii_sids():
    sample = "Security descriptor owner: S-1-5-21-3623811015-3361044348-30300820-1013"
    sanitized = sanitize_pii(sample)
    assert "S-1-5-21-<REDACTED>" in sanitized
    assert "3623811015" not in sanitized


def test_sanitize_pii_emails_and_macs():
    sample = "Notify alerts@enterprise.org on node 00:1A:2B:3C:4D:5E with MAC 00-11-22-33-44-55"
    sanitized = sanitize_pii(sample)
    assert "<EMAIL_REDACTED>" in sanitized
    assert "alerts@enterprise.org" not in sanitized
    assert "<MAC_REDACTED>" in sanitized
    assert "00:1A:2B:3C:4D:5E" not in sanitized


def test_sanitize_pii_credentials():
    sample = "Connection failed with password=SecretPassword123! and token: eyJhbGciOi"
    sanitized = sanitize_pii(sample)
    assert "password=<REDACTED>" in sanitized
    assert "SecretPassword123!" not in sanitized
    assert "token=<REDACTED>" in sanitized
    assert "eyJhbGciOi" not in sanitized


def test_sanitize_pii_edge_cases():
    assert sanitize_pii("") == ""
    assert sanitize_pii(None) == ""
    assert sanitize_pii("Clean log message with no PII") == "Clean log message with no PII"


# ---------------------------------------------------------------------------
# Normalization & Analysis Tests
# ---------------------------------------------------------------------------

def test_normalize_entry_from_log_entry():
    now = datetime(2026, 11, 18, 14, 30, 0)
    entry = LogEntry(
        timestamp=now,
        source="Service Control Manager",
        level="Error",
        message="The Print Spooler terminated unexpectedly.",
        raw={"event_id": 7034},
    )
    norm = normalize_entry(entry, default_channel="Event Viewer")
    assert norm["channel"] == "Event Viewer"
    assert norm["timestamp"] == "2026-11-18 14:30:00"
    assert norm["level"] == "Error"
    assert norm["source"] == "Service Control Manager"
    assert "Print Spooler" in norm["message"]
    assert norm["raw"] == {"event_id": 7034}


def test_normalize_entry_from_dict():
    raw_dict = {
        "timestamp": "2026-11-18T10:15:00",
        "channel": "CBS Log",
        "level": "Warning",
        "source": "CBS",
        "message": "Package verification warning",
        "raw": None,
    }
    norm = normalize_entry(raw_dict, default_channel="DefaultChan")
    assert norm["channel"] == "CBS Log"
    assert norm["timestamp"] == "2026-11-18 10:15:00"
    assert norm["level"] == "Warning"
    assert norm["raw"] == {}


def test_analyze_diagnostics_counts_and_faulting_sources():
    t1 = datetime(2026, 11, 18, 10, 0, 0)
    t2 = datetime(2026, 11, 18, 10, 5, 0)
    t3 = datetime(2026, 11, 18, 10, 10, 0)

    readers_data = {
        "Event Viewer": [
            LogEntry(t1, "Service Control Manager", "Error", "Service crash 7034", {}),
            LogEntry(t2, "Service Control Manager", "Error", "Service crash 7031", {}),
            LogEntry(t3, "Kernel-Power", "Warning", "System sleep entered", {}),
        ],
        "CBS Log": [
            LogEntry(t1, "CBS", "Error", "0x800f081f Component store corruption in WinSxS", {}),
            LogEntry(t2, "CBS", "Info", "Initialization finished", {}),
        ],
    }

    analysis = analyze_diagnostics(readers_data)
    assert analysis["total_events"] == 5
    assert analysis["error_count"] == 3
    assert analysis["warning_count"] == 1
    assert analysis["info_count"] == 1

    top = analysis["top_faulting"]
    assert len(top) >= 2
    assert top[0]["source"] == "Service Control Manager"
    assert top[0]["error_count"] == 2
    assert top[1]["source"] == "CBS"
    assert top[1]["error_count"] == 1

    # Check that CBS pattern triggered DISM repair recommendation
    rems = analysis["remediations"]
    assert any("DISM" in r["title"] or "Component Store" in r["title"] for r in rems)


def test_analyze_diagnostics_clean_logs_healthy_remediation():
    readers_data = {
        "Windows Update": [
            LogEntry(datetime.now(), "WindowsUpdateClient", "Info", "Installation successful", {}),
        ]
    }
    analysis = analyze_diagnostics(readers_data)
    assert analysis["error_count"] == 0
    assert analysis["warning_count"] == 0
    rems = analysis["remediations"]
    assert len(rems) == 1
    assert "Routine Maintenance" in rems[0]["title"]


# ---------------------------------------------------------------------------
# Report Generation & Packaging Tests
# ---------------------------------------------------------------------------

def test_system_info_collection():
    info = get_system_info()
    assert "hostname" in info
    assert "os_name" in info
    assert "architecture" in info
    assert "boot_time" in info
    assert "uptime" in info


def test_generate_system_summary_txt():
    sys_info = {
        "hostname": "TEST-HOST",
        "os_name": "Windows 11 Pro",
        "os_version": "10.0.26100",
        "architecture": "x64",
        "boot_time": "2026-11-18 08:00:00",
        "uptime": "1d 2h 30m",
    }
    analysis = {
        "total_events": 10,
        "error_count": 2,
        "warning_count": 3,
        "info_count": 5,
        "channel_stats": {"Event Viewer": {"total": 10, "errors": 2, "warnings": 3}},
        "top_faulting": [{"source": "Disk", "error_count": 2, "warning_count": 0}],
        "remediations": [{"title": "Check Disk", "module": "Disk Health", "reason": "I/O errors", "severity": "High"}],
    }
    txt = generate_system_summary_txt(sys_info, analysis, extra_notes="User notes: BSOD occurred")
    assert "TEST-HOST" in txt
    assert "Windows 11 Pro" in txt
    assert "User notes: BSOD occurred" in txt
    assert "Total Analyzed Events: 10" in txt
    assert "Errors / Critical:     2" in txt
    assert "Disk" in txt
    assert "Check Disk" in txt


def test_generate_report_html_structure():
    sys_info = {
        "hostname": "TEST-RIG",
        "os_name": "Windows 11",
        "os_version": "10.0.26100",
        "architecture": "x64",
        "uptime": "3h 15m",
    }
    t = datetime(2026, 11, 18, 12, 0, 0)
    analysis = {
        "total_events": 2,
        "error_count": 1,
        "warning_count": 1,
        "info_count": 0,
        "channel_stats": {"Crash Dumps": {"total": 2, "errors": 1, "warnings": 1}},
        "top_faulting": [{"source": "Kernel-Power", "error_count": 1, "warning_count": 0}],
        "remediations": [{"title": "Kernel Crash Dump Analysis", "module": "Crash Dumps", "reason": "Bugcheck detected", "severity": "Critical"}],
        "entries": [
            {
                "channel": "Crash Dumps",
                "timestamp": "2026-11-18 12:00:00",
                "level": "Error",
                "source": "Kernel-Power",
                "message": "Event 41 BugCheck 0x00000124",
                "raw": {"bugcheck_code": "0x124"},
            }
        ],
    }
    html_out = generate_report_html(sys_info, analysis, extra_notes="System crashed while rendering video.")
    assert "<!DOCTYPE html>" in html_out
    assert "TEST-RIG" in html_out
    assert "CRITICAL / ERRORS DETECTED" in html_out
    assert "Kernel-Power" in html_out
    assert "BugCheck 0x00000124" in html_out
    assert "System crashed while rendering video" in html_out
    assert "Kernel Crash Dump Analysis" in html_out


def test_create_incident_package_end_to_end(tmp_path):
    zip_path = tmp_path / "subfolder" / "Test_Incident_Bundle.zip"

    now = datetime(2026, 11, 18, 15, 0, 0)
    readers_data = {
        "Event Viewer": [
            LogEntry(now, "Service Control Manager", "Error", r"C:\Users\SecretUser\crash.exe died with password=123", {"pid": 1234}),
            LogEntry(now, "Kernel-General", "Info", "Operating system started", {}),
        ],
        "DISM Log": [
            LogEntry(now, "DISM", "Warning", "Package servicing warning on 192.168.1.55", {}),
        ],
    }

    stats = create_incident_package(
        output_zip_path=str(zip_path),
        readers_data=readers_data,
        extra_notes="Stutter observed under heavy disk usage.",
    )

    # Verify return dictionary keys
    assert stats["total_events"] == 3
    assert stats["error_count"] == 1
    assert stats["warning_count"] == 1
    assert stats["info_count"] == 1
    assert stats["zip_size_bytes"] > 0
    assert stats["output_path"] == str(zip_path.resolve())
    assert set(stats["channels"]) == {"Event Viewer", "DISM Log"}

    # Verify ZIP structure
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path, "r") as zf:
        namelist = zf.namelist()
        assert set(namelist) == {"report.html", "events.json", "events_sanitized.csv", "system_summary.txt"}

        # Check events.json
        events_json = json.loads(zf.read("events.json").decode("utf-8"))
        assert events_json["summary"]["total_events"] == 3
        assert events_json["notes"] == "Stutter observed under heavy disk usage."
        assert len(events_json["entries"]) == 3

        # Check events_sanitized.csv
        csv_text = zf.read("events_sanitized.csv").decode("utf-8")
        reader = list(csv.reader(io.StringIO(csv_text)))
        assert reader[0] == ["Timestamp", "Channel", "Level", "Source", "Message", "RawDetails"]
        # Verify PII masking in CSV
        assert "SecretUser" not in csv_text
        assert "192.168.1.55" not in csv_text
        assert "password=<REDACTED>" in csv_text or "password=" not in csv_text

        # Check system_summary.txt
        summary_txt = zf.read("system_summary.txt").decode("utf-8")
        assert "DIAGNOSTIC INCIDENT SUMMARY" in summary_txt
        assert "Stutter observed under heavy disk usage" in summary_txt

        # Check report.html
        html_text = zf.read("report.html").decode("utf-8")
        assert "Diagnostic Incident Report" in html_text
        assert "Service Control Manager" in html_text


def test_create_incident_package_empty_data(tmp_path):
    zip_path = tmp_path / "empty_bundle.zip"
    stats = create_incident_package(str(zip_path), {})
    assert stats["total_events"] == 0
    assert stats["error_count"] == 0
    assert stats["warning_count"] == 0
    assert stats["zip_size_bytes"] > 0
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path, "r") as zf:
        assert "report.html" in zf.namelist()
        assert "events.json" in zf.namelist()


# ---------------------------------------------------------------------------
# UI Integration Tests (IncidentExportDialog & DiagnoseModule)
# ---------------------------------------------------------------------------

def test_incident_export_dialog_initialization(qapp, tmp_path):
    from modules.diagnose.incident_export_dialog import IncidentExportDialog

    child1 = MagicMock()
    child1.name = "Event Viewer"
    pane1 = MagicMock()
    pane1._source = [LogEntry(datetime.now(), "SCM", "Error", "fail", {})]
    child1._pane = pane1

    child2 = MagicMock()
    child2.name = "CBS Log"
    child2._pane = None

    dlg = IncidentExportDialog(children=[child1, child2], current_index=0)
    assert dlg.windowTitle() == "Export Diagnostic Incident Bundle"

    # Test all tabs collection
    dlg._rb_all.setChecked(True)
    all_data = dlg._collect_readers_data()
    assert "Event Viewer" in all_data
    assert len(all_data["Event Viewer"]) == 1
    assert "CBS Log" in all_data
    assert len(all_data["CBS Log"]) == 0

    # Test active tab collection
    dlg._rb_active.setChecked(True)
    active_data = dlg._collect_readers_data()
    assert "Event Viewer" in active_data
    assert "CBS Log" not in active_data

    dlg.close()


def test_diagnose_module_has_export_button(qapp):
    from PyQt6.QtWidgets import QTabWidget
    from modules.diagnose.diagnose_module import DiagnoseModule

    mod = DiagnoseModule()
    tabs = QTabWidget()
    widget = mod.wrap(tabs)
    assert hasattr(mod, "_export_bundle_btn")
    assert "Export Incident Bundle" in mod._export_bundle_btn.text()
    assert callable(mod._export_incident_bundle)


def test_dialog_fallback_to_entries_when_source_is_none(qapp):
    from modules.diagnose.incident_export_dialog import IncidentExportDialog

    child = MagicMock()
    child.name = "Timeline"
    pane = MagicMock()
    pane._source = None
    pane._entries = [LogEntry(datetime.now(), "Tweaks", "Info", "Applied tweak", {})]
    child._pane = pane

    dlg = IncidentExportDialog(children=[child], current_index=0)
    dlg._rb_all.setChecked(True)
    data = dlg._collect_readers_data()
    assert "Timeline" in data
    assert len(data["Timeline"]) == 1
    assert data["Timeline"][0].message == "Applied tweak"
    dlg.close()


def test_sanitize_pii_complex_compound_log():
    raw_log = (
        r"CRITICAL: User DOMAIN\admin on C:\Users\superadmin\bin\app.exe (SID: S-1-5-21-1111-2222-3333-4444) "
        r"connected to 172.16.254.1 with secret=SuperSecretAPIKey! Contact sysops@cloudcorp.com or 00:1B:44:11:3A:B7"
    )
    san = sanitize_pii(raw_log)
    assert "superadmin" not in san
    assert "DOMAIN\\admin" not in san
    assert "172.16.254.1" not in san
    assert "1111-2222-3333-4444" not in san
    assert "SuperSecretAPIKey!" not in san
    assert "sysops@cloudcorp.com" not in san
    assert "00:1B:44:11:3A:B7" not in san
    assert "<DOMAIN>\\<USER>" in san
    assert r"C:\Users\<REDACTED>" in san
    assert "S-1-5-21-<REDACTED>" in san
    assert "10.x.x.x" in san
    assert "secret=<REDACTED>" in san
    assert "<EMAIL_REDACTED>" in san
    assert "<MAC_REDACTED>" in san


def test_normalize_entry_timestamp_types():
    # Integer timestamp
    e_int = {"timestamp": 1700000000, "source": "S", "level": "Info", "message": "M"}
    n_int = normalize_entry(e_int)
    assert n_int["timestamp_dt"] is not None
    assert "2023-" in n_int["timestamp"]

    # Float timestamp
    e_flt = {"timestamp": 1700000000.5, "source": "S", "level": "Info", "message": "M"}
    n_flt = normalize_entry(e_flt)
    assert n_flt["timestamp_dt"] is not None

    # None timestamp
    e_none = {"timestamp": None, "source": "S", "level": "Info", "message": "M"}
    n_none = normalize_entry(e_none)
    assert n_none["timestamp_dt"] is None
    assert n_none["timestamp"] == "—"

    # Arbitrary string
    e_str = {"timestamp": "NotADate", "source": "S", "level": "Info", "message": "M"}
    n_str = normalize_entry(e_str)
    assert n_str["timestamp"] == "NotADate"


def test_create_incident_package_deep_nested_directory(tmp_path):
    nested_path = tmp_path / "deep" / "nested" / "path" / "incident.zip"
    res = create_incident_package(str(nested_path), {"Event Viewer": []}, extra_notes="Deep path test")
    assert nested_path.exists()
    assert res["zip_size_bytes"] > 0
    assert res["total_events"] == 0

