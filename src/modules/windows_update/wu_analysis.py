"""What went wrong in Windows Update, in words. Qt-free.

`ReportingEvents.log` records failures as `Installation Failure: Windows failed
to install the following update with error 0x800f0820: <title>`. This module
pulls the failing update (and its KB number when it has one) and the error code
out, translates the code, and counts them.
"""
from __future__ import annotations

import html
import re
from collections import OrderedDict
from typing import List, Optional, Tuple

from core.types import LogEntry
from core.wu_error_codes import decode_wu_error

_HRESULT = re.compile(r"\b0[xX]([0-9a-fA-F]{8})\b")
_KB = re.compile(r"\bKB\d{6,8}\b", re.IGNORECASE)
_FAILED_UPDATE = re.compile(
    r"following update(?: with error 0x[0-9a-fA-F]+)?:\s*(.+?)\.?\s*$", re.IGNORECASE)

#: Codes seen in real ReportingEvents.log files that `decode_wu_error` does not
#: curate. Wording follows Microsoft's documented constant names.
EXTRA_CODES = {
    0x80240016: "install not allowed: another installation is in progress, or a restart is pending (WU_E_INSTALL_NOT_ALLOWED)",
    0x8024200B: "the update's installer reported a failure (WU_E_UH_INSTALLERFAILURE)",
    0x800F0820: "a servicing operation is pending; a restart is usually needed first (CBS_E_PENDING)",
    0x80073D02: "the package could not be installed because resources it modifies are in use - close the app and retry (ERROR_PACKAGES_IN_USE)",
}


def explain_code(code: int) -> str:
    """A sentence for an HRESULT, or "" when nothing reliable is known."""
    code &= 0xFFFFFFFF
    if code in EXTRA_CODES:
        return EXTRA_CODES[code]
    text = decode_wu_error(code)
    if " - " in text:
        return text.split(" - ", 1)[1]
    return ""


def codes_in(text: str) -> List[int]:
    return [int(m.group(1), 16) for m in _HRESULT.finditer(text or "")]


def failing_code(entry: LogEntry) -> Optional[int]:
    """The HRESULT for a failing entry: the parsed field, else one named in the text."""
    raw = entry.raw or {}
    code = raw.get("hresult")
    if isinstance(code, int) and code & 0x80000000:
        return code
    found = [c for c in codes_in(entry.message) if c & 0x80000000]
    return found[0] if found else None


def failed_update_name(entry: LogEntry) -> str:
    """The update named in a failure message ("2026-09 Preview Update (KB5124010) ...")."""
    m = _FAILED_UPDATE.search(entry.message or "")
    return m.group(1).strip() if m else ""


def kb_of(text: str) -> str:
    m = _KB.search(text or "")
    return m.group(0).upper() if m else ""


def failures(entries: List[LogEntry]) -> List[Tuple[str, int, str, int]]:
    """(update, code, meaning, times) for each distinct failing update, newest first."""
    grouped: "OrderedDict[Tuple[str, int], int]" = OrderedDict()
    for e in sorted((x for x in entries if x.level == "Error"), key=lambda x: x.timestamp, reverse=True):
        code = failing_code(e)
        if code is None:
            continue
        name = failed_update_name(e) or e.message[:80]
        grouped[(name, code)] = grouped.get((name, code), 0) + 1
    return [(name, code, explain_code(code), n) for (name, code), n in grouped.items()]


def summary_text(entries: List[LogEntry]) -> str:
    if not entries:
        return "No Windows Update reporting events were found."
    installs = sum(1 for e in entries if (e.raw or {}).get("event_name") == "AGENT_INSTALLING_SUCCEEDED")
    fails = failures(entries)
    span = f"{min(e.timestamp for e in entries):%Y-%m-%d} to {max(e.timestamp for e in entries):%Y-%m-%d}"
    text = f"{len(entries)} events ({span}); {installs} successful install(s), {sum(f[3] for f in fails)} failed."
    if fails:
        head = "; ".join(_fmt_failure(f) for f in fails[:4])
        text += " Failing: " + head + (f"; and {len(fails) - 4} more." if len(fails) > 4 else ".")
    return text


def _fmt_failure(f: Tuple[str, int, str, int]) -> str:
    name, code, meaning, times = f
    tail = f" - {meaning}" if meaning else ""
    return f"{name} (0x{code:08X}{tail}){' x' + str(times) if times > 1 else ''}"


def detail_html(entry: LogEntry) -> str:
    code = failing_code(entry)
    if code is None:
        return ""
    meaning = explain_code(code)
    out = f"<b>Error 0x{code:08X}</b><br>"
    out += html.escape(meaning) + "<br>" if meaning else "<i>No documented meaning is known for this code.</i><br>"
    name = failed_update_name(entry)
    if name:
        out += f"<i>Update:</i> {html.escape(name)}<br>"
    return out + "<hr>"
