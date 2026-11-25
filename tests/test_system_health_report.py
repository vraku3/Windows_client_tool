"""Tests for System Health Audit Report Generator."""
from __future__ import annotations

from types import SimpleNamespace
from modules.system_health.report_generator import (
    render_health_report_html,
    render_health_report_markdown,
    write_health_report,
)


def test_render_health_report_html():
    findings = [
        SimpleNamespace(id="err1", severity="error", title="Corrupt Servicing",
                        detail="WinSxS pending XML locked", evidence="file exists at WinSxS", jump="System Health"),
        SimpleNamespace(id="warn1", severity="warning", title="Orphaned Task",
                        detail="Target exe missing", evidence="C:\\bin\\old.exe not found", jump="Scheduled Tasks"),
        SimpleNamespace(id="info1", severity="info", title="Hosts file clean",
                        detail="No hijacking entries", evidence="", jump=""),
    ]
    html = render_health_report_html(findings, {"hostname": "TEST-HOST"})
    assert "System Health Audit Report" in html
    assert "TEST-HOST" in html
    assert "Corrupt Servicing" in html
    assert "Orphaned Task" in html
    assert "Hosts file clean" in html
    assert "Remediate in: System Health" in html
    assert "Remediate in: Scheduled Tasks" in html


def test_render_health_report_html_empty():
    html = render_health_report_html([])
    assert "All System Health Checks Passed" in html


def test_render_health_report_markdown():
    findings = [
        SimpleNamespace(id="warn1", severity="warning", title="Driver Blocklist Outdated",
                        detail="Blocklist not refreshed", evidence="Reg value is 0", jump="Security Dashboard"),
    ]
    md = render_health_report_markdown(findings, {"hostname": "PROD-PC"})
    assert "# System Health Audit Report: PROD-PC" in md
    assert "**[WARNING] Driver Blocklist Outdated**" in md
    assert "Remediation target: Security Dashboard" in md


def test_write_health_report(tmp_path):
    path = write_health_report(str(tmp_path), "<html>Health Test</html>")
    assert path is not None
    assert "health-audit-" in path
    from pathlib import Path
    assert Path(path).read_text(encoding="utf-8") == "<html>Health Test</html>"
