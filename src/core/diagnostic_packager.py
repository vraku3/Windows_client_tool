"""Diagnostic Incident Packaging and Reporting.

Creates an executive incident bundle (.zip archive) containing:
- report.html: Executive diagnostic HTML report (KPIs, top faulting sources, recommendations).
- events.json: Structured JSON of all entries across all diagnostic channels.
- events_sanitized.csv: PII-sanitized CSV log entries.
- system_summary.txt: Hostname, OS version, boot time, uptime, active user notes.

Designed to be Qt-free, headless, and fully unit-testable.
"""
from __future__ import annotations

import csv
import html
import io
import json
import logging
import os
import platform
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

# Try importing sanitize_log_text from UI if available, else fallback
try:
    from ui.log_table_widget import sanitize_log_text as _ui_sanitize_log_text
except Exception:
    _ui_sanitize_log_text = None

# Try importing match_suggested_fix from core.diagnostic_bridge
try:
    from core.diagnostic_bridge import match_suggested_fix
except Exception:
    match_suggested_fix = None


# ---------------------------------------------------------------------------
# PII Sanitization
# ---------------------------------------------------------------------------

_RE_USER_PATH = re.compile(r"[A-Za-z]:\\Users\\[a-zA-Z0-9_\-\.]+", re.IGNORECASE)
_RE_DOMAIN_USER = re.compile(r"\b[A-Za-z0-9\-_]{2,15}\\[A-Za-z0-9\-_.]+\b")
_RE_IPV4 = re.compile(r"\b(?!127\.0\.0\.1|0\.0\.0\.0)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_RE_SID = re.compile(r"S-1-5-21-\d+-\d+-\d+-\d+")
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_RE_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}(?:[0-9A-Fa-f]{2})\b")
_RE_CREDENTIALS = re.compile(r"(?i)\b(password|secret|bearer|token|apikey)\s*[=:]\s*([^\s,;]+)")


def sanitize_pii(text: str) -> str:
    """Mask IPv4 addresses, user paths, domain accounts, SIDs, emails, and credentials."""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)

    # Base sanitization matching ui.log_table_widget
    if _ui_sanitize_log_text is not None:
        text = _ui_sanitize_log_text(text)
    else:
        text = _RE_USER_PATH.sub(r"<USERPATH_REDACTED>", text)
        text = _RE_DOMAIN_USER.sub(r"<DOMAIN>\\<USER>", text)
        text = _RE_IPV4.sub(r"10.x.x.x", text)
        text = _RE_SID.sub(r"S-1-5-21-<REDACTED>", text)

    # Additional hardening
    text = _RE_EMAIL.sub("<EMAIL_REDACTED>", text)
    text = _RE_MAC.sub("<MAC_REDACTED>", text)
    text = _RE_CREDENTIALS.sub(r"\1=<REDACTED>", text)
    return text


# ---------------------------------------------------------------------------
# System Telemetry & Facts Collection
# ---------------------------------------------------------------------------

def get_system_info() -> Dict[str, str]:
    """Collect basic host and OS facts safely without throwing."""
    node = platform.node() or os.environ.get("COMPUTERNAME", "Unknown-Host")
    os_name = f"{platform.system()} {platform.release()}"
    os_version = platform.version()
    arch = platform.machine() or "x64"

    boot_time_str = "Unknown"
    uptime_str = "Unknown"

    try:
        import psutil
        boot_ts = psutil.boot_time()
        boot_dt = datetime.fromtimestamp(boot_ts)
        boot_time_str = boot_dt.strftime("%Y-%m-%d %H:%M:%S")
        uptime_delta = datetime.now() - boot_dt
        days = uptime_delta.days
        hours, rem = divmod(uptime_delta.seconds, 3600)
        minutes, _ = divmod(rem, 60)
        if days > 0:
            uptime_str = f"{days}d {hours}h {minutes}m"
        else:
            uptime_str = f"{hours}h {minutes}m"
    except Exception as e:
        logger.debug("psutil boot_time retrieval failed: %s", e)

    return {
        "hostname": node,
        "os_name": os_name,
        "os_version": os_version,
        "architecture": arch,
        "boot_time": boot_time_str,
        "uptime": uptime_str,
        "python_version": platform.python_version(),
    }


# ---------------------------------------------------------------------------
# Entry Normalization & Diagnostics Analysis
# ---------------------------------------------------------------------------

def _normalize_timestamp(val: Any) -> Tuple[Optional[datetime], str]:
    """Extract datetime and formatted string representation."""
    if isinstance(val, datetime):
        return val, val.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(val, (int, float)):
        try:
            dt = datetime.fromtimestamp(val)
            return dt, dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as ex:
            logger.debug("Failed parsing numeric timestamp '%s': %s", val, ex)
    if isinstance(val, str) and val:
        try:
            # Try ISO parse
            dt = datetime.fromisoformat(val)
            return dt, dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as ex:
            logger.debug("Failed parsing iso timestamp '%s': %s", val, ex)
        return None, str(val)
    return None, "—"


def _is_error_level(level: str) -> bool:
    lvl = (level or "").strip().lower()
    return any(k in lvl for k in ("error", "crit", "fatal", "fail", "bugcheck", "panic"))


def _is_warning_level(level: str) -> bool:
    lvl = (level or "").strip().lower()
    return any(k in lvl for k in ("warn", "caution", "alert")) and not _is_error_level(lvl)


def normalize_entry(entry: Any, default_channel: str = "Diagnostic") -> Dict[str, Any]:
    """Normalize any LogEntry, dict, or object into standard format."""
    if isinstance(entry, dict):
        raw_ts = entry.get("timestamp")
        source = str(entry.get("source") or "Unknown")
        level = str(entry.get("level") or "Info")
        message = str(entry.get("message") or "")
        channel = str(entry.get("channel") or default_channel)
        raw = entry.get("raw")
        if not isinstance(raw, dict):
            raw = {"detail": str(raw)} if raw is not None else {}
    else:
        raw_ts = getattr(entry, "timestamp", None)
        source = str(getattr(entry, "source", "Unknown") or "Unknown")
        level = str(getattr(entry, "level", "Info") or "Info")
        message = str(getattr(entry, "message", "") or "")
        channel = str(getattr(entry, "channel", default_channel) or default_channel)
        raw = getattr(entry, "raw", {})
        if not isinstance(raw, dict):
            raw = {"detail": str(raw)} if raw is not None else {}

    dt, ts_str = _normalize_timestamp(raw_ts)

    return {
        "channel": channel,
        "timestamp_dt": dt,
        "timestamp": ts_str,
        "level": level,
        "source": source,
        "message": message,
        "raw": raw,
    }


def analyze_diagnostics(readers_data: Dict[str, Sequence[Any]]) -> Dict[str, Any]:
    """Inspect all channel entries and compute metrics, fault counters, and remediations."""
    all_normalized: List[Dict[str, Any]] = []
    channel_stats: Dict[str, Dict[str, int]] = {}
    error_sources = Counter()
    warning_sources = Counter()
    all_sources = Counter()

    error_count = 0
    warning_count = 0
    info_count = 0

    remediation_set: Dict[str, Dict[str, str]] = {}

    for channel_name, entries in readers_data.items():
        ch_total = 0
        ch_errors = 0
        ch_warnings = 0

        for item in entries or []:
            norm = normalize_entry(item, default_channel=channel_name)
            all_normalized.append(norm)
            ch_total += 1
            src = norm["source"]
            msg = norm["message"]
            lvl = norm["level"]
            all_sources[src] += 1

            if _is_error_level(lvl):
                error_count += 1
                ch_errors += 1
                error_sources[src] += 1
                _extract_remediations(src, msg, remediation_set)
            elif _is_warning_level(lvl):
                warning_count += 1
                ch_warnings += 1
                warning_sources[src] += 1
            else:
                info_count += 1

        channel_stats[channel_name] = {
            "total": ch_total,
            "errors": ch_errors,
            "warnings": ch_warnings,
        }

    # If no issues were found, add healthy advice
    if not remediation_set and error_count == 0 and warning_count == 0:
        remediation_set["clean_health"] = {
            "title": "Routine Maintenance",
            "module": "System Health",
            "reason": "No critical faults detected across the analyzed channels. Keep system updated.",
            "severity": "Info",
        }

    # Sort entries newest first if possible
    def sort_key(e):
        return e["timestamp_dt"] or datetime.min

    all_normalized.sort(key=sort_key, reverse=True)

    top_faulting = []
    for src, cnt in error_sources.most_common(10):
        top_faulting.append({"source": src, "error_count": cnt, "warning_count": warning_sources[src]})

    return {
        "total_events": len(all_normalized),
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "entries": all_normalized,
        "channel_stats": channel_stats,
        "top_faulting": top_faulting,
        "remediations": list(remediation_set.values()),
    }


def _extract_remediations(src: str, msg: str, remediation_set: Dict[str, Dict[str, str]]) -> None:
    if match_suggested_fix:
        try:
            fix = match_suggested_fix(src, msg)
            if fix:
                key = f"{fix.title}:{fix.target_module}"
                if key not in remediation_set:
                    remediation_set[key] = {
                        "title": fix.title,
                        "module": fix.target_module,
                        "reason": fix.reason,
                        "severity": "High",
                    }
        except Exception as ex:
            logger.debug("Failed matching suggested fix for '%s': %s", src, ex)

    # Built-in pattern recognition
    comb = f"{src} {msg}".lower()
    if any(k in comb for k in ("0x800f081f", "cbs", "winsxs", "dism", "component store")):
        remediation_set["dism_repair"] = {
            "title": "Repair Windows Component Store (DISM)",
            "module": "System Health",
            "reason": "Corrupted servicing or CBS packages detected. Run ScanHealth & RestoreHealth.",
            "severity": "High",
        }
    if any(k in comb for k in ("disk", "ntfs", "bad block", "event 51", "event 153")):
        remediation_set["disk_repair"] = {
            "title": "Verify Disk Surface & SMART Telemetry",
            "module": "Disk Health",
            "reason": "Disk I/O or filesystem errors logged. Run CHKDSK and inspect drive wear.",
            "severity": "Critical",
        }
    if any(k in comb for k in ("nvlddmkm", "amdkmdag", "livekernelevent", "tdr", "display driver")):
        remediation_set["gpu_driver"] = {
            "title": "Update / Clean Install Display Driver",
            "module": "Driver Manager",
            "reason": "Graphics driver timeout or crash detected. Check vendor drivers.",
            "severity": "High",
        }
    if any(k in comb for k in ("kernel-power", "bugcheck", "event 41", "event 1001")):
        remediation_set["crash_dump"] = {
            "title": "Analyze Kernel Crash Dump & Stability",
            "module": "Crash Dumps",
            "reason": "Unexpected reboot or BSOD recorded. Check bugcheck parameters and minidumps.",
            "severity": "Critical",
        }


# ---------------------------------------------------------------------------
# Report Generators
# ---------------------------------------------------------------------------

def generate_system_summary_txt(system_info: Dict[str, str], analysis: Dict[str, Any], extra_notes: str = "") -> str:
    """Generate plaintext system and incident diagnostic summary."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = io.StringIO()
    out.write("======================================================================\n")
    out.write("WINDOWS CLIENT TOOL - DIAGNOSTIC INCIDENT SUMMARY\n")
    out.write("======================================================================\n")
    out.write(f"Generated At:   {now_str}\n")
    out.write(f"Computer Name:  {system_info.get('hostname', 'Unknown')}\n")
    out.write(f"OS Version:     {system_info.get('os_name', 'Unknown')} ({system_info.get('os_version', '')})\n")
    out.write(f"Architecture:   {system_info.get('architecture', 'x64')}\n")
    out.write(f"System Boot:    {system_info.get('boot_time', 'Unknown')}\n")
    out.write(f"System Uptime:  {system_info.get('uptime', 'Unknown')}\n")
    out.write("----------------------------------------------------------------------\n")
    out.write("INCIDENT NOTES / USER CONTEXT:\n")
    notes_clean = (extra_notes or "").strip()
    if notes_clean:
        out.write(f"{notes_clean}\n")
    else:
        out.write("(No incident notes provided)\n")
    out.write("----------------------------------------------------------------------\n")
    out.write("DIAGNOSTIC METRICS:\n")
    out.write(f"Total Analyzed Events: {analysis['total_events']}\n")
    out.write(f"Errors / Critical:     {analysis['error_count']}\n")
    out.write(f"Warnings:              {analysis['warning_count']}\n")
    out.write(f"Informational / Other: {analysis['info_count']}\n")
    out.write("----------------------------------------------------------------------\n")
    out.write("CHANNEL BREAKDOWN:\n")
    for ch_name, stats in analysis.get("channel_stats", {}).items():
        out.write(f" - {ch_name:24s}: {stats['total']:5d} events ({stats['errors']} errors, {stats['warnings']} warnings)\n")
    out.write("----------------------------------------------------------------------\n")
    out.write("TOP FAULTING SOURCES:\n")
    top = analysis.get("top_faulting", [])
    if top:
        for item in top:
            out.write(f" - {item['source']:30s}: {item['error_count']:3d} errors, {item['warning_count']:3d} warnings\n")
    else:
        out.write(" - None recorded.\n")
    out.write("----------------------------------------------------------------------\n")
    out.write("RECOMMENDED REMEDIATION:\n")
    rems = analysis.get("remediations", [])
    if rems:
        for idx, r in enumerate(rems, 1):
            out.write(f" {idx}. [{r.get('severity', 'Info')}] {r.get('title')} ({r.get('module')})\n")
            out.write(f"    Reason: {r.get('reason')}\n")
    else:
        out.write(" - Routine system maintenance.\n")
    out.write("======================================================================\n")
    return out.getvalue()


def _build_top_faulting_html(analysis: Dict[str, Any]) -> str:
    top_rows_html = []
    for item in analysis.get("top_faulting", []):
        top_rows_html.append(
            f"<tr><td><b>{html.escape(item['source'])}</b></td>"
            f"<td class='text-danger'>{item['error_count']}</td>"
            f"<td class='text-warning'>{item['warning_count']}</td></tr>"
        )
    if not top_rows_html:
        top_rows_html.append("<tr><td colspan='3' class='text-muted'>(No error-producing sources recorded)</td></tr>")
    return "".join(top_rows_html)


def _build_remediation_cards_html(analysis: Dict[str, Any]) -> str:
    remed_cards_html = []
    for r in analysis.get("remediations", []):
        sev = r.get("severity", "Info")
        sev_class = "badge-danger" if sev in ("Critical", "High") else "badge-warning" if sev == "Medium" else "badge-info"
        remed_cards_html.append(f"""
        <div class="remed-card">
            <div class="remed-head">
                <span class="badge {sev_class}">{html.escape(sev)}</span>
                <span class="remed-title">{html.escape(r.get('title', ''))}</span>
                <span class="remed-module">Target: <b>{html.escape(r.get('module', ''))}</b></span>
            </div>
            <div class="remed-body">{html.escape(r.get('reason', ''))}</div>
        </div>
        """)
    return "".join(remed_cards_html)


def _build_channel_stats_html(analysis: Dict[str, Any]) -> str:
    chan_rows_html = []
    for ch_name, stats in analysis.get("channel_stats", {}).items():
        st_badge = (
            "<span class='badge badge-danger'>Critical</span>" if stats["errors"] > 0
            else "<span class='badge badge-warning'>Warning</span>" if stats["warnings"] > 0
            else "<span class='badge badge-success'>Healthy</span>"
        )
        chan_rows_html.append(
            f"<tr><td><b>{html.escape(ch_name)}</b></td>"
            f"<td>{stats['total']}</td>"
            f"<td class='{'text-danger font-bold' if stats['errors'] > 0 else ''}'>{stats['errors']}</td>"
            f"<td class='{'text-warning font-bold' if stats['warnings'] > 0 else ''}'>{stats['warnings']}</td>"
            f"<td>{st_badge}</td></tr>"
        )
    return "".join(chan_rows_html)


def _build_incident_highlights_html(analysis: Dict[str, Any]) -> str:
    incident_rows_html = []
    incident_entries = [e for e in analysis.get("entries", []) if _is_error_level(e["level"]) or _is_warning_level(e["level"])]
    for e in incident_entries[:25]:
        is_err = _is_error_level(e["level"])
        lvl_class = "badge-danger" if is_err else "badge-warning"
        raw_detail = ""
        if e.get("raw"):
            raw_str = json.dumps(e["raw"], indent=2, ensure_ascii=False)
            if len(raw_str) > 10:
                raw_detail = f"<details class='log-raw'><summary>Raw Details</summary><pre>{html.escape(raw_str)}</pre></details>"

        incident_rows_html.append(
            f"<tr>"
            f"<td class='text-mono text-sm whitespace-nowrap'>{html.escape(e['timestamp'])}</td>"
            f"<td><span class='badge {lvl_class}'>{html.escape(e['level'])}</span></td>"
            f"<td><span class='badge badge-neutral'>{html.escape(e['channel'])}</span></td>"
            f"<td><b>{html.escape(e['source'])}</b></td>"
            f"<td><div class='log-msg'>{html.escape(e['message'])}</div>{raw_detail}</td>"
            f"</tr>"
        )

    if not incident_rows_html:
        incident_rows_html.append("<tr><td colspan='5' class='text-muted'>(No error or warning incidents recorded)</td></tr>")
    return "".join(incident_rows_html)


_REPORT_CSS = """
:root {
    --bg: __BG__;
    --surface: __SURFACE__;
    --surface-elevated: __SURFACE_ELEVATED__;
    --border: __BORDER__;
    --text: __TEXT__;
    --text-muted: __TEXT_MUTED__;
    --accent: __ACCENT__;
    --danger: __DANGER__;
    --danger-bg: rgba(239, 68, 68, 0.12);
    --danger-border: rgba(239, 68, 68, 0.35);
    --warning: __WARNING__;
    --warning-bg: rgba(245, 158, 11, 0.12);
    --warning-border: rgba(245, 158, 11, 0.35);
    --success: __SUCCESS__;
    --success-bg: rgba(16, 185, 129, 0.12);
    --success-border: rgba(16, 185, 129, 0.35);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background-color: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    padding: 24px;
}
.container { max-width: 1200px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px; }
header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
    gap: 12px;
}
.title-group h1 { font-size: 24px; font-weight: 700; color: white; display: flex; align-items: center; gap: 8px; }
.title-group p { color: var(--text-muted); font-size: 13px; }
.header-meta { text-align: right; font-size: 13px; color: var(--text-muted); }

/* Status Banners */
.status-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    border-radius: 8px;
    border: 1px solid transparent;
    font-size: 14px;
    flex-wrap: wrap;
    gap: 10px;
}
.status-critical { background: var(--danger-bg); border-color: var(--danger-border); color: var(--danger); }
.status-warning { background: var(--warning-bg); border-color: var(--warning-border); color: var(--warning); }
.status-healthy { background: var(--success-bg); border-color: var(--success-border); color: var(--success); }

/* Metrics Row */
.metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
}
.card {
    background-color: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 18px;
}
.metric-card {
    display: flex;
    flex-direction: column;
    gap: 6px;
}
.metric-num { font-size: 32px; font-weight: 700; line-height: 1; }
.metric-label { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }

.grid-2col {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}
@media (max-width: 800px) {
    .grid-2col { grid-template-columns: 1fr; }
}

/* Tables */
table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
th {
    background: var(--surface-elevated);
    color: var(--text-muted);
    font-weight: 600;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
}
td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:last-child td { border-bottom: none; }
tbody tr:hover { background-color: rgba(255, 255, 255, 0.02); }

/* Badges */
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
}
.badge-danger { background: var(--danger-bg); border: 1px solid var(--danger-border); color: var(--danger); }
.badge-warning { background: var(--warning-bg); border: 1px solid var(--warning-border); color: var(--warning); }
.badge-success { background: var(--success-bg); border: 1px solid var(--success-border); color: var(--success); }
.badge-info { background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.35); color: var(--accent); }
.badge-neutral { background: var(--surface-elevated); color: var(--text-muted); }

.remed-card {
    background: var(--surface-elevated);
    border-left: 4px solid var(--accent);
    border-radius: 4px;
    padding: 12px 14px;
    margin-bottom: 10px;
}
.remed-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.remed-title { font-weight: 600; color: white; font-size: 14px; }
.remed-module { margin-left: auto; font-size: 12px; color: var(--text-muted); }
.remed-body { color: var(--text-muted); font-size: 13px; }

.notes-card { background: rgba(56, 189, 248, 0.05); border-color: rgba(56, 189, 248, 0.25); }
.notes-text { white-space: pre-wrap; color: var(--text); font-size: 13px; margin-top: 8px; }

.text-danger { color: var(--danger); }
.text-warning { color: var(--warning); }
.text-muted { color: var(--text-muted); }
.font-bold { font-weight: 700; }
.text-mono { font-family: "Cascadia Code", Consolas, monospace; }
.text-sm { font-size: 12px; }
.whitespace-nowrap { white-space: nowrap; }
.log-msg { word-break: break-word; }
.log-raw summary { color: var(--accent); cursor: pointer; font-size: 11px; margin-top: 4px; }
.log-raw pre {
    background: rgba(0, 0, 0, 0.4);
    padding: 8px;
    border-radius: 4px;
    font-size: 11px;
    margin-top: 4px;
    overflow-x: auto;
    color: var(--text-muted);
}

h2, h3 { color: white; margin-bottom: 12px; font-weight: 600; }
footer {
    text-align: center;
    font-size: 12px;
    color: var(--text-muted);
    border-top: 1px solid var(--border);
    padding-top: 16px;
}
"""

_REPORT_BODY_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Incident Diagnostics Report — {hostname_title}</title>
    <style>
{css_content}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-group">
                <h1>📦 Diagnostic Incident Report</h1>
                <p>Generated by Windows Client Tool &bull; Host: <b>{hostname}</b></p>
            </div>
            <div class="header-meta">
                <div><b>Date:</b> {now_str}</div>
                <div><b>OS:</b> {os_name} ({architecture})</div>
                <div><b>Uptime:</b> {uptime}</div>
            </div>
        </header>

        <div class="status-banner {status_banner_class}">
            <div>
                <strong>STATUS: {status_badge_text}</strong> &mdash; {status_summary_text}
            </div>
        </div>

        <div class="metrics-grid">
            <div class="card metric-card">
                <span class="metric-num" style="color: var(--accent);">{total_cnt}</span>
                <span class="metric-label">Total Events Analyzed</span>
            </div>
            <div class="card metric-card">
                <span class="metric-num text-danger">{err_cnt}</span>
                <span class="metric-label">Critical / Errors</span>
            </div>
            <div class="card metric-card">
                <span class="metric-num text-warning">{warn_cnt}</span>
                <span class="metric-label">Warnings</span>
            </div>
            <div class="card metric-card">
                <span class="metric-num" style="color: var(--text);">{channels_cnt}</span>
                <span class="metric-label">Diagnostic Channels</span>
            </div>
        </div>

        {notes_section_html}

        <div class="card">
            <h2>💡 Actionable Remediation & Recommended Fixes</h2>
            <div class="remed-list">
                {remed_cards_html}
            </div>
        </div>

        <div class="grid-2col">
            <div class="card">
                <h2>🔥 Top Faulting Sources</h2>
                <table>
                    <thead>
                        <tr><th>Source</th><th>Errors</th><th>Warnings</th></tr>
                    </thead>
                    <tbody>
                        {top_rows_html}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h2>📑 Diagnostic Channels Breakdown</h2>
                <table>
                    <thead>
                        <tr><th>Channel</th><th>Total</th><th>Errors</th><th>Warnings</th><th>Health</th></tr>
                    </thead>
                    <tbody>
                        {chan_rows_html}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="card">
            <h2>⚠️ Incident Highlights (Errors & Warnings)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Level</th>
                        <th>Channel</th>
                        <th>Source</th>
                        <th>Message</th>
                    </tr>
                </thead>
                <tbody>
                    {incident_rows_html}
                </tbody>
            </table>
        </div>

        <footer>
            Windows Client Tool &bull; Confidential Incident Bundle &bull; Sanitized for Safe Sharing
        </footer>
    </div>
</body>
</html>
"""


def generate_report_html(
    system_info: Dict[str, str],
    analysis: Dict[str, Any],
    extra_notes: str = "",
) -> str:
    """Generate self-contained executive diagnostic HTML report."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    err_cnt = analysis["error_count"]
    warn_cnt = analysis["warning_count"]
    total_cnt = analysis["total_events"]
    channels_cnt = len(analysis.get("channel_stats", {}))

    if err_cnt > 0:
        status_banner_class = "status-critical"
        status_badge_text = "CRITICAL / ERRORS DETECTED"
        status_summary_text = f"{err_cnt} error/critical events require attention across {channels_cnt} channels."
    elif warn_cnt > 0:
        status_banner_class = "status-warning"
        status_badge_text = "WARNINGS PRESENT"
        status_summary_text = f"{warn_cnt} warning events logged across {channels_cnt} channels."
    else:
        status_banner_class = "status-healthy"
        status_badge_text = "ALL CHANNELS HEALTHY"
        status_summary_text = f"Zero errors or warnings detected in {total_cnt} recorded events."

    top_rows_html = _build_top_faulting_html(analysis)
    remed_cards_html = _build_remediation_cards_html(analysis)
    chan_rows_html = _build_channel_stats_html(analysis)
    incident_rows_html = _build_incident_highlights_html(analysis)

    notes_section_html = ""
    if extra_notes.strip():
        notes_section_html = (
            '<div class="card notes-card">'
            '<h3>📝 Incident Notes / Active Context</h3>'
            f'<p class="notes-text">{html.escape(extra_notes.strip())}</p>'
            '</div>'
        )

    # Resolved CSS with palette theme
    try:
        from core.semantic_colors import CHROME_PALETTES, SEMANTIC_PALETTES, PANE_BACKGROUND
        c = CHROME_PALETTES['dark']
        s = SEMANTIC_PALETTES['dark']
        css = _REPORT_CSS.replace('__BG__', PANE_BACKGROUND['dark']
                ).replace('__SURFACE__', c['surface']
                ).replace('__SURFACE_ELEVATED__', c['surface_selected']
                ).replace('__BORDER__', c['outline']
                ).replace('__TEXT__', c['text']
                ).replace('__TEXT_MUTED__', c['text_muted']
                ).replace('__ACCENT__', s['info']
                ).replace('__DANGER__', s['error']
                ).replace('__WARNING__', s['warning']
                ).replace('__SUCCESS__', s['success'])
    except Exception as ex:
        logger.debug("Failed applying semantic palette to report CSS: %s", ex)
        css = _REPORT_CSS

    return _REPORT_BODY_TEMPLATE.format(
        hostname_title=html.escape(system_info.get('hostname', 'System')),
        hostname=html.escape(system_info.get('hostname', 'Host')),
        now_str=now_str,
        os_name=html.escape(system_info.get('os_name', 'Windows')),
        architecture=html.escape(system_info.get('architecture', 'x64')),
        uptime=html.escape(system_info.get('uptime', 'Unknown')),
        status_banner_class=status_banner_class,
        status_badge_text=status_badge_text,
        status_summary_text=status_summary_text,
        total_cnt=total_cnt,
        err_cnt=err_cnt,
        warn_cnt=warn_cnt,
        channels_cnt=channels_cnt,
        notes_section_html=notes_section_html,
        remed_cards_html=remed_cards_html,
        top_rows_html=top_rows_html,
        chan_rows_html=chan_rows_html,
        incident_rows_html=incident_rows_html,
        css_content=css,
    )


# ---------------------------------------------------------------------------
# Core Incident Packaging Entry Point
# ---------------------------------------------------------------------------

def create_incident_package(
    output_zip_path: str,
    readers_data: Dict[str, Sequence[Any]],
    extra_notes: str = "",
) -> Dict[str, Any]:
    """Create a complete diagnostic incident bundle ZIP archive.

    Files generated in archive:
    - report.html: Executive diagnostic HTML report
    - events.json: Structured JSON of all entries across all diagnostic channels
    - events_sanitized.csv: PII-sanitized CSV log entries
    - system_summary.txt: Host, OS version, boot time, uptime, active user notes

    Returns:
        Summary stats dict: (total_events, error_count, warning_count, zip_size_bytes, output_path, ...)
    """
    output_zip_path = os.path.abspath(output_zip_path)
    parent_dir = os.path.dirname(output_zip_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    # 1. Analyze and normalize logs
    analysis = analyze_diagnostics(readers_data)
    system_info = get_system_info()

    # 2. Build report.html
    html_content = generate_report_html(system_info, analysis, extra_notes=extra_notes)

    # 3. Build events.json
    json_entries = []
    for e in analysis.get("entries", []):
        json_entries.append({
            "channel": e["channel"],
            "timestamp": e["timestamp"],
            "level": e["level"],
            "source": e["source"],
            "message": e["message"],
            "raw": e["raw"],
        })

    json_payload = {
        "generated_at": datetime.now().isoformat(),
        "notes": extra_notes,
        "system": system_info,
        "summary": {
            "total_events": analysis["total_events"],
            "error_count": analysis["error_count"],
            "warning_count": analysis["warning_count"],
            "info_count": analysis["info_count"],
            "channels": list(readers_data.keys()),
        },
        "entries": json_entries,
    }
    json_content = json.dumps(json_payload, indent=2, ensure_ascii=False)

    # 4. Build events_sanitized.csv
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf, lineterminator="\n")
    writer.writerow(["Timestamp", "Channel", "Level", "Source", "Message", "RawDetails"])

    for e in analysis.get("entries", []):
        san_ts = sanitize_pii(e["timestamp"])
        san_ch = sanitize_pii(e["channel"])
        san_lvl = sanitize_pii(e["level"])
        san_src = sanitize_pii(e["source"])
        san_msg = sanitize_pii(e["message"])
        san_raw = sanitize_pii(json.dumps(e["raw"], ensure_ascii=False)) if e["raw"] else ""
        writer.writerow([san_ts, san_ch, san_lvl, san_src, san_msg, san_raw])
    csv_content = csv_buf.getvalue()

    # 5. Build system_summary.txt
    summary_txt = generate_system_summary_txt(system_info, analysis, extra_notes=extra_notes)

    # 6. Write ZIP archive
    with zipfile.ZipFile(output_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.html", html_content)
        zf.writestr("events.json", json_content)
        zf.writestr("events_sanitized.csv", csv_content)
        zf.writestr("system_summary.txt", summary_txt)

    zip_size = os.path.getsize(output_zip_path)

    return {
        "total_events": analysis["total_events"],
        "error_count": analysis["error_count"],
        "warning_count": analysis["warning_count"],
        "info_count": analysis["info_count"],
        "zip_size_bytes": zip_size,
        "output_path": output_zip_path,
        "channels": list(readers_data.keys()),
        "generated_at": datetime.now().isoformat(),
    }
