"""Reliability, Windows Update and CBS/DISM analysis (Qt-free)."""
from datetime import datetime, timedelta

from core.types import LogEntry
from core import servicing_summary as ss
from modules.reliability import reliability_analysis as ra
from modules.windows_update import wu_analysis
from modules.windows_update.wu_parser import WUParser


def _e(when, source, level, message, raw=None):
    return LogEntry(timestamp=when, source=source, level=level, message=message, raw=raw or {})


# -- reliability -----------------------------------------------------------
def test_a_successful_msi_install_is_not_an_error():
    # The old keyword test saw "error status" and painted every one of these red.
    msg = "Windows Installer installed the product. Installation success or error status: 0."
    assert ra.classify("MsiInstaller", 1033, msg) == "Info"


def test_failures_are_classified_by_what_happened():
    assert ra.classify("Application Error", 1000, "Faulting application") == "Error"
    assert ra.classify("EventLog", 6008, "unexpected") == "Error"
    assert ra.classify("Microsoft-Windows-WindowsUpdateClient", 20, "x") == "Warning"
    assert ra.classify("Vendor", 5, "Installation Failure: Windows failed to install") == "Warning"
    assert ra.classify("Vendor", 5, "Installation Successful") == "Info"


def test_find_dips_attaches_the_failures_in_that_hour():
    t0 = datetime(2026, 9, 7, 16, 0)
    metrics = [ra.Metric(t0 + timedelta(hours=i), idx) for i, idx in enumerate([10, 10, 7.3, 7.4, 7.5])]
    crash = _e(t0 + timedelta(hours=2, minutes=10), "Application Error", "Error", "crash")
    noise = _e(t0 + timedelta(hours=2, minutes=20), "MsiInstaller", "Info", "ok")
    elsewhere = _e(t0 + timedelta(hours=9), "Application Error", "Error", "later")
    dips = ra.find_dips(metrics, [crash, noise, elsewhere])
    assert len(dips) == 1
    assert round(dips[0].drop, 1) == 2.7
    assert dips[0].events == (crash,)


def test_summary_and_detail_use_the_index_of_the_hour():
    t0 = datetime(2026, 9, 7, 16, 0)
    metrics = [ra.Metric(t0, 10.0), ra.Metric(t0 + timedelta(hours=1), 8.0)]
    entry = _e(t0 + timedelta(hours=1, minutes=5), "Application Error", "Error", "x")
    text = ra.summary_text(metrics, [entry])
    assert "now 8.0" in text and "1 drop" in text and "Application Error" in text
    assert "8.0 / 10" in ra.detail_html(entry, metrics)
    assert ra.detail_html(entry, []) == ""


def test_summary_without_history_says_so():
    assert "No stability index history" in ra.summary_text([], [])


def test_parse_wmi_time():
    assert ra.parse_wmi_time("20260926030000.000000-000") == datetime(2026, 9, 26, 3, 0)
    assert ra.parse_wmi_time("garbage") is None and ra.parse_wmi_time(None) is None


# -- windows update --------------------------------------------------------
_FAIL = "\t".join([
    "{CB6DE3B4-CC97-4BD7-B8F2-2AF4A0DB4FCC}", "2026-09-23 15:29:04:734+0300", "1", "182 [AGENT_INSTALLING_FAILED]",
    "101", "{624F8F61-FD5B-4DF9-B992-122BE8FAB976}", "1", "800f0820", "MoUpdateOrchestrator", "Failure",
    "Content Install",
    "Installation Failure: Windows failed to install the following update with error 0x800f0820: "
    "2026-09 Preview Update (KB5124010) (26200.9550).", "id"])
_OK = "\t".join([
    "{28F07B47-27A4-4C40-A515-3DC37B8FE9EF}", "2026-09-16 08:49:57:938+0300", "1", "162 [AGENT_DOWNLOAD_SUCCEEDED]",
    "101", "{052ED135-1FD7-44D5-AB39-FF92E65A8D10}", "1", "0", "Update;Scan", "Success", "Content Download",
    "Download succeeded.", "id"])


def test_real_reporting_events_format_is_parsed_structurally():
    parser = WUParser("unused")
    fail, ok = parser.parse_line(_FAIL), parser.parse_line(_OK)
    assert fail.level == "Error" and ok.level == "Info"
    assert fail.source == "Content Install"            # not the GUID
    assert fail.raw["hresult"] == 0x800F0820 and fail.raw["event_name"] == "AGENT_INSTALLING_FAILED"
    assert ok.message.startswith("Update;Scan: Download succeeded")


def test_failing_updates_are_named_with_translated_codes():
    parser = WUParser("unused")
    entries = [parser.parse_line(_FAIL), parser.parse_line(_FAIL), parser.parse_line(_OK)]
    (name, code, meaning, times), = wu_analysis.failures(entries)
    assert "KB5124010" in name and code == 0x800F0820 and times == 2
    assert "pending" in meaning
    text = wu_analysis.summary_text(entries)
    assert "KB5124010" in text and "0x800F0820" in text and "2 failed" in text
    assert "0x800F0820" in wu_analysis.detail_html(entries[0])
    assert wu_analysis.detail_html(entries[2]) == ""


def test_an_unknown_code_is_admitted_not_invented():
    assert wu_analysis.explain_code(0x80999999) == ""
    entry = _e(datetime.now(), "x", "Error", "failed 0x80999999", {"hresult": 0x80999999})
    assert "No documented meaning" in wu_analysis.detail_html(entry)


def test_old_format_lines_still_parse():
    entry = WUParser("unused").parse_line("2026-03-25 10:00:01\tAgent\tWindowsUpdate\tFailure\tKB67890 failed")
    assert entry is not None and entry.level == "Error"


# -- CBS / DISM ------------------------------------------------------------
def _lines(*msgs, level="Info"):
    t = datetime(2026, 9, 26, 9, 0)
    return [_e(t + timedelta(seconds=i), "CBS", level, m) for i, m in enumerate(msgs)]


def test_clean_log_is_positively_clean():
    text = ss.summarize(_lines("Starting TrustedInstaller", "Lock: added 0x00000000"), "cbs")
    assert "No component-store corruption markers" in text and "No failing HRESULTs" in text


def test_corruption_and_codes_surface_regardless_of_the_logged_level():
    entries = _lines(
        "Failed to internally open package. [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]",
        "Failed to create open package. [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]",
        "[SR] Cannot repair member file [l:24]'x.dll' of Microsoft-Windows-Foo",
        "[SR] Repairing 1 components",
    )
    text = ss.summarize(entries, "cbs")
    assert "cannot repair x1" in text
    assert "0x800F0805 x2" in text and "CBS_E_INVALID_PACKAGE" in text
    assert "repair activity: 1" in text


def test_dism_summary_lists_recent_commands():
    entries = _lines('DISM.EXE: Executing command line: "C:\\WINDOWS\\system32\\Dism.exe" /Online /Cleanup-Image /RestoreHealth')
    assert "/Online /Cleanup-Image /RestoreHealth" in ss.summarize(entries, "dism")


def test_empty_log_is_stated():
    assert "no readable lines" in ss.summarize([], "cbs")


def test_servicing_detail_translates_codes_in_the_line():
    entry = _lines("failed [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]")[0]
    assert "CBS_E_INVALID_PACKAGE" in ss.detail_html(entry)
    assert ss.detail_html(_lines("all fine")[0]) == ""
