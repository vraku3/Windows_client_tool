"""A summary strip for the CBS and DISM logs: findings first, raw lines below. Qt-free.

Both logs are tens of thousands of lines of routine chatter in which the answer
to "is the component store damaged, and did the repair work?" is a handful of
lines. This finds them: corruption markers, SFC repair activity, and the
failing HRESULTs (translated, counted), regardless of the severity the log
itself printed -- CBS logs `CBS_E_INVALID_PACKAGE` at Info.
"""
from __future__ import annotations

import html
import re
from collections import Counter
from typing import List, Optional, Tuple

from core.types import LogEntry
from modules.log_viewer.error_codes import corruption_spans, describe, explain

_HRESULT = re.compile(r"\b0[xX]([0-9a-fA-F]{8})\b")
#: `[HRESULT = 0x800f0805 - CBS_E_INVALID_PACKAGE]`: Windows names the code itself.
_INLINE_NAME = re.compile(r"0[xX]([0-9a-fA-F]{8})\s*-\s*([A-Z][A-Z0-9_]{4,})")
_SR_ACTIVITY = re.compile(r"\[SR\]\s+(Repairing|Repaired|Repair complete|Verifying)", re.IGNORECASE)
_SR_REPAIRED = re.compile(r"\[SR\]\s+(Repairing|Repaired|Repair complete)", re.IGNORECASE)
_DISM_COMMAND = re.compile(r"Executing command line:\s*(.+)$", re.IGNORECASE)


def failing_codes(entries: List[LogEntry]) -> Counter:
    """Count of each failing HRESULT (sign bit set) across all messages."""
    counts: Counter = Counter()
    for e in entries:
        for m in _HRESULT.finditer(e.message or ""):
            code = int(m.group(1), 16)
            if code & 0x80000000:
                counts[code] += 1
    return counts


def _inline_names(entries: List[LogEntry]) -> dict:
    names = {}
    for e in entries:
        for m in _INLINE_NAME.finditer(e.message or ""):
            names.setdefault(int(m.group(1), 16), m.group(2))
    return names


def code_meaning(code: int, names: dict) -> str:
    text = describe(code)
    if names.get(code):
        return f"{names[code]}" + (f": {text}" if text else "")
    return text


def corruption_markers(entries: List[LogEntry]) -> Counter:
    counts: Counter = Counter()
    for e in entries:
        for _s, _e, label in corruption_spans(e.message or ""):
            counts[label] += 1
    return counts


def summarize(entries: List[LogEntry], kind: str = "cbs") -> str:
    """One paragraph. `kind` is "cbs" or "dism"."""
    if not entries:
        return "The log had no readable lines."
    levels = Counter(e.level for e in entries)
    first = min(e.timestamp for e in entries)
    last = max(e.timestamp for e in entries)
    parts = [f"{len(entries):,} lines, {first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M}; "
             f"{levels.get('Error', 0)} Error, {levels.get('Warning', 0)} Warning."]
    markers = corruption_markers(entries)
    if markers:
        parts.append("Corruption markers: " + ", ".join(f"{k} x{v}" for k, v in markers.most_common(4)) + ".")
    else:
        parts.append("No component-store corruption markers.")
    if kind == "cbs":
        repaired = sum(1 for e in entries if _SR_REPAIRED.search(e.message or ""))
        if repaired:
            parts.append(f"SFC/CBS repair activity: {repaired} line(s).")
    if kind == "dism":
        commands = _dism_commands(entries)
        if commands:
            parts.append("Recent DISM commands: " + " | ".join(commands) + ".")
    codes = failing_codes(entries)
    if codes:
        names = _inline_names(entries)
        shown = []
        for code, n in codes.most_common(4):
            meaning = code_meaning(code, names)
            shown.append(f"0x{code:08X} x{n}" + (f" ({meaning})" if meaning else ""))
        parts.append("Failing codes: " + "; ".join(shown)
                     + (f"; +{len(codes) - 4} more" if len(codes) > 4 else "") + ".")
    else:
        parts.append("No failing HRESULTs.")
    return " ".join(parts)


def _dism_commands(entries: List[LogEntry], limit: int = 3) -> List[str]:
    found: List[Tuple] = []
    for e in entries:
        m = _DISM_COMMAND.search(e.message or "")
        if m:
            found.append((e.timestamp, m.group(1).strip()))
    found.sort(key=lambda p: p[0])
    out = []
    for when, cmd in found[-limit:]:
        cmd = re.sub(r"^\"?[^ ]*[Dd]ism(\.exe)?\"?\s*", "", cmd)
        out.append(f"{when:%m-%d %H:%M} {cmd[:70]}")
    return out


def detail_html(entry: LogEntry) -> str:
    """Translate any failing codes in the selected line."""
    found = explain(entry.message or "")
    names = _inline_names([entry])
    rows = []
    for code_text, meaning in found:
        rows.append(f"{html.escape(code_text)} - {html.escape(meaning)}<br>")
    if not rows:
        for code, name in names.items():
            rows.append(f"0x{code:08X} - {html.escape(name)}<br>")
    if not rows:
        return ""
    return "<b>Codes in this line</b><br>" + "".join(rows) + "<hr>"
