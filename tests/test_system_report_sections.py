"""System Report sections: containment of failures, rendering, Markdown."""
import pytest

from modules.system_report import report_sections as rs


def _ok():
    return ([rs.Section("Alpha", ["A", "B"], [["1", "x|y"]])],
            [rs.ReportFinding("Disks", "warning", "Hot", "70 C")])


def _boom():
    raise RuntimeError("wmi exploded")


def test_a_failing_builder_costs_only_its_own_sections():
    sections, findings = rs.collect_sections([_ok, _boom, _ok])
    titles = [s.title for s in sections]
    assert titles == ["Alpha", "_boom", "Alpha"]
    assert sections[1].error == "wmi exploded"
    assert len(findings) == 2


def test_unreadable_section_is_not_rendered_as_empty():
    md = rs.sections_markdown([rs.Section("Disk health", error="PowerShell could not be run")])
    assert "Could not be read: PowerShell could not be run" in md
    html = rs.sections_html([rs.Section("Disk health", error="nope")])
    assert "Could not be read: nope" in html and "None." not in html


def test_genuinely_empty_section_says_none():
    assert "None." in rs.sections_markdown([rs.Section("Monitors", ["A"], [])])
    assert "None." in rs.sections_html([rs.Section("Monitors", ["A"], [])])


def test_markdown_escapes_pipes_and_lists_headers():
    md = rs.sections_markdown(_ok()[0])
    assert "| A | B |" in md and "x\\|y" in md


def test_html_escapes_values():
    html = rs.sections_html([rs.Section("T", ["H"], [["<script>alert(1)</script>"]])])
    assert "<script>" not in html and "&lt;script&gt;" in html
    f = rs.findings_html([rs.ReportFinding("A", "error", "<b>x</b>", "")])
    assert "<b>x</b>" not in f


def test_attention_lists_errors_before_warnings_and_skips_info():
    items = [rs.ReportFinding("A", "info", "i"), rs.ReportFinding("B", "warning", "w"),
             rs.ReportFinding("C", "error", "e")]
    md = rs.findings_markdown(items)
    assert md.index("e.") < md.index("w.") and "i." not in md
    assert "Nothing flagged" in rs.findings_markdown([rs.ReportFinding("A", "info", "i")])
    assert "Nothing flagged" in rs.findings_html([])


def test_build_markdown_is_a_full_ticket_body():
    data = {"hostname": "H", "generated": "2026-01-01 00:00:00", "os_name": "Windows 11",
            "architecture": "AMD64", "os": "10.0.1", "uptime_hours": 5, "cpu_name": "CPU",
            "cpu_cores": 4, "cpu_threads": 8, "ram_total_gb": 16, "ram_percent": 50,
            "antivirus": "Defender", "firewall": "Win", "software_count": 10,
            "findings": _ok()[1], "sections": _ok()[0]}
    md = rs.build_markdown(data)
    assert md.startswith("## System report: H")
    assert "[warning] Disks: Hot" in md and "#### Alpha" in md and "| CPU | CPU (4 cores / 8 threads) |" in md


def test_firmware_rows_flag_placeholder_serial_and_unknowns():
    from modules.hardware_inventory import asset_parse as ap
    fw = ap.FirmwareInfo(firmware_mode="UEFI", secure_boot=None, tpm_present=None, tpm_reason="needs admin")
    rows = dict(ap.firmware_rows(fw, None, "No battery installed.", [("Serial Number", "Default string")]))
    assert rows["Secure Boot"].startswith("Unknown")
    assert rows["TPM"] == "needs admin"
    assert "not filled in" in rows["Serial Number"]
    assert rows["Battery"] == "No battery installed."


def test_battery_rows_when_present():
    from modules.hardware_inventory import asset_parse as ap
    rows = dict(ap.firmware_rows(ap.FirmwareInfo(), ap.BatteryHealth(50000, 40000, 312), "", []))
    assert rows["Battery health"].startswith("80.0%") and rows["Battery cycle count"] == "312"


def test_real_machine_sections_all_have_a_title_and_a_state():
    import pythoncom
    pythoncom.CoInitialize()
    try:
        sections, findings = rs.collect_sections()
    finally:
        pythoncom.CoUninitialize()
    titles = {s.title for s in sections}
    assert {"Asset record", "Disk health", "System Restore", "Software"} <= titles
    for s in sections:
        assert s.error or s.rows or s.title in {"Monitors", "Volumes", "System Restore"}
    for f in findings:
        assert f.severity in ("error", "warning", "info")


# ---- stability and health sections ------------------------------------------

def _incident(cause, summary, when="2026-10-04 13:49"):
    from datetime import datetime
    from core.stability import Incident
    return Incident(datetime.strptime(when, "%Y-%m-%d %H:%M"), cause, summary, event_ids=[41, 6008])


def test_stability_lists_incidents_and_summarises_them_in_one_finding():
    from core import stability as st
    incidents = [_incident(st.POWER_LOSS, "Lost power"),
                 _incident(st.POWER_BUTTON, "Forced off", "2026-09-13 17:23"),
                 _incident(st.POWER_LOSS, "Lost power", "2026-09-23 11:34")]
    sections, findings = rs.stability_sections(reader=lambda days: (incidents, ""))
    assert len(sections[0].rows) == 3
    (finding,) = findings
    assert finding.severity == "warning"
    assert finding.title == "3 unexpected shutdown(s) in 30 days"
    assert finding.detail.startswith("2 power loss/hard reset, 1 power button held")


def test_a_blue_screen_makes_the_stability_finding_an_error():
    from core import stability as st
    _s, (finding,) = rs.stability_sections(
        reader=lambda days: ([_incident(st.BUGCHECK, "Blue screen")], ""))
    assert finding.severity == "error"


def test_no_incidents_says_so_once_and_flags_nothing():
    sections, findings = rs.stability_sections(reader=lambda days: ([], ""))
    assert findings == []
    md = rs.sections_markdown(sections)
    assert "No unexpected shutdowns" in md and "None." not in md


def test_an_unreadable_crash_history_is_not_reported_as_stable():
    sections, findings = rs.stability_sections(reader=lambda days: (None, "Access is denied."))
    assert sections[0].error == "Access is denied."
    assert findings[0].title == "Crash history could not be read"


def test_health_section_carries_the_system_health_findings_verbatim():
    from modules.system_health.findings import Finding
    found = [Finding("reboot", "A restart is pending", "Windows Update", "warning"),
             Finding("disk", "1342 GB free on C:", "", "info")]
    sections, findings = rs.health_sections(reader=lambda: found)
    assert [r[1] for r in sections[0].rows] == ["A restart is pending", "1342 GB free on C:"]
    assert [f.severity for f in rs._attention(findings)] == ["warning"]
