"""Deep tests for CBS and DISM Servicing Log Analysis.

Verifies:
1. CBSParser:
   - Specific servicing failures (missing payload, manifests not found, store corruption, stage failures)
   - Package stage transitions (Staged, Installed, Superseded, Failed)
   - Extraction of affected package names and target file paths
   - Actionable causes and remedies
   - Benign package probe discrimination
2. DISMParser:
   - Operation parsing (CheckHealth, ScanHealth, RestoreHealth, Add/Remove-Package, StartComponentCleanup)
   - Scratch directory extraction and space warning detection
   - HRESULT extraction and remedies
3. servicing_summary:
   - Stage transitions and failure summaries
   - Rich detail HTML generation for all card types
   - Glance summary strings for CBS and DISM
4. CBSLogModule and DISMLogModule:
   - Widget creation and glance tile updates
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime

from core.types import LogEntry
from modules.cbs_log.cbs_parser import CBSParser
from modules.dism_log.dism_parser import DISMParser
from core import servicing_summary as ss


# ============================================================================
# CBSParser Deep Tests
# ============================================================================

def test_cbs_parser_missing_payload_failure():
    line = (
        "2026-11-18 10:15:30, Error                 CBS    "
        "Failed to find file: C:\\Windows\\servicing\\packages\\Package_for_KB5005565~31bf3856ad364e35~amd64~~10.0.1.0.mum "
        "[HRESULT = 0x800f081f - CBS_E_SOURCE_MISSING]"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(line + "\n")
        path = f.name

    try:
        parser = CBSParser(path)
        entries = parser.parse()
        assert len(entries) == 1
        e = entries[0]
        assert e.level == "Error"
        assert e.source == "CBS"
        assert e.raw.get("failure_type") == "missing_payload"
        assert e.raw.get("hresult") == 0x800F081F
        assert e.raw.get("hresult_name") == "CBS_E_SOURCE_MISSING"
        assert "Package_for_KB5005565" in e.raw.get("package", "")
        assert "Package_for_KB5005565" in e.raw.get("file_path", "")
        assert "mum" in e.raw.get("file_path", "")
        assert "RestoreHealth" in e.raw.get("remedy", "")
        assert "payload" in e.raw.get("cause", "").lower() or "source" in e.raw.get("cause", "").lower()
    finally:
        os.unlink(path)


def test_cbs_parser_manifest_not_found():
    line = (
        "2026-11-18 10:20:00, Error                 CBS    "
        "Manifest not found: C:\\Windows\\WinSxS\\Manifests\\amd64_microsoft-windows-servicingstack_none_12345.manifest "
        "[HRESULT = 0x80070003 - ERROR_PATH_NOT_FOUND]"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(line + "\n")
        path = f.name

    try:
        parser = CBSParser(path)
        entries = parser.parse()
        assert len(entries) == 1
        e = entries[0]
        assert e.raw.get("failure_type") == "manifest_not_found"
        assert e.raw.get("file_path") == "C:\\Windows\\WinSxS\\Manifests\\amd64_microsoft-windows-servicingstack_none_12345.manifest"
        assert "sfc /scannow" in e.raw.get("remedy", "")
    finally:
        os.unlink(path)


def test_cbs_parser_store_corruption():
    line = (
        "2026-11-18 10:22:15, Error                 CSI    "
        "0000001a [SR] Could not reproject corrupted file \\??\\C:\\Windows\\System32\\drivers\\tcpip.sys; "
        "source file in store is also corrupted [HRESULT = 0x80073712 - ERROR_SXS_COMPONENT_STORE_CORRUPT]"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(line + "\n")
        path = f.name

    try:
        parser = CBSParser(path)
        entries = parser.parse()
        assert len(entries) == 1
        e = entries[0]
        assert e.raw.get("failure_type") == "store_corruption"
        assert e.raw.get("hresult") == 0x80073712
        assert "tcpip.sys" in e.raw.get("file_path", "")
        # Cleaned up namespace prefix
        assert not e.raw.get("file_path", "").startswith(r"\??")
        assert "RestoreHealth" in e.raw.get("remedy", "")
    finally:
        os.unlink(path)


def test_cbs_parser_package_stage_transitions():
    lines = [
        "2026-11-18 11:00:01, Info                  CBS    Exec: Staging package: Package_for_RollupFix~31bf3856ad364e35~amd64~~19041.1234.1.0, current: Absent, pending: Default, target: Staged, call count: 1\n",
        "2026-11-18 11:00:02, Info                  CBS    Plan: Package: Package_for_KB5005565~31bf3856ad364e35~amd64~~10.0.1.0, current: Staged, pending: Default, target: Installed, limit: None\n",
        "2026-11-18 11:00:03, Info                  CBS    Appl: Package: Package_for_KB1234567~31bf3856ad364e35~amd64~~10.0.1.0, current: Installed, pending: Default, target: Superseded\n",
        "2026-11-18 11:00:04, Error                 CBS    Package: Package_for_KB9999999~31bf3856ad364e35~amd64~~10.0.1.0 changed state to Failed\n",
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.writelines(lines)
        path = f.name

    try:
        parser = CBSParser(path)
        entries = parser.parse()
        assert len(entries) == 4

        # Staged
        assert entries[0].raw.get("target_state") == "Staged"
        assert entries[0].raw.get("current_state") == "Absent"
        assert entries[0].raw.get("transition") == "Absent -> Staged"
        assert "Package_for_RollupFix" in entries[0].raw.get("package", "")

        # Installed
        assert entries[1].raw.get("target_state") == "Installed"
        assert entries[1].raw.get("current_state") == "Staged"
        assert entries[1].raw.get("transition") == "Staged -> Installed"
        assert "Package_for_KB5005565" in entries[1].raw.get("package", "")

        # Superseded
        assert entries[2].raw.get("target_state") == "Superseded"
        assert entries[2].raw.get("current_state") == "Installed"
        assert entries[2].raw.get("transition") == "Installed -> Superseded"
        assert "Package_for_KB1234567" in entries[2].raw.get("package", "")

        # Failed
        assert entries[3].raw.get("target_state") == "Failed"
        assert entries[3].raw.get("failure_type") == "stage_failure"
        assert "Package_for_KB9999999" in entries[3].raw.get("package", "")
        assert "RestoreHealth" in entries[3].raw.get("remedy", "")
    finally:
        os.unlink(path)


def test_cbs_parser_benign_probe_discrimination():
    line = (
        "2026-11-18 11:05:00, Info                  CBS    "
        "Failed to internally open package. [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(line + "\n")
        path = f.name

    try:
        parser = CBSParser(path)
        entries = parser.parse()
        assert len(entries) == 1
        e = entries[0]
        assert e.raw.get("is_probe") is True
        # Benign probe is NOT classified as a critical servicing failure
        assert e.raw.get("failure_type") is None
    finally:
        os.unlink(path)


# ============================================================================
# DISMParser Deep Tests
# ============================================================================

def test_dism_parser_operations():
    lines = [
        "2026-11-18 12:00:00, Info                  DISM   DISM.EXE: Executing command line: dism.exe /Online /Cleanup-Image /CheckHealth\n",
        "2026-11-18 12:01:00, Info                  DISM   DISM.EXE: Executing command line: Dism.exe /Online /Cleanup-Image /ScanHealth\n",
        "2026-11-18 12:05:00, Info                  DISM   DISM.EXE: Executing command line: dism /Online /Cleanup-Image /RestoreHealth /Source:D:\\sources\\install.wim\n",
        "2026-11-18 12:10:00, Info                  DISM   DISM.EXE: Executing command line: dism /Online /Cleanup-Image /StartComponentCleanup\n",
        "2026-11-18 12:15:00, Info                  DISM   DISM.EXE: Executing command line: dism /Online /Add-Package /PackagePath:C:\\temp\\update.msu\n",
        "2026-11-18 12:20:00, Info                  DISM   DISM Package Manager: PID=1234 TID=5678 Adding package Package_for_KB5012345~31bf3856ad364e35~amd64~~10.0.1.0\n",
        "2026-11-18 12:25:00, Info                  DISM   DISM Package Manager: PID=1234 TID=5678 Removing package Package_for_KB5012345~31bf3856ad364e35~amd64~~10.0.1.0\n",
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.writelines(lines)
        path = f.name

    try:
        parser = DISMParser(path)
        entries = parser.parse()
        assert len(entries) == 7

        assert entries[0].raw.get("operation") == "CheckHealth"
        assert entries[1].raw.get("operation") == "ScanHealth"
        assert entries[2].raw.get("operation") == "RestoreHealth"
        assert entries[3].raw.get("operation") == "StartComponentCleanup"
        assert entries[4].raw.get("operation") == "Add-Package"
        assert entries[5].raw.get("operation") == "Add-Package"
        assert "Package_for_KB5012345" in entries[5].raw.get("target_package", "")
        assert entries[6].raw.get("operation") == "Remove-Package"
        assert "Package_for_KB5012345" in entries[6].raw.get("target_package", "")
    finally:
        os.unlink(path)


def test_dism_parser_scratch_warnings_and_hresults():
    lines = [
        "2026-11-18 12:30:00, Info                  DISM   PID=80280 TID=67836 Scratch directory set to 'C:\\Users\\Admin\\AppData\\Local\\Temp\\'. - CDISMManager::put_ScratchDir\n",
        "2026-11-18 12:30:01, Warning               DISM   PID=80280 TID=67836 Scratch directory at C:\\Users\\Admin\\AppData\\Local\\Temp\\ has less than 512 MB of free space. Operations may fail.\n",
        "2026-11-18 12:30:02, Error                 DISM   PID=80280 TID=67836 Failed to restore image health. [HRESULT = 0x800f081f - CBS_E_SOURCE_MISSING]\n",
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.writelines(lines)
        path = f.name

    try:
        parser = DISMParser(path)
        entries = parser.parse()
        assert len(entries) == 3

        # Scratch directory set
        assert "C:\\Users\\Admin\\AppData\\Local\\Temp\\" in entries[0].raw.get("scratch_dir", "")

        # Scratch low space warning
        assert entries[1].level == "Warning"
        assert entries[1].raw.get("scratch_free_mb") == 512
        assert "512 MB" in entries[1].raw.get("scratch_warning", "")
        assert "/ScratchDir" in entries[1].raw.get("remedy", "")

        # Error with HRESULT
        assert entries[2].level == "Error"
        assert entries[2].raw.get("operation") == "RestoreHealth"
        assert entries[2].raw.get("hresult") == 0x800F081F
        assert entries[2].raw.get("hresult_name") == "CBS_E_SOURCE_MISSING"
        assert "install.wim" in entries[2].raw.get("remedy", "") or "RestoreHealth" in entries[2].raw.get("remedy", "")
    finally:
        os.unlink(path)


# ============================================================================
# Servicing Summary & Rich Details Tests
# ============================================================================

def test_servicing_summary_stage_and_failure_aggregates():
    ts = datetime(2026, 11, 18, 10, 0, 0)
    entries = [
        LogEntry(
            timestamp=ts,
            source="CBS",
            level="Info",
            message="Plan: Package: Package_for_KB1~amd64~~10.0, current: Staged, pending: Default, target: Installed",
            raw={"target_state": "Installed", "package": "Package_for_KB1"},
        ),
        LogEntry(
            timestamp=ts,
            source="CBS",
            level="Info",
            message="Exec: Staging package: Package_for_KB2~amd64~~10.0, current: Absent, pending: Default, target: Staged",
            raw={"target_state": "Staged", "package": "Package_for_KB2"},
        ),
        LogEntry(
            timestamp=ts,
            source="CBS",
            level="Error",
            message="Package: Package_for_KB3 failed. [HRESULT = 0x800f081f]",
            raw={
                "target_state": "Failed",
                "failure_type": "missing_payload",
                "package": "Package_for_KB3",
                "file_path": "C:\\Windows\\servicing\\packages\\KB3.mum",
                "hresult": 0x800F081F,
            },
        ),
    ]

    stages = ss.package_stage_summary(entries)
    assert stages["total_transitions"] == 3
    assert stages["counts"]["Installed"] == 1
    assert stages["counts"]["Staged"] == 1
    assert stages["counts"]["Failed"] == 1
    assert "Package_for_KB3" in stages["failed_packages"]

    failures = ss.servicing_failures_summary(entries)
    assert failures["total_failures"] == 1
    assert failures["counts"]["missing_payload"] == 1
    assert "Package_for_KB3" in failures["affected_packages"]
    assert "C:\\Windows\\servicing\\packages\\KB3.mum" in failures["target_files"]

    summary_text = ss.summarize(entries, "cbs")
    assert "Servicing Failures Detected:" in summary_text
    assert "1 missing payload(s)" in summary_text
    assert "Package Stage Transitions (3):" in summary_text
    assert "Failed Package(s): Package_for_KB3" in summary_text
    assert "RestoreHealth" in summary_text


def test_servicing_summary_dism_operations():
    ts = datetime(2026, 11, 18, 12, 0, 0)
    entries = [
        LogEntry(
            timestamp=ts,
            source="DISM",
            level="Info",
            message="Executing command line: dism /Online /Cleanup-Image /ScanHealth",
            raw={"operation": "ScanHealth", "command": "dism /Online /Cleanup-Image /ScanHealth"},
        ),
        LogEntry(
            timestamp=ts,
            source="DISM",
            level="Warning",
            message="Scratch directory at C:\\Temp has less than 1024 MB",
            raw={"scratch_dir": "C:\\Temp", "scratch_warning": "Low space: 500 MB (< 1024 MB)", "scratch_free_mb": 500},
        ),
    ]

    ops = ss.dism_operations_summary(entries)
    assert ops["total_operations"] == 1
    assert ops["counts"]["ScanHealth"] == 1
    assert ops["scratch_directory"] == "C:\\Temp"
    assert len(ops["scratch_warnings"]) == 1

    summary_text = ss.summarize(entries, "dism")
    assert "DISM Operations (1):" in summary_text
    assert "ScanHealth" in summary_text
    assert "Scratch Dir: C:\\Temp" in summary_text
    assert "/ScratchDir" in summary_text


def test_detail_html_rendering():
    ts = datetime(2026, 11, 18, 10, 0, 0)
    entry = LogEntry(
        timestamp=ts,
        source="CBS",
        level="Error",
        message="Failed to find file: C:\\Windows\\servicing\\packages\\test.mum [HRESULT = 0x800f081f - CBS_E_SOURCE_MISSING]",
        raw={
            "failure_type": "missing_payload",
            "package": "Package_for_KB5005565",
            "file_path": "C:\\Windows\\servicing\\packages\\test.mum",
            "cause": "Component store payload missing.",
            "remedy": "DISM /Online /Cleanup-Image /RestoreHealth",
            "target_state": "Failed",
            "transition": "Staged -> Failed",
            "hresult": 0x800F081F,
            "hresult_name": "CBS_E_SOURCE_MISSING",
        },
    )

    html_out = ss.detail_html(entry)
    # Servicing failure card
    assert "Servicing Failure:" in html_out
    assert "Missing Payload" in html_out
    assert "Package_for_KB5005565" in html_out
    assert "C:\\Windows\\servicing\\packages\\test.mum" in html_out
    assert "DISM /Online /Cleanup-Image /RestoreHealth" in html_out

    # Package stage transition card
    assert "Package Stage Transition:" in html_out
    assert "Staged -&gt; Failed" in html_out or "Staged -> Failed" in html_out

    # Codes translated
    assert "0x800F081F" in html_out
    assert "CBS_E_SOURCE_MISSING" in html_out


# ============================================================================
# UI Integration & Glance Tiles Tests
# ============================================================================

def test_cbs_module_glance_tiles():
    from modules.cbs_log.cbs_module import CBSLogModule
    mod = CBSLogModule()
    pane = mod.create_widget()
    assert pane is not None

    # Check that custom glance tiles exist
    assert mod._store_health_tile is not None
    assert mod._pkg_stages_tile is not None
    assert mod._store_health_tile.caption.text() == "STORE HEALTH"
    assert mod._pkg_stages_tile.caption.text() == "PACKAGE STAGES"

    # Simulate entries loaded through summarizer
    ts = datetime(2026, 11, 18, 10, 0, 0)
    entries = [
        LogEntry(
            timestamp=ts,
            source="CBS",
            level="Error",
            message="Corruption detected [0x80073712]",
            raw={"failure_type": "store_corruption", "target_state": "Failed", "package": "KB123"},
        ),
    ]

    opts = mod.pane_options()
    summarizer = opts["summarizer"]
    text = summarizer(entries)
    assert "Servicing Failures Detected:" in text
    assert mod._store_health_tile.value.text() == "Issues Found"
    assert mod._store_health_tile.severity == "danger"
    assert "1 Fail" in mod._pkg_stages_tile.sub.text()


def test_dism_module_glance_tiles():
    from modules.dism_log.dism_module import DISMLogModule
    mod = DISMLogModule()
    pane = mod.create_widget()
    assert pane is not None

    # Check custom glance tiles exist
    assert mod._dism_ops_tile is not None
    assert mod._scratch_tile is not None
    assert mod._dism_ops_tile.caption.text() == "DISM OPERATIONS"
    assert mod._scratch_tile.caption.text() == "SCRATCH BUFFER"

    # Simulate entries loaded with scratch warning
    ts = datetime(2026, 11, 18, 12, 0, 0)
    entries = [
        LogEntry(
            timestamp=ts,
            source="DISM",
            level="Warning",
            message="Scratch warning",
            raw={"operation": "RestoreHealth", "scratch_dir": "C:\\Temp", "scratch_warning": "Low space"},
        ),
    ]

    opts = mod.pane_options()
    summarizer = opts["summarizer"]
    text = summarizer(entries)
    assert "RestoreHealth" in text
    assert mod._dism_ops_tile.value.text() == "1 Ops"
    assert mod._scratch_tile.value.text() == "Warning"
    assert mod._scratch_tile.severity == "warning"
