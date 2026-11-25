"""Everything the machine kept about its crashes, as `LogEntry` rows.

Four sources, because a crash leaves evidence in more than one place and any
one of them can be missing:

* `C:\\Windows\\Minidump\\*.dmp`, small kernel dumps written at a bugcheck
* `C:\\Windows\\MEMORY.DMP`, the larger kernel/complete dump
* `C:\\Windows\\LiveKernelReports\\<kind>\\*.dmp`, dumps Windows takes WITHOUT
  crashing when a driver reports a watchdog timeout (GPU hangs, mostly)
* System-log event 1001 (BugCheck), which survives after the dump file has
  been deleted or was never written, and carries the code and parameters

Every dump is opened read-only and only its header is parsed (`dump_parser`).
An unreadable directory is REPORTED, never the same as an empty one.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from core.types import LogEntry
from core.windows_utils import system_root

from modules.crash_dumps.dump_parser import DumpInfo, read_dump_info
from modules.crash_dumps.rca_engine import (
    correlate_incidents,
    decode_bugcheck_params,
    get_bugcheck_sysadmin_explanation,
    parse_kernel_power_41,
    parse_whea_record,
)
from modules.crash_dumps.driver_fingerprint import (
    CulpritInfo,
    extract_driver_culprit,
    get_driver_info,
    is_third_party_driver,
)
from core.diag_knowledge import bugcheck_info, bugcheck_label, is_live_kernel_event

logger = logging.getLogger(__name__)

MINIDUMP_DIR = os.path.join(system_root(), "Minidump")
MEMORY_DMP = os.path.join(system_root(), "MEMORY.DMP")
LIVE_REPORTS_DIR = os.path.join(system_root(), "LiveKernelReports")

_BUGCHECK_TEXT = re.compile(
    r"bugcheck was:\s*(0x[0-9a-fA-F]+)\s*\(([^)]*)\)", re.IGNORECASE)
_DUMP_PATH = re.compile(r"saved in:\s*(\S.*?)\.\s", re.IGNORECASE)


def _hex(v: int) -> str:
    return f"0x{v:X}"


def describe(info: DumpInfo, filename: str, size: int, live_kind: str = "") -> Tuple[str, str, dict]:
    """(level, message, raw) for one parsed dump."""
    raw = {"filename": filename, "file_size": size, "kind": info.kind}
    culprit = (
        extract_driver_culprit(info.faulting_module)
        or extract_driver_culprit(filename)
        or extract_driver_culprit(live_kind)
    )
    if culprit:
        raw["culprit_driver"] = culprit.filename
        raw["faulting_module"] = culprit.filename
        raw["is_third_party"] = culprit.is_third_party
        raw["vendor"] = culprit.vendor
        raw["category"] = culprit.category
        raw["remedy"] = culprit.remedy
    elif info.faulting_module:
        raw["faulting_module"] = info.faulting_module

    culprit_suffix = f" [Culprit: {culprit.display_tag}]" if (culprit and culprit.is_third_party) else ""

    if info.kind in ("kernel64", "kernel32") and info.bugcheck_code is not None:
        code = info.bugcheck_code
        label = bugcheck_label(code)
        # Anything under LiveKernelReports is a live dump: taken while the system kept running.
        live = is_live_kernel_event(code) or bool(live_kind)
        raw.update(bug_check=label, bugcheck_code=_hex(code),
                   params=[_hex(p) for p in info.params], exception_code=_hex(info.exception_code or 0),
                   exception_address=_hex(info.exception_address or 0),
                   machine=info.machine, processors=info.processors, build=info.build)
        explanation = get_bugcheck_sysadmin_explanation(code, tuple(info.params))
        if live:
            what = f"{label}" if bugcheck_info(code) else f"live kernel event {_hex(code)}"
            raw["cause"] = ((explanation + " ") if explanation else "") + (
                f"This is a LIVE dump ({live_kind or 'unnamed'}): Windows captured it while the system kept "
                f"running, because a driver reported a watchdog/timeout. It is not a blue screen.")
            return "Warning", f"{filename} ({size // 1024} KB) - live dump, {what}{culprit_suffix}", raw
        raw["cause"] = explanation
        return "Error", f"{filename} ({size // 1024} KB) - {label}{culprit_suffix}", raw
    if info.kind == "user":
        raw.update(bug_check="User-mode dump")
        detail = f" faulting module {info.faulting_module}" if info.faulting_module else ""
        if info.exception_code is not None:
            raw["exception_code"] = _hex(info.exception_code)
            raw["exception_address"] = _hex(info.exception_address or 0)
            detail = f" exception {_hex(info.exception_code)}{detail}"
        if info.faulting_module:
            raw["faulting_module"] = info.faulting_module
        return "Error", f"{filename} ({size // 1024} KB) - user-mode dump{detail}", raw
    raw["bug_check"] = "Unknown"
    if info.problems:
        raw["problems"] = "; ".join(info.problems)
    return "Error", f"{filename} ({size // 1024} KB) - header not recognised ({'; '.join(info.problems)})", raw


def _entry_for_file(path: str, source: str, live_kind: str = "") -> Optional[LogEntry]:
    try:
        stat = os.stat(path)
    except OSError as exc:
        logger.warning("Could not stat dump file %s: %s", path, exc)
        return None
    info = read_dump_info(path)
    level, message, raw = describe(info, os.path.basename(path), stat.st_size, live_kind)
    raw["filepath"] = path
    return LogEntry(timestamp=datetime.fromtimestamp(stat.st_mtime), source=source,
                    level=level, message=message, raw=raw)


def read_crash_dumps(
    dump_dir: str = MINIDUMP_DIR,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> List[LogEntry]:
    """Kernel minidumps in `dump_dir`, newest first (kept for callers/tests)."""
    entries, _note = _scan_dir(dump_dir, "Minidump")
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    if progress_callback:
        progress_callback(100)
    return entries


def _scan_dir(dump_dir: str, source: str, live_kind: str = "") -> Tuple[List[LogEntry], Optional[str]]:
    if not os.path.isdir(dump_dir):
        return [], None
    try:
        names = [f for f in os.listdir(dump_dir) if f.lower().endswith(".dmp")]
    except OSError as exc:
        logger.warning("Cannot list %s: %s", dump_dir, exc)
        return [], f"{dump_dir} could not be read ({exc}); its dumps are not shown."
    out = []
    for name in names:
        entry = _entry_for_file(os.path.join(dump_dir, name), source, live_kind)
        if entry is not None:
            out.append(entry)
    return out, None


def _scan_live_reports(root: str) -> Tuple[List[LogEntry], List[str]]:
    if not os.path.isdir(root):
        return [], []
    try:
        kinds = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
    except OSError as exc:
        logger.warning("Cannot list %s: %s", root, exc)
        return [], [f"{root} could not be read ({exc}); live kernel reports are not shown."]
    entries, notes = [], []
    for kind in kinds:
        found, note = _scan_dir(os.path.join(root, kind), f"LiveKernelReports\\{kind}", kind)
        entries.extend(found)
        if note:
            notes.append(note)
    return entries, notes


def parse_bugcheck_event(message: str) -> Optional[dict]:
    """Code, parameters and dump path out of a System 1001 BugCheck message."""
    m = _BUGCHECK_TEXT.search(message or "")
    if not m:
        return None
    try:
        code = int(m.group(1), 16)
    except ValueError:
        return None
    params = [p.strip() for p in m.group(2).split(",")]
    path = _DUMP_PATH.search(message or "")
    culprit = extract_driver_culprit(message or "")
    return {
        "code": code,
        "params": params,
        "dump_path": path.group(1) if path else "",
        "culprit": culprit,
    }


def _bugcheck_events(days: int = 90) -> Tuple[List[LogEntry], Optional[str]]:
    from modules.event_viewer import event_query

    xpath = ("*[System[Provider[@Name='Microsoft-Windows-WER-SystemErrorReporting'] and EventID=1001 "
             f"and TimeCreated[timediff(@SystemTime) <= {days * 86400 * 1000}]]]")
    result = event_query.query_log("System", None, 200, xpath=xpath)
    if result.error is not None:
        return [], f"BugCheck events could not be read: {result.error}"
    out = []
    for ev in result.entries:
        parsed = parse_bugcheck_event(ev.message)
        if parsed is None:
            continue
        code = parsed["code"]
        label = bugcheck_label(code)
        explanation = get_bugcheck_sysadmin_explanation(code, tuple(parsed["params"]))
        culprit = parsed.get("culprit")
        culprit_suffix = f" [Culprit: {culprit.display_tag}]" if (culprit and culprit.is_third_party) else ""

        raw = {
            "bug_check": label,
            "bugcheck_code": _hex(code),
            "params": parsed["params"],
            "dump_path": parsed["dump_path"],
            "kind": "event",
            "cause": explanation,
        }
        if culprit:
            raw["culprit_driver"] = culprit.filename
            raw["faulting_module"] = culprit.filename
            raw["is_third_party"] = culprit.is_third_party
            raw["vendor"] = culprit.vendor
            raw["category"] = culprit.category
            raw["remedy"] = culprit.remedy

        out.append(LogEntry(
            timestamp=ev.timestamp,
            source="BugCheck event",
            level="Error",
            message=f"System restarted after bugcheck {label}{culprit_suffix}",
            raw=raw,
        ))
    return out, None


def _wer_application_events(days: int = 90) -> Tuple[List[LogEntry], Dict[str, CulpritInfo]]:
    """Query Application log for Windows Error Reporting Event 1001 BlueScreen / LiveKernelEvent."""
    from modules.event_viewer import event_query

    xpath = ("*[System[Provider[@Name='Windows Error Reporting'] and EventID=1001 "
             f"and TimeCreated[timediff(@SystemTime) <= {days * 86400 * 1000}]]]")
    result = event_query.query_log("Application", None, 100, xpath=xpath)
    if result.error is not None:
        return [], {}
    out: List[LogEntry] = []
    dump_map: Dict[str, CulpritInfo] = {}

    for ev in result.entries:
        msg = ev.message or ""
        raw_data = ev.raw.get("data", {}) if ev.raw else {}
        event_name = raw_data.get("EventName") or ""
        if not event_name:
            if "BlueScreen" in msg:
                event_name = "BlueScreen"
            elif "LiveKernelEvent" in msg:
                event_name = "LiveKernelEvent"
        if event_name not in ("BlueScreen", "LiveKernelEvent"):
            continue

        culprit = extract_driver_culprit(raw_data) or extract_driver_culprit(msg)

        # Index any referenced dump file names
        attached = raw_data.get("AttachedFiles") or ""
        dump_names = re.findall(r"([a-zA-Z0-9_\-]+\.dmp)", f"{attached} {msg}", re.IGNORECASE)
        if culprit:
            for dm in dump_names:
                dump_map[dm.lower()] = culprit

        p1 = raw_data.get("P1")
        code_int = None
        if p1:
            try:
                code_int = int(str(p1), 16)
            except (ValueError, TypeError) as exc:
                logger.debug("WER P1 could not be parsed as hex: %s", exc)

        label = bugcheck_label(code_int) if code_int else f"WER {event_name}"
        culprit_suffix = f" [Culprit: {culprit.display_tag}]" if (culprit and culprit.is_third_party) else ""

        raw_entry = {
            "bug_check": label,
            "kind": "wer_report",
            "event_name": event_name,
            "data": raw_data,
        }
        if code_int is not None:
            raw_entry["bugcheck_code"] = _hex(code_int)
            raw_entry["cause"] = get_bugcheck_sysadmin_explanation(code_int)
        if culprit:
            raw_entry["culprit_driver"] = culprit.filename
            raw_entry["faulting_module"] = culprit.filename
            raw_entry["is_third_party"] = culprit.is_third_party
            raw_entry["vendor"] = culprit.vendor
            raw_entry["category"] = culprit.category
            raw_entry["remedy"] = culprit.remedy

        level = "Warning" if event_name == "LiveKernelEvent" else "Error"
        out.append(LogEntry(
            timestamp=ev.timestamp,
            source="Windows Error Reporting",
            level=level,
            message=f"{event_name}: {label}{culprit_suffix}",
            raw=raw_entry,
        ))

    return out, dump_map


def _kernel_power_events(days: int = 90) -> Tuple[List[LogEntry], Optional[str]]:
    from modules.event_viewer import event_query

    xpath = ("*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=41 "
             f"and TimeCreated[timediff(@SystemTime) <= {days * 86400 * 1000}]]]")
    result = event_query.query_log("System", None, 100, xpath=xpath)
    if result.error is not None:
        return [], f"Kernel-Power events could not be read: {result.error}"
    out = []
    for ev in result.entries:
        raw_data = ev.raw.get("data", {}) if ev.raw else {}
        kp = parse_kernel_power_41(raw_data, ev.timestamp)
        level = "Critical" if kp.is_power_cut else "Error"
        raw = {
            "bug_check": kp.diagnosis,
            "bugcheck_code": f"0x{kp.bugcheck_code:X}",
            "params": [f"0x{p:X}" for p in kp.bugcheck_params],
            "kind": "kernel_power",
            "is_power_cut": kp.is_power_cut,
            "cause": kp.cause,
            "event_id": 41,
            "data": raw_data,
        }
        out.append(LogEntry(
            timestamp=ev.timestamp,
            source="Microsoft-Windows-Kernel-Power",
            level=level,
            message=kp.diagnosis,
            raw=raw,
        ))
    return out, None


def _whea_events(days: int = 90) -> Tuple[List[LogEntry], Optional[str]]:
    from modules.event_viewer import event_query

    xpath = ("*[System[Provider[@Name='Microsoft-Windows-WHEA-Logger'] "
             "and (EventID=17 or EventID=18 or EventID=19 or EventID=20 or EventID=47) "
             f"and TimeCreated[timediff(@SystemTime) <= {days * 86400 * 1000}]]]")
    result = event_query.query_log("System", None, 100, xpath=xpath)
    if result.error is not None:
        return [], f"WHEA events could not be read: {result.error}"
    out = []
    for ev in result.entries:
        raw_data = ev.raw.get("data", {}) if ev.raw else {}
        eid = ev.raw.get("event_id", 0) if ev.raw else 0
        whea = parse_whea_record(eid, raw_data, ev.message, ev.timestamp)
        level = "Critical" if whea.is_fatal else "Warning"
        raw = {
            "bug_check": whea.description,
            "kind": "whea",
            "event_id": eid,
            "apic_id": whea.apic_id,
            "bank": whea.bank,
            "is_fatal": whea.is_fatal,
            "cause": f"WHEA hardware error: {whea.description}.",
            "data": raw_data,
        }
        out.append(LogEntry(
            timestamp=ev.timestamp,
            source="Microsoft-Windows-WHEA-Logger",
            level=level,
            message=whea.description,
            raw=raw,
        ))
    return out, None


def read_crash_evidence(progress_callback: Optional[Callable[[int], None]] = None
                        ) -> Tuple[List[LogEntry], List[str]]:
    """Every source above, newest first, plus notes on anything that could not be read."""
    entries: List[LogEntry] = []
    notes: List[str] = []

    def step(pct):
        if progress_callback:
            progress_callback(pct)

    mini, note = _scan_dir(MINIDUMP_DIR, "Minidump")
    entries.extend(mini)
    if note:
        notes.append(note)
    step(15)
    if os.path.isfile(MEMORY_DMP):
        entry = _entry_for_file(MEMORY_DMP, "MEMORY.DMP")
        if entry is not None:
            entries.append(entry)
    step(30)
    live, live_notes = _scan_live_reports(LIVE_REPORTS_DIR)
    entries.extend(live)
    notes.extend(live_notes)
    step(45)
    events, note = _bugcheck_events()
    entries.extend(events)
    if note:
        notes.append(note)
    step(60)
    wer_events, dump_culprits = _wer_application_events()
    if wer_events:
        # Avoid duplicating if already recorded by System 1001 BugCheck event
        existing_times = [e.timestamp for e in entries if e.source == "BugCheck event"]
        for we in wer_events:
            if not any(abs((we.timestamp - et).total_seconds()) < 5 for et in existing_times):
                entries.append(we)
    # Correlate dump files with dump_culprits from WER
    if dump_culprits:
        for e in entries:
            fn = (e.raw or {}).get("filename", "").lower()
            if fn in dump_culprits and not (e.raw or {}).get("culprit_driver"):
                c = dump_culprits[fn]
                e.raw["culprit_driver"] = c.filename
                e.raw["faulting_module"] = c.filename
                e.raw["is_third_party"] = c.is_third_party
                e.raw["vendor"] = c.vendor
                e.raw["category"] = c.category
                e.raw["remedy"] = c.remedy
                if c.is_third_party and "[Culprit:" not in e.message:
                    e.message += f" [Culprit: {c.display_tag}]"
    step(75)
    kp_events, note = _kernel_power_events()
    entries.extend(kp_events)
    if note:
        notes.append(note)
    step(85)
    whea, note = _whea_events()
    entries.extend(whea)
    if note:
        notes.append(note)
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    step(100)
    return entries, notes


#: How far back the two comparison windows reach. 30/30 was picked because it is the
#: smallest window that still reads as "recent" to an admin while giving a crash that
#: happens every couple of weeks a chance to show up on both sides of the split.
_TREND_WINDOW_DAYS = 30
#: Below this many total records across both windows, "more/less frequent" is not a
#: trend -- it is one or two isolated incidents, and saying "100% more frequent" about
#: 2 crashes vs 1 is the kind of alarming-not-actionable reading the sysadmin review
#: process exists to avoid.
_TREND_MIN_RECORDS = 3


def crash_frequency_trend(entries: List[LogEntry], now: Optional[datetime] = None) -> Optional[str]:
    """Is this machine bugchecking more or less often lately?

    Compares the count of actual crash records (kernel dumps and BugCheck events --
    the same `crashes` definition `summary_text` uses, NOT live kernel reports, which
    are watchdog recoveries rather than blue screens) in the last `_TREND_WINDOW_DAYS`
    days against the `_TREND_WINDOW_DAYS` days before that.

    Returns None rather than a sentence when there isn't enough data to say anything
    useful: no crash records at all, or all of them older than both windows, or too
    few total records for "more/less frequent" to mean anything (see
    `_TREND_MIN_RECORDS`). A trend needs real data on both sides of the split to be
    worth stating -- an admin who sees "2 in the last 30 days vs 0 before" from a
    machine with exactly 2 crashes in its lifetime is being alarmed, not informed.
    """
    now = now or datetime.now()
    crashes = [e for e in entries if e.source in ("Minidump", "MEMORY.DMP", "BugCheck event")]
    if not crashes:
        return None
    recent_cut = now - timedelta(days=_TREND_WINDOW_DAYS)
    prior_cut = now - timedelta(days=2 * _TREND_WINDOW_DAYS)
    recent = sum(1 for e in crashes if e.timestamp >= recent_cut)
    prior = sum(1 for e in crashes if prior_cut <= e.timestamp < recent_cut)
    if recent + prior < _TREND_MIN_RECORDS:
        return None
    if recent > prior:
        verdict = "getting MORE frequent"
    elif recent < prior:
        verdict = "getting LESS frequent"
    else:
        verdict = "holding steady"
    return (f"Crash frequency: {recent} in the last {_TREND_WINDOW_DAYS} days vs {prior} in the "
            f"{_TREND_WINDOW_DAYS} days before that — {verdict}.")


def summary_text(entries: List[LogEntry], notes: List[str]) -> str:
    crashes = [e for e in entries if e.source in ("Minidump", "MEMORY.DMP", "BugCheck event", "Windows Error Reporting")]
    live = [e for e in entries if e.source.startswith("LiveKernelReports")]
    rca = correlate_incidents(entries)
    if not entries:
        base = ("No crash records were found in the places that could be read." if notes
                else "No crash dumps, bugchecks, or dirty shutdowns on record (no blue screen).")
    else:
        base = f"{len(crashes)} crash dump/bugcheck record(s), {rca.power_cut_count} sudden power cut/reset(s), {rca.whea_count} WHEA hardware error(s), and {len(live)} live kernel report(s)."
        if rca.suspect_summary:
            base += f" [RCA: {rca.suspect_summary}]"
        trend = crash_frequency_trend(entries)
        if trend:
            base += " " + trend
    return base + ("  " + " ".join(notes) if notes else "")


def detail_html(entry: LogEntry) -> str:
    """Bugcheck, power cut or WHEA explanation for the detail panel."""
    import html

    raw = entry.raw or {}
    label = raw.get("bug_check")
    if not label or label == "Unknown":
        return ""
    out = [f"<b>{html.escape(str(label))}</b><br>"]
    if raw.get("cause"):
        out.append(f"<i>Diagnosis:</i> {html.escape(raw['cause'])}<br>")
    code = raw.get("bugcheck_code")
    params = raw.get("params") or []
    if code and params and code != "0x0":
        try:
            code_num = int(code, 16)
        except ValueError:
            code_num = None
        numeric_params = []
        for p in params:
            try:
                numeric_params.append(int(str(p), 16) if str(p).startswith("0x") else int(str(p)))
            except (ValueError, TypeError):
                numeric_params.append(0)
        detailed_decoded = decode_bugcheck_params(code_num, tuple(numeric_params)) if code_num is not None else []

        out.append("<i>Parameters:</i><br>")
        if detailed_decoded:
            for line in detailed_decoded:
                out.append(f"&nbsp;&nbsp;{html.escape(line)}<br>")
        else:
            info = bugcheck_info(code_num) if code_num else None
            meanings = info.params if info else ()
            for i, value in enumerate(params):
                note = meanings[i] if i < len(meanings) and meanings[i] else ""
                out.append(f"&nbsp;&nbsp;{i + 1}: {html.escape(str(value))}"
                           + (f" &nbsp;({html.escape(note)})" if note else "") + "<br>")
    if raw.get("apic_id") is not None:
        out.append(f"<i>Processor APIC ID (Logical Core):</i> {raw['apic_id']}<br>")
    if raw.get("bank") is not None:
        out.append(f"<i>Hardware Bank:</i> {raw['bank']}<br>")

    # Driver Culprit Fingerprinting
    culprit = None
    if raw.get("culprit_driver") or raw.get("faulting_module"):
        target_name = raw.get("culprit_driver") or raw.get("faulting_module")
        culprit = extract_driver_culprit(target_name) or extract_driver_culprit(raw)
    else:
        culprit = extract_driver_culprit(raw) or extract_driver_culprit(entry.message)

    if culprit:
        from core.semantic_colors import chrome, semantic
        err_col = semantic("error")
        info_col = semantic("info")
        surf_col = chrome("surface")
        text_col = chrome("text")
        badge = (
            f'<span style="background-color: {err_col}; color: #ffffff; padding: 2px 6px; border-radius: 3px; font-size: 11px; font-weight: bold;">THIRD-PARTY DRIVER</span>'
            if culprit.is_third_party else
            f'<span style="background-color: {info_col}; color: #ffffff; padding: 2px 6px; border-radius: 3px; font-size: 11px;">OS COMPONENT</span>'
        )
        vendor_html = f"<b>Vendor:</b> {html.escape(culprit.vendor)} &nbsp;|&nbsp; <b>Category:</b> {html.escape(culprit.category)}<br>" if culprit.vendor else (f"<b>Category:</b> {html.escape(culprit.category)}<br>" if culprit.category != "Unknown" else "")
        desc_html = f"<i>Description:</i> {html.escape(culprit.description)}<br>" if culprit.description else ""
        remedy_html = f"<b>Sysadmin Action:</b> {html.escape(culprit.remedy)}<br>" if culprit.remedy else ""
        out.append(
            f'<div style="background-color: {surf_col}; color: {text_col}; border-left: 4px solid {err_col}; padding: 8px 10px; margin: 8px 0; border-radius: 4px;">'
            f'<b>🚨 Suspect Driver Culprit:</b> <code style="color: {err_col}; font-size: 13px; font-weight: bold;">{html.escape(culprit.filename)}</code> &nbsp;{badge}<br>'
            f'{vendor_html}{desc_html}{remedy_html}'
            f'</div>'
        )
    elif raw.get("faulting_module"):
        out.append(f"<i>Faulting module:</i> {html.escape(raw['faulting_module'])}<br>")
    elif raw.get("kind") in ("kernel64", "kernel32"):
        out.append("<i>Faulting driver:</i> not stated in a kernel dump's header; it needs a "
                   "debugger's stack analysis (open the dump in WinDbg and run !analyze -v).<br>")
    if raw.get("dump_path"):
        out.append(f"<i>Dump file:</i> {html.escape(raw['dump_path'])}<br>")
    return "".join(out) + "<hr>"
