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


#: The events that settle what happened to one update, by its update id.
_OUTCOME = {
    "AGENT_DOWNLOAD_FAILED": False, "AGENT_INSTALLING_FAILED": False,
    "AGENT_DOWNLOAD_SUCCEEDED": True, "AGENT_INSTALLING_SUCCEEDED": True,
}
_STORE_APP = re.compile(r"^9[A-Z0-9]{11}-")
_NAMED_UPDATE = re.compile(r"following update(?: with error 0x[0-9a-fA-F]+)?:\s*(.+?)\.?\s*$",
                           re.IGNORECASE)


def is_store_app(name: str) -> bool:
    """Store packages are titled by their product id: '9NKSQGP7F2NH-5319275A.WhatsAppDesktop'."""
    return bool(_STORE_APP.match(name or ""))


def _update_id(entry: LogEntry) -> str:
    raw = entry.raw if isinstance(entry.raw, dict) else {}
    return str(raw.get("update_id") or "")


def _names_by_id(entries: List[LogEntry]) -> dict:
    """update id -> title, from any line that names it. Download failures carry
    no title at all, so without this they were named after the CLIENT that
    asked ('Update;MoUpdateOrchestratorDeviceScan-...') -- 14 of 25 here."""
    names: dict = {}
    for e in entries:
        uid = _update_id(e)
        m = _NAMED_UPDATE.search(e.message or "")
        if uid and m and uid not in names:
            names[uid] = m.group(1).strip()
    return names


def _latest_outcome(entries: List[LogEntry]) -> dict:
    latest: dict = {}
    for e in sorted(entries, key=lambda x: x.timestamp):
        raw = e.raw if isinstance(e.raw, dict) else {}
        outcome = _OUTCOME.get(raw.get("event_name"))
        if outcome is not None and _update_id(e):
            latest[_update_id(e)] = outcome
    return latest


def _grouped_failures(entries: List[LogEntry]) -> "OrderedDict[Tuple[str, int], list]":
    names = _names_by_id(entries)
    grouped: "OrderedDict[Tuple[str, int], list]" = OrderedDict()
    for e in sorted((x for x in entries if x.level == "Error"), key=lambda x: x.timestamp, reverse=True):
        code = failing_code(e)
        if code is None:
            continue
        uid = _update_id(e)
        name = failed_update_name(e) or names.get(uid) or (f"update {uid}" if uid else e.message[:80])
        grouped.setdefault((name, code), []).append(uid)
    return grouped


def failures(entries: List[LogEntry]) -> List[Tuple[str, int, str, int]]:
    """(update, code, meaning, times) for each update STILL failing, newest first.

    An update whose latest download/install outcome in the log succeeded is
    left out: measured 2026-10-09, 8 of the 14 updates with failures here
    had since succeeded (HTTP 503s retried within the hour, a Gaming Services
    install 7 s later), and listing them made a healthy machine read as one
    whose updates were broken. A line with no update id cannot be checked,
    so it stays in.
    """
    latest = _latest_outcome(entries)
    out = []
    for (name, code), uids in _grouped_failures(entries).items():
        # Every attempt in the group must have since succeeded: WhatsApp's
        # 2026-09-29 version installed, its 2026-10-08 one (another id) did not.
        if all(uid and latest.get(uid) is True for uid in uids):
            continue
        out.append((name, code, explain_code(code), len(uids)))
    return out


def resolved_count(entries: List[LogEntry]) -> int:
    """Updates that failed at least once and have since succeeded."""
    latest = _latest_outcome(entries)
    return len({uid for uids in _grouped_failures(entries).values() for uid in uids
                if uid and latest.get(uid) is True})


def summary_text(entries: List[LogEntry]) -> str:
    if not entries:
        return "No Windows Update reporting events were found."
    installs = sum(1 for e in entries if (e.raw or {}).get("event_name") == "AGENT_INSTALLING_SUCCEEDED")
    fails = failures(entries)
    resolved = resolved_count(entries)
    span = f"{min(e.timestamp for e in entries):%Y-%m-%d} to {max(e.timestamp for e in entries):%Y-%m-%d}"
    text = f"{len(entries)} events ({span}); {installs} successful install(s)."
    later = f" {resolved} update(s) failed and later succeeded." if resolved else ""
    if not fails:
        return text + " Nothing is failing now." + later
    system = [f for f in fails if not is_store_app(f[0])]
    store = [f for f in fails if is_store_app(f[0])]
    if system:
        head = "; ".join(_fmt_failure(f) for f in system[:4])
        text += (f" Still failing: {head}"
                 + (f"; and {len(system) - 4} more." if len(system) > 4 else "."))
    if store:
        in_use = sum(1 for f in store if f[1] & 0xFFFFFFFF == 0x80073D02)
        text += (f" {len(store)} Store app(s) not updated"
                 + (f" ({in_use} because the app was running; they retry once it is closed)" if in_use else "")
                 + ": " + ", ".join(f[0].split("-", 1)[1] for f in store[:5])
                 + (" ..." if len(store) > 5 else "") + ".")
    return text + later


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
