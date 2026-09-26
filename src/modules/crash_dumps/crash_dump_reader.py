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
import logging
import os
import re
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from core.types import LogEntry
from core.windows_utils import system_root

from modules.crash_dumps.dump_parser import DumpInfo, read_dump_info
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
    if info.kind in ("kernel64", "kernel32") and info.bugcheck_code is not None:
        code = info.bugcheck_code
        label = bugcheck_label(code)
        # Anything under LiveKernelReports is a live dump: taken while the system kept running.
        live = is_live_kernel_event(code) or bool(live_kind)
        raw.update(bug_check=label, bugcheck_code=_hex(code),
                   params=[_hex(p) for p in info.params], exception_code=_hex(info.exception_code or 0),
                   exception_address=_hex(info.exception_address or 0),
                   machine=info.machine, processors=info.processors, build=info.build)
        if live:
            known = bugcheck_info(code)
            what = f"{label}" if known else f"live kernel event {_hex(code)}"
            raw["cause"] = ((known.cause + " ") if known else "") + (
                f"This is a LIVE dump ({live_kind or 'unnamed'}): Windows captured it while the system kept "
                f"running, because a driver reported a watchdog/timeout. It is not a blue screen.")
            return "Warning", f"{filename} ({size // 1024} KB) - live dump, {what}", raw
        known = bugcheck_info(code)
        if known:
            raw["cause"] = known.cause
        return "Error", f"{filename} ({size // 1024} KB) - {label}", raw
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
    return {"code": code, "params": params, "dump_path": path.group(1) if path else ""}


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
        label = bugcheck_label(parsed["code"])
        known = bugcheck_info(parsed["code"])
        raw = {"bug_check": label, "bugcheck_code": _hex(parsed["code"]), "params": parsed["params"],
               "dump_path": parsed["dump_path"], "kind": "event"}
        if known:
            raw["cause"] = known.cause
        out.append(LogEntry(timestamp=ev.timestamp, source="BugCheck event", level="Error",
                            message=f"System restarted after bugcheck {label}", raw=raw))
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
    step(25)
    if os.path.isfile(MEMORY_DMP):
        entry = _entry_for_file(MEMORY_DMP, "MEMORY.DMP")
        if entry is not None:
            entries.append(entry)
    step(45)
    live, live_notes = _scan_live_reports(LIVE_REPORTS_DIR)
    entries.extend(live)
    notes.extend(live_notes)
    step(75)
    events, note = _bugcheck_events()
    entries.extend(events)
    if note:
        notes.append(note)
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    step(100)
    return entries, notes


def summary_text(entries: List[LogEntry], notes: List[str]) -> str:
    crashes = [e for e in entries if e.source in ("Minidump", "MEMORY.DMP", "BugCheck event")]
    live = [e for e in entries if e.source.startswith("LiveKernelReports")]
    if not entries:
        base = ("No crash records were found in the places that could be read." if notes
                else "No crash dumps or bugcheck events were found: no blue screen on record.")
    else:
        base = f"{len(crashes)} crash record(s) (dumps and bugcheck events) and {len(live)} live kernel report(s)."
        kinds = {}
        for e in live:
            kinds[e.source.split("\\", 1)[1]] = kinds.get(e.source.split("\\", 1)[1], 0) + 1
        if kinds:
            base += " Live reports by driver/watchdog: " + ", ".join(f"{k} x{n}" for k, n in sorted(kinds.items())) + "."
    return base + ("  " + " ".join(notes) if notes else "")


def detail_html(entry: LogEntry) -> str:
    """Bugcheck explanation for the detail panel: what it is, likely cause, what the parameters mean."""
    import html

    raw = entry.raw or {}
    label = raw.get("bug_check")
    if not label or label == "Unknown":
        return ""
    out = [f"<b>{html.escape(str(label))}</b><br>"]
    if raw.get("cause"):
        out.append(f"<i>Usual cause:</i> {html.escape(raw['cause'])}<br>")
    code = raw.get("bugcheck_code")
    params = raw.get("params") or []
    if code and params:
        try:
            info = bugcheck_info(int(code, 16))
        except ValueError:
            info = None
        meanings = info.params if info else ()
        out.append("<i>Parameters:</i><br>")
        for i, value in enumerate(params):
            note = meanings[i] if i < len(meanings) and meanings[i] else ""
            out.append(f"&nbsp;&nbsp;{i + 1}: {html.escape(str(value))}"
                       + (f" &nbsp;({html.escape(note)})" if note else "") + "<br>")
    if raw.get("faulting_module"):
        out.append(f"<i>Faulting module:</i> {html.escape(raw['faulting_module'])}<br>")
    elif raw.get("kind") in ("kernel64", "kernel32"):
        out.append("<i>Faulting driver:</i> not stated in a kernel dump's header; it needs a "
                   "debugger's stack analysis (open the dump in WinDbg and run !analyze -v).<br>")
    if raw.get("dump_path"):
        out.append(f"<i>Dump file:</i> {html.escape(raw['dump_path'])}<br>")
    return "".join(out) + "<hr>"
