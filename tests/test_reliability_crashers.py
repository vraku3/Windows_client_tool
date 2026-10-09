"""Reliability: which applications crash, where, and with what exception."""
from datetime import datetime

import pytest

from core.types import LogEntry
from modules.reliability import reliability_analysis as ra


def _crash(app, module, code, when=datetime(2026, 10, 6, 21, 30)):
    # The real Application Error 1000 layout, from this machine's records.
    msg = (f"Faulting application name: {app}, version: 26.173.906.8, time stamp: 0x3334898b\n"
           f"Faulting module name: {module}, version: 10.0.26100.9444, time stamp: 0x2bbefd73\n"
           f"Exception code: {code}\nFault offset: 0x00000000000a527e\n"
           f"Faulting process id: 0x2770")
    return LogEntry(timestamp=when, source="Application Error", level="Error", message=msg, raw={})


def _hang(app, when=datetime(2026, 10, 5, 9, 0)):
    msg = f"The program {app} version 10.0.26100.1 stopped interacting with Windows and was closed."
    return LogEntry(timestamp=when, source="Application Hang", level="Error", message=msg, raw={})


def test_a_crash_record_parses_into_app_module_and_code():
    assert ra.parse_crash(_crash("OneDrive.exe", "ucrtbase.dll", "0xc0000409")) == (
        "OneDrive.exe", "ucrtbase.dll", 0xC0000409)
    assert ra.parse_crash(_hang("TabTip.exe")) == ("TabTip.exe", "(hang)", None)


def test_crashers_are_grouped_by_app_and_ranked():
    entries = ([_crash("SnippingTool.exe", "SnippingTool.exe", "0xc000027b")] * 4
               + [_crash("python.exe", "Qt6Core.dll", "0xc0000409")] * 2
               + [_crash("python.exe", "python312.dll", "0xc0000005")]
               + [_hang("TabTip.exe")])
    top = ra.top_crashers(entries)
    assert [(c.app, c.count) for c in top] == [("SnippingTool.exe", 4), ("python.exe", 3), ("TabTip.exe", 1)]
    python = top[1]
    assert python.module == "Qt6Core.dll" and python.code == 0xC0000409   # the most common of each


def test_app_names_group_case_insensitively():
    entries = [_crash("ONENOTE.EXE", "x.dll", "0x01483052"), _crash("onenote.exe", "x.dll", "0x01483052")]
    assert [c.count for c in ra.top_crashers(entries)] == [2]


@pytest.mark.parametrize("code,word", [(0xC000027B, "Store/WinRT"), (0xC0000409, "fail-fast"),
                                       (0xC0000005, "access violation"), (0xE0434352, ".NET")])
def test_known_exception_codes_are_named(code, word):
    assert word in ra.explain_exception(code)


def test_an_unknown_code_is_bare_hex_not_a_guess():
    assert ra.explain_exception(0x01483052) == "0x01483052"


def test_the_summary_names_the_apps_not_just_application_error():
    entries = [_crash("SnippingTool.exe", "SnippingTool.exe", "0xc000027b")] * 2
    text = ra.summary_text([], entries)
    assert "Most crashes: SnippingTool.exe x2 (in SnippingTool.exe, 0xC000027B" in text


def test_a_crash_detail_shows_the_decoded_exception():
    html = ra.detail_html(_crash("OneDrive.exe", "ucrtbase.dll", "0xc0000409"), [])
    assert "0xC0000409 fail-fast" in html and "ucrtbase.dll" in html


def test_a_non_crash_record_has_no_crash_detail():
    entry = LogEntry(timestamp=datetime(2026, 10, 1), source="MsiInstaller", level="Info",
                     message="Product: X -- Installation completed successfully.", raw={})
    assert ra.parse_crash(entry) is None
    assert ra.detail_html(entry, []) == ""
