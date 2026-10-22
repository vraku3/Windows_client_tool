"""Self-contained HTML maintenance report — Windows Update / winget / Store /
cleanup results plus run history, in one file. Styling matches the app's
existing system_report/report_module.py conventions (dark, #1e1e1e / #3498db)
so it looks consistent with the rest of the app rather than importing a new
palette.
"""
import html
import logging
import os
import socket
from core.formatting import format_size as _fmt_size
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)


def _esc(v) -> str:
    return html.escape(str(v) if v is not None else "")


def render_update_report_html(data: dict, history: List[dict]) -> str:
    """data keys (all optional): wu_results, wu_held, winget_results, store_triggered,
    cleanup_freed, cleanup_deleted, dism_output — see stage_runners.py outputs."""
    hostname = data.get("hostname") or socket.gethostname()
    generated = data.get("generated") or datetime.now().strftime("%A, %d %B %Y, %H:%M")

    wu_results = data.get("wu_results") or []
    #: AMD display drivers the hold kept back: listed as held, never installed.
    wu_held = data.get("wu_held") or []
    winget_results = data.get("winget_results") or []
    store_triggered = data.get("store_triggered")
    cleanup_freed = data.get("cleanup_freed", 0)
    cleanup_deleted = data.get("cleanup_deleted", 0)
    dism_output = data.get("dism_output")
    pending_restart = data.get("wu_pending_restart", 0)

    wu_rows = "".join(
        f"<tr><td>{_esc(r.get('title'))}</td><td>{_esc(r.get('kb'))}</td>"
        f"<td class=\"{'ok' if r.get('success') else 'warn'}\">{_esc(r.get('message'))}</td></tr>"
        for r in wu_results
    ) + "".join(
        f"<tr><td>{_esc(r.get('title'))}</td><td>{_esc(r.get('kb'))}</td>"
        f"<td class=\"held\">{_esc(r.get('message') or 'held')}</td></tr>"
        for r in wu_held
    ) or "<tr><td colspan=3>No Windows Update installs this run.</td></tr>"

    wg_rows = "".join(
        f"<tr><td>{_esc(r.get('name'))}</td><td>{_esc(r.get('id'))}</td>"
        f"<td>{_esc(r.get('before'))}</td><td>{_esc(r.get('after'))}</td>"
        f"<td class=\"{'ok' if r.get('confirmed') else 'warn'}\">"
        f"{'CONFIRMED' if r.get('confirmed') else _esc(r.get('state') or 'UNCHANGED')}</td></tr>"
        for r in winget_results
    ) or "<tr><td colspan=5>No winget updates this run.</td></tr>"

    hist_rows = "".join(
        f"<tr><td>{_esc(e.get('ts'))}</td><td>{_esc(_fmt_size(e.get('freed', 0)))}</td>"
        f"<td>{_esc(e.get('updates', 0))}</td><td>{_esc(e.get('pending_restart', 0))}</td></tr>"
        for e in list(history)[-20:][::-1]
    ) or "<tr><td colspan=4>No prior runs recorded.</td></tr>"

    held_suffix = f", {len(wu_held)} held" if wu_held else ""
    cards = f"""
      <div class="card"><div class="k">Windows Update</div>
        <div class="v {'ok' if all(r.get('success') for r in wu_results) or not wu_results else 'warn'}">
          {len([r for r in wu_results if r.get('success')])}/{len(wu_results)} installed{held_suffix}
          {f'; Restart required to finish {pending_restart} update(s)' if pending_restart else ''}</div></div>
      <div class="card"><div class="k">Winget</div><div class="v">{sum(bool(r.get('confirmed')) for r in winget_results)} confirmed updated</div></div>
      <div class="card"><div class="k">Microsoft Store</div>
        <div class="v">{'scan triggered' if store_triggered else 'not run'}</div></div>
      <div class="card"><div class="k">Cleanup freed</div>
        <div class="v ok">{_esc(_fmt_size(cleanup_freed))} ({_esc(cleanup_deleted)} items)</div></div>
    """

    dism_section = ""
    if dism_output:
        dism_section = f"""
        <h2>DISM Component Store Cleanup</h2>
        <pre>{_esc(dism_output)}</pre>
        """

    cleanup_rows = "".join(
        f"<tr><td>{_esc(area.get('area'))}</td><td>{_esc(_fmt_size(area.get('freed', 0)))}</td>"
        f"<td>{_esc(area.get('left'))}</td></tr>" for area in data.get("cleanup_areas", []))
    cleanup_section = (f"<h2>Cleanup by area</h2><p>{_esc(data.get('cleanup_left', ''))}</p>"
                       "<table><tr><th>Area</th><th>Cleaned</th><th>Left and why</th></tr>"
                       f"{cleanup_rows}</table>") if cleanup_rows else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Maintenance Report — {_esc(hostname)}</title>
<style>
  body {{font-family:Segoe UI,Arial,sans-serif;background:#1e1e1e;color:#ccc;margin:20px;}}
  h1 {{color:#fff;margin-bottom:4px;}} h2 {{color:#3498db;border-bottom:1px solid #333;padding-bottom:4px;margin-top:28px;}}
  .sub {{color:#888;margin-bottom:20px;}}
  .cards {{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:8px;}}
  .card {{background:#252526;border:1px solid #3c3c3c;border-radius:8px;padding:12px 18px;min-width:180px;}}
  .card .k {{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.04em;}}
  .card .v {{font-size:17px;font-weight:600;margin-top:4px;}}
  table {{border-collapse:collapse;width:100%;margin-bottom:16px;}}
  th {{background:#2c2c2c;color:#aaa;text-align:left;padding:6px 10px;}}
  td {{padding:5px 10px;border-bottom:1px solid #2c2c2c;}}
  .ok {{color:#4ec9b0;}} .warn {{color:#e5c07b;}} .held {{opacity:.7;}}
  pre {{background:#151515;border:1px solid #2c2c2c;border-radius:6px;padding:10px;overflow-x:auto;white-space:pre-wrap;}}
</style>
</head>
<body>
<h1>&#x1f6e0;&#xfe0f; Maintenance Report</h1>
<div class="sub">Generated: {_esc(generated)} &nbsp;|&nbsp; Host: <b>{_esc(hostname)}</b></div>
<div class="cards">{cards}</div>

<h2>Windows Update</h2>
<table><tr><th>Title</th><th>KB</th><th>Result</th></tr>{wu_rows}</table>

<h2>Winget packages</h2>
<table><tr><th>Package</th><th>Id</th><th>Before</th><th>After</th><th>Result</th></tr>{wg_rows}</table>
{dism_section}
{cleanup_section}
<h2>Run history (last 20)</h2>
<table><tr><th>Date</th><th>Freed</th><th>Updates installed</th><th>Pending restart</th></tr>{hist_rows}</table>
</body></html>"""


def write_report(app_data_dir: str, html_text: str) -> Optional[str]:
    """Write the report to {app_data_dir}/updates/report-<timestamp>.html and
    return the full path — or None (logging a warning) if there isn't enough
    free space to safely write it, rather than failing partway through."""
    updates_dir = os.path.join(app_data_dir, "updates")
    os.makedirs(updates_dir, exist_ok=True)

    from core.disk_space import check_report_preflight
    reason = check_report_preflight(updates_dir)
    if reason:
        logger.warning("Skipping report write: %s", reason)
        return None

    filename = f"report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    path = os.path.join(updates_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return path
