"""Reliability, Windows Update and CBS/DISM analysis (Qt-free)."""
from datetime import datetime, timedelta

import pytest

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


def test_sparkline_tooltip_states_range_and_lowest_point():
    t0 = datetime(2026, 9, 4, 11, 0)
    metrics = [
        ra.Metric(t0, 10.0),
        ra.Metric(t0 + timedelta(hours=13, minutes=13), 1.4),  # the lowest
        ra.Metric(t0 + timedelta(days=25), 4.4),
    ]
    text = ra.sparkline_tooltip(metrics)
    assert "2026-09-04" in text and "2026-09-29" in text
    assert "Now: 4.4 / 10" in text
    assert "Lowest: 1.4 / 10" in text


def test_sparkline_tooltip_without_history_says_so():
    assert ra.sparkline_tooltip([]) == "No stability index history was returned."


def test_sparkline_tooltip_sorts_out_of_order_metrics():
    # set_metrics on the chart widget sorts before calling this, but the
    # function itself must not trust the caller either.
    t0 = datetime(2026, 9, 20, 0, 0)
    metrics = [ra.Metric(t0 + timedelta(hours=5), 3.0), ra.Metric(t0, 8.0)]
    text = ra.sparkline_tooltip(metrics)
    assert "Now: 3.0 / 10" in text


def test_real_reliability_stability_metrics_feed_the_sparkline():
    """Real machine: Win32_ReliabilityStabilityMetrics is readable unelevated
    and gives at least one usable (start, index 0..10) row here -- confirmed
    2026-09-29 with a live decline from a flat 10.0 on 2026-09-04 to ~4.4."""
    import pytest
    from modules.reliability.reliability_reader import read_stability_metrics

    try:
        metrics = read_stability_metrics()
    except Exception as exc:
        pytest.skip(f"WMI reliability metrics not readable in this environment: {exc}")
    if not metrics:
        pytest.skip("No stability index history on this machine yet.")
    assert all(0.0 <= m.index <= 10.0 for m in metrics)
    text = ra.sparkline_tooltip(metrics)
    assert "Now:" in text and "Lowest:" in text


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
    assert "KB5124010" in text and "0x800F0820" in text and "Still failing" in text and " x2" in text
    assert "0x800F0820" in wu_analysis.detail_html(entries[0])
    assert wu_analysis.detail_html(entries[2]) == ""


def _wu(when, event, uid, hresult, client, result, category, message):
    return WUParser("unused").parse_line("	".join([
        "{CB6DE3B4-CC97-4BD7-B8F2-2AF4A0DB4FCC}", f"2026-10-0{when}:000+0300", "1", event,
        "101", uid, "1", hresult, client, result, category, message, "id"]))


_STORE = "9NKSQGP7F2NH-5319275A.WhatsAppDesktop"
_IN_USE = "80073d02"


def _store_fail(when, uid):
    return _wu(when, "182 [AGENT_INSTALLING_FAILED]", uid, _IN_USE, "Acquisition;setup", "Failure",
               "Content Install", f"Installation Failure: Windows failed to install the following "
               f"update with error 0x80073d02: {_STORE}.")


def _store_ok(when, uid):
    return _wu(when, "183 [AGENT_INSTALLING_SUCCEEDED]", uid, "0", "Acquisition;setup", "Success",
               "Content Install", f"Installation Successful: Windows successfully installed the "
               f"following update: {_STORE}")


def test_a_failure_that_later_succeeded_is_not_still_failing():
    """Measured 2026-10-09: 8 of 14 failing updates here had since succeeded."""
    entries = [_store_fail("2 09:47:44", "{E33B}"), _store_ok("2 09:47:51", "{E33B}")]
    assert wu_analysis.failures(entries) == []
    assert wu_analysis.resolved_count(entries) == 1
    text = wu_analysis.summary_text(entries)
    assert "Nothing is failing now" in text and "1 update(s) failed and later succeeded" in text


def test_an_older_version_succeeding_does_not_hide_a_newer_one_failing():
    """WhatsApp here: the 09-29 version installed, the 10-08 one did not."""
    entries = [_store_ok("1 21:31:05", "{1FD3}"), _store_fail("8 12:41:01", "{A681}")]
    ((name, code, _m, _n),) = wu_analysis.failures(entries)
    assert name == _STORE and code == 0x80073D02


def test_a_nameless_download_failure_takes_its_name_from_the_update_id():
    """AGENT_DOWNLOAD_FAILED lines carry no title; 14 of 25 failures here were
    named after the client ('Update;MoUpdateOrchestratorDeviceScan-...')."""
    entries = [
        _wu("7 09:45:44", "161 [AGENT_DOWNLOAD_FAILED]", "{9E21}", "80244022",
            "Update;MoUpdateOrchestratorDeviceScan", "Failure", "Content Download", "Error: Download failed."),
        _wu("7 09:50:00", "182 [AGENT_INSTALLING_FAILED]", "{9E21}", _IN_USE, "Acquisition;setup", "Failure",
            "Content Install", "Installation Failure: Windows failed to install the following update "
            "with error 0x80073d02: 9NRZT3Q9R3DL-Microsoft.WindowsAppRuntime.2."),
    ]
    names = {f[0] for f in wu_analysis.failures(entries)}
    assert names == {"9NRZT3Q9R3DL-Microsoft.WindowsAppRuntime.2"}


def test_store_apps_in_use_are_summarised_apart_from_windows_failures():
    entries = [parser_line for parser_line in (WUParser("unused").parse_line(_FAIL),
                                               _store_fail("8 12:41:01", "{A681}"))]
    text = wu_analysis.summary_text(entries)
    assert "Still failing: 2026-09 Preview Update (KB5124010)" in text
    assert "1 Store app(s) not updated (1 because the app was running" in text
    assert "5319275A.WhatsAppDesktop" in text


def test_real_reporting_events_log_summary_is_well_formed():
    import os
    from modules.windows_update import wu_module
    if not os.path.exists(wu_module.WU_LOG_PATH):
        pytest.skip("no ReportingEvents.log on this machine")
    entries = WUParser(wu_module.WU_LOG_PATH).parse()
    text = wu_analysis.summary_text(entries)
    assert "Update;MoUpdateOrchestrator" not in text, "a client named as if it were an update"
    for name, _code, _meaning, _n in wu_analysis.failures(entries):
        assert not name.startswith("Update;"), name


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
        "Exec: Failed to stage package. [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]",
        "Failed to install package. [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]",
        "[SR] Cannot repair member file [l:24]'x.dll' of Microsoft-Windows-Foo",
        "[SR] Repairing 1 components",
    )
    text = ss.summarize(entries, "cbs")
    assert "cannot repair x1" in text
    assert "0x800F0805 x2" in text and "CBS_E_INVALID_PACKAGE" in text
    assert "repair activity: 1" in text


_PROBE = (
    "InternalOpenPackage failed for Package_for_KB3025096~31bf3856ad364e35~amd64~~6.4.1.0 "
    "[HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]",
    "Failed to internally open package. [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]",
    "Failed to create open package. [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]",
    "Failed to OpenPackage using worker session [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]",
)


def test_a_lookup_of_an_absent_package_is_routine_not_a_failing_code():
    """Real CBS.log 2026-10-09: 72 x 0x800F0805 were 18 such lookups, four lines each."""
    text = ss.summarize(_lines(*(_PROBE * 3)), "cbs")
    assert "No failing HRESULTs" in text
    assert "Routine: 3 lookup(s) of packages not installed here (Package_for_KB3025096 x3)" in text


def test_a_real_failure_beside_the_probes_still_counts():
    entries = _lines(*_PROBE, "Exec: Failed to stage package. [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]",
                     "Failed to unload offline registry [HRESULT = 0x80070005 - E_ACCESSDENIED]")
    codes = ss.failing_codes(entries)
    assert codes[0x800F0805] == 1 and codes[0x80070005] == 1


def test_dism_summary_lists_recent_commands():
    entries = _lines('DISM.EXE: Executing command line: "C:\\WINDOWS\\system32\\Dism.exe" /Online /Cleanup-Image /RestoreHealth')
    assert "/Online /Cleanup-Image /RestoreHealth" in ss.summarize(entries, "dism")


def test_empty_log_is_stated():
    assert "no readable lines" in ss.summarize([], "cbs")


def test_servicing_detail_translates_codes_in_the_line():
    entry = _lines("failed [HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]")[0]
    assert "CBS_E_INVALID_PACKAGE" in ss.detail_html(entry)
    assert ss.detail_html(_lines("all fine")[0]) == ""
