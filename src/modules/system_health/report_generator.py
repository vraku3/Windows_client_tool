"""Self-contained HTML and Markdown System Health Audit Report generator (Qt-free).

Produces a comprehensive diagnostic health audit report covering all executed
checks (servicing, orphaned tasks/services, NVMe wear, crash dumps, hosts, WinRE,
latency, etc.).

Styling matches the app's dark theme and report_sections conventions.
"""
from __future__ import annotations

import html
import logging
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from core.semantic_colors import CHROME_PALETTES, SEMANTIC_PALETTES

logger = logging.getLogger(__name__)


def _chrome(role: str, theme: str = "dark") -> str:
    return CHROME_PALETTES[theme][role]


def _semantic(role: str, theme: str = "dark") -> str:
    return SEMANTIC_PALETTES[theme][role]


HEALTH_REPORT_CSS = f"""
  body {{
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    background: {_chrome('surface_inactive')};
    color: {_chrome('text')};
    margin: 0;
    padding: 24px 32px;
    line-height: 1.5;
  }}
  h1 {{ margin: 0 0 6px 0; font-size: 24px; color: {_chrome('text')}; }}
  h2 {{ margin: 28px 0 12px 0; font-size: 18px; color: {_semantic('info')}; border-bottom: 1px solid {_chrome('outline')}; padding-bottom: 6px; }}
  .meta {{ color: {_chrome('text_muted')}; font-size: 13px; margin-bottom: 24px; }}
  .summary-bar {{
    display: flex;
    gap: 16px;
    margin-bottom: 24px;
    flex-wrap: wrap;
  }}
  .stat-card {{
    background: {_chrome('surface')};
    border: 1px solid {_chrome('outline')};
    border-radius: 6px;
    padding: 12px 20px;
    min-width: 140px;
  }}
  .stat-card .val {{ font-size: 22px; font-weight: bold; margin-bottom: 2px; }}
  .stat-card .val.err {{ color: {_semantic('error')}; }}
  .stat-card .val.warn {{ color: {_semantic('warning')}; }}
  .stat-card .val.info {{ color: {_semantic('info')}; }}
  .stat-card .val.ok {{ color: {_semantic('success')}; }}
  .stat-card .lbl {{ font-size: 12px; color: {_chrome('text_muted')}; text-transform: uppercase; }}
  .finding-card {{
    background: {_chrome('surface')};
    border: 1px solid {_chrome('outline')};
    border-left: 4px solid {_chrome('outline')};
    border-radius: 4px;
    padding: 12px 16px;
    margin-bottom: 12px;
  }}
  .finding-card.error {{ border-left-color: {_semantic('error')}; }}
  .finding-card.warning {{ border-left-color: {_semantic('warning')}; }}
  .finding-card.info {{ border-left-color: {_semantic('info')}; }}
  .finding-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
  .badge {{
    font-size: 11px;
    font-weight: bold;
    padding: 2px 8px;
    border-radius: 3px;
    letter-spacing: 0.5px;
  }}
  .badge.error {{ background: rgba(244, 71, 71, 0.2); color: {_semantic('error')}; }}
  .badge.warning {{ background: rgba(229, 192, 123, 0.2); color: {_semantic('warning')}; }}
  .badge.info {{ background: rgba(79, 195, 247, 0.2); color: {_semantic('info')}; }}
  .finding-title {{ font-size: 15px; font-weight: 600; color: {_chrome('text')}; flex: 1; }}
  .jump-pill {{
    font-size: 11px;
    background: {_chrome('surface_selected')};
    color: {_semantic('info')};
    padding: 2px 10px;
    border-radius: 12px;
  }}
  .finding-detail {{ font-size: 13.5px; color: {_chrome('text')}; margin-bottom: 4px; }}
  .evidence {{
    background: {_chrome('surface_inactive')};
    border: 1px solid {_chrome('outline')};
    padding: 8px 12px;
    border-radius: 4px;
    font-family: Consolas, monospace;
    font-size: 12px;
    color: {_chrome('text_muted')};
    overflow-x: auto;
    white-space: pre-wrap;
    margin-top: 8px;
  }}
  .all-clean-banner {{
    background: rgba(78, 201, 176, 0.1);
    border: 1px solid {_semantic('success')};
    border-radius: 6px;
    padding: 24px;
    text-align: center;
    margin: 24px 0;
  }}
  .all-clean-banner h3 {{ color: {_semantic('success')}; margin: 0 0 8px 0; font-size: 18px; }}
  .all-clean-banner p {{ color: {_chrome('text')}; margin: 0; font-size: 14px; }}
  footer {{ margin-top: 40px; font-size: 11px; color: {_chrome('text_muted')}; border-top: 1px solid {_chrome('outline')}; padding-top: 12px; }}
"""


def _esc(v) -> str:
    return html.escape(str(v) if v is not None else "")


def render_health_report_html(findings: Sequence, metadata: Optional[dict] = None) -> str:
    meta = metadata or {}
    hostname = meta.get("hostname") or socket.gethostname()
    generated = meta.get("generated") or datetime.now().strftime("%A, %d %B %Y, %H:%M")
    os_name = meta.get("os_name", "Windows")

    errors = [f for f in findings if getattr(f, "severity", "") in ("error", "critical")]
    warnings = [f for f in findings if getattr(f, "severity", "") == "warning"]
    infos = [f for f in findings if getattr(f, "severity", "") == "info"]

    def _render_group(items, group_title: str, badge_cls: str) -> str:
        if not items:
            return ""
        rows = []
        for f in items:
            title = _esc(getattr(f, "title", ""))
            detail = _esc(getattr(f, "detail", ""))
            evidence = _esc(getattr(f, "evidence", "") or "")
            jump = _esc(getattr(f, "jump", "") or "")
            jump_html = f'<span class="jump-pill">Remediate in: {jump}</span>' if jump else ""
            evidence_html = f'<pre class="evidence">{evidence}</pre>' if evidence else ""
            rows.append(
                f'<div class="finding-card {badge_cls}">'
                f'  <div class="finding-header">'
                f'    <span class="badge {badge_cls}">{badge_cls.upper()}</span>'
                f'    <span class="finding-title">{title}</span>'
                f'    {jump_html}'
                f'  </div>'
                f'  <div class="finding-detail">{detail}</div>'
                f'  {evidence_html}'
                f'</div>'
            )
        return f'<h2>{group_title} ({len(items)})</h2>' + "\n".join(rows)

    sections_html = (
        _render_group(errors, "Critical & Error Issues", "error")
        + _render_group(warnings, "Warnings & Needs Attention", "warning")
        + _render_group(infos, "Informational & Optimization Opportunities", "info")
    )

    if not findings:
        sections_html = (
            '<div class="all-clean-banner">'
            '  <h3>✓ All System Health Checks Passed</h3>'
            '  <p>No orphaned services, scheduled tasks, servicing corruption, driver blocklist issues, '
            'or storage degradation were detected on this machine.</p>'
            '</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>System Health Audit - {_esc(hostname)}</title>
<style>
{HEALTH_REPORT_CSS}
</style>
</head>
<body>
  <h1>System Health Audit Report</h1>
  <div class="meta">Host: <b>{_esc(hostname)}</b> &nbsp;|&nbsp; Generated: <b>{_esc(generated)}</b> &nbsp;|&nbsp; Platform: <b>{_esc(os_name)}</b></div>

  <div class="summary-bar">
    <div class="stat-card">
      <div class="val {'ok' if not errors and not warnings else 'err'}">{len(findings)}</div>
      <div class="lbl">Total Findings</div>
    </div>
    <div class="stat-card">
      <div class="val {'err' if errors else 'ok'}">{len(errors)}</div>
      <div class="lbl">Errors</div>
    </div>
    <div class="stat-card">
      <div class="val {'warn' if warnings else 'ok'}">{len(warnings)}</div>
      <div class="lbl">Warnings</div>
    </div>
    <div class="stat-card">
      <div class="val info">{len(infos)}</div>
      <div class="lbl">Informational</div>
    </div>
  </div>

  {sections_html}

  <footer>Generated by Windows Client Tool &bull; Read-only non-destructive audit</footer>
</body>
</html>
"""


def render_health_report_markdown(findings: Sequence, metadata: Optional[dict] = None) -> str:
    meta = metadata or {}
    hostname = meta.get("hostname") or socket.gethostname()
    generated = meta.get("generated") or datetime.now().strftime("%Y-%m-%d %H:%M")

    errors = [f for f in findings if getattr(f, "severity", "") in ("error", "critical")]
    warnings = [f for f in findings if getattr(f, "severity", "") == "warning"]
    infos = [f for f in findings if getattr(f, "severity", "") == "info"]

    lines = [
        f"# System Health Audit Report: {hostname}",
        f"*Generated: {generated}*",
        "",
        f"**Summary:** {len(findings)} finding(s) ({len(errors)} error(s), {len(warnings)} warning(s), {len(infos)} info(s))",
        "",
    ]

    if not findings:
        lines.append("✓ **All checks passed cleanly. No issues detected.**\n")
        return "\n".join(lines)

    for group_name, items in [("Critical & Errors", errors), ("Warnings", warnings), ("Informational", infos)]:
        if not items:
            continue
        lines.append(f"## {group_name}")
        lines.append("")
        for f in items:
            sev = getattr(f, "severity", "").upper()
            title = getattr(f, "title", "")
            detail = getattr(f, "detail", "")
            evidence = getattr(f, "evidence", "")
            jump = getattr(f, "jump", "")
            lines.append(f"- **[{sev}] {title}**")
            lines.append(f"  {detail}")
            if jump:
                lines.append(f"  *Remediation target: {jump}*")
            if evidence:
                lines.append("  ```")
                lines.append(f"  {evidence.strip()}")
                lines.append("  ```")
            lines.append("")

    return "\n".join(lines)


def write_health_report(app_data_dir: str, html_text: str) -> Optional[str]:
    """Write health report HTML to disk in reports folder and return absolute path."""
    if not app_data_dir:
        return None
    try:
        report_dir = Path(app_data_dir) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = report_dir / f"health-audit-{ts}.html"
        target.write_text(html_text, encoding="utf-8")
        return str(target)
    except OSError:
        logger.warning("Could not write system health audit report", exc_info=True)
        return None
