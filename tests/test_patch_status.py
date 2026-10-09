"""modules.updates.patch_status: patch level from Windows Update history."""
from datetime import datetime, timezone

import pytest

from modules.updates import patch_status as ps
from modules.system_report import report_sections as rs

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def _e(day, title, code=2, hresult=0):
    return ps.HistoryEntry(datetime(2026, *day, tzinfo=timezone.utc), title, code, hresult)


# Real titles from this machine's history (2026-10-09).
SECURITY = "2026-09 Security Update (KB5129195) (26200.9457)"
PREVIEW = "2026-09 Preview Update (KB5124010) (26200.9550)"
FEATURE = "Windows 11, version 26H2"
DEFENDER = "Security Intelligence Update for Microsoft Defender Antivirus - KB2267602 (Version 1.459.634.0)"
STORE = "9WZDNCRFHVN5-MICROSOFT.WINDOWSCALCULATOR"
AMD = "Advanced Micro Devices, Inc. - Display - 31.0.14000.58004"


@pytest.mark.parametrize("title,os_update,security", [
    (SECURITY, True, True),
    (PREVIEW, True, False),
    ("Cumulative Update for Windows 11 Version 24H2 for x64-based Systems (KB5040442)", True, True),
    (DEFENDER, False, False),
    ("2026-09 .NET Framework Security Update (KB5126052)", False, False),
    (FEATURE, False, False),
])
def test_os_updates_are_told_apart_by_their_build_suffix(title, os_update, security):
    assert ps.is_os_quality_update(title) is os_update
    assert ps.is_security_update(title) is security


def test_daily_definitions_do_not_count_as_being_patched():
    status = ps.analyse([_e((9, 14), SECURITY), _e((10, 9), DEFENDER), _e((10, 9), STORE)], now=NOW)
    assert status.last_security.title == SECURITY
    assert status.last_os_update.title == SECURITY


def test_preview_is_an_os_update_but_not_a_security_one():
    status = ps.analyse([_e((9, 14), SECURITY), _e((9, 23), PREVIEW), _e((9, 30), FEATURE)], now=NOW)
    assert status.last_security.title == SECURITY
    assert status.last_os_update.title == PREVIEW
    assert status.last_feature.title == FEATURE


def test_a_failure_that_later_succeeded_is_not_reported():
    """Real: AMD display driver failed 2026-09-23, installed 2026-09-24."""
    status = ps.analyse([_e((9, 23), AMD, code=4), _e((9, 24), AMD)], now=NOW)
    assert status.unresolved_failures == []


def test_a_failure_still_standing_is_reported_but_store_noise_is_not():
    status = ps.analyse([_e((10, 1), SECURITY.replace("09", "10", 1), code=4, hresult=0x80073712),
                         _e((10, 7), STORE, code=4)], now=NOW)
    assert [e.result_code for e in status.unresolved_failures] == [4]
    assert "Security Update" in status.unresolved_failures[0].title


def test_old_failures_age_out():
    status = ps.analyse([_e((8, 1), AMD, code=4)], now=NOW)
    assert status.unresolved_failures == []


def test_report_flags_a_missed_patch_cycle():
    status = ps.analyse([_e((7, 14), SECURITY)], now=NOW)
    _s, findings = rs.patch_sections(reader=lambda: (status, ""), now=NOW)
    assert any(f.severity == "warning" and "No security update for 87 days" in f.title for f in findings)


def test_report_is_quiet_when_patched_this_cycle():
    status = ps.analyse([_e((9, 14), SECURITY), _e((9, 23), PREVIEW)], now=NOW)
    sections, findings = rs.patch_sections(reader=lambda: (status, ""), now=NOW)
    assert findings == []
    rows = dict(sections[0].rows)
    assert rows["Last security update"].startswith("2026-09-14 (25 d ago)")


def test_report_explains_a_failed_update_and_lists_it():
    status = ps.analyse([_e((10, 1), "2026-10 Security Update (KB5130000) (26200.9600)",
                            code=4, hresult=0x80073712)], now=NOW)
    sections, findings = rs.patch_sections(reader=lambda: (status, ""), now=NOW)
    assert sections[1].title.startswith("Failed updates")
    assert any(f.title.startswith("Update failed") for f in findings)


def test_unreadable_history_is_not_reported_as_patched():
    sections, findings = rs.patch_sections(reader=lambda: (None, "COM refused"))
    assert sections[0].error == "COM refused"
    assert findings[0].title == "Update history could not be read"


def test_real_machine_history_reads_unelevated():
    import pythoncom
    pythoncom.CoInitialize()
    try:
        status, reason = ps.read_patch_status()
    finally:
        pythoncom.CoUninitialize()
    assert status is not None, reason
    assert status.entries_read > 0
