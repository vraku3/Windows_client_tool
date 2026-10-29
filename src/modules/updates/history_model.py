"""Searchable Windows Update history model (Qt-free)."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

_KB = re.compile(r"\bKB(\d{6,8})\b", re.I)

RESULT_LABELS = {0: "Not started", 1: "In progress", 2: "Succeeded",
                 3: "Succeeded with errors", 4: "Failed", 5: "Cancelled"}
OPERATION_LABELS = {1: "Install", 2: "Uninstall"}
FAILED_CODES = (3, 4, 5)


def result_label(code: int) -> str:
    return RESULT_LABELS.get(int(code), f"Result {code}")


def operation_label(operation: int) -> str:
    return OPERATION_LABELS.get(int(operation), f"Operation {operation}")


def kb_of_title(title: str) -> str:
    """The first KB number in an update title, prefixed; '' when there is none."""
    match = _KB.search(str(title or ""))
    return f"KB{match.group(1)}" if match else ""


def uninstallable(entry) -> bool:
    """Only a succeeded, still-known installation of a KB can be offered."""
    return (int(getattr(entry, "operation", 1) or 1) == 1
            and int(getattr(entry, "result_code", 0) or 0) in (2, 3)
            and bool(kb_of_title(getattr(entry, "title", ""))))


def detail_text(entry) -> str:
    """Decoded HRESULT for a failed/unfinished entry; empty otherwise."""
    code = int(getattr(entry, "result_code", 0) or 0)
    hresult = int(getattr(entry, "hresult", 0) or 0)
    if code not in FAILED_CODES or not hresult:
        return ""
    try:
        from core.wu_error_codes import decode_wu_error
        return decode_wu_error(hresult)
    except Exception:
        return f"0x{hresult & 0xFFFFFFFF:08X}"


def filter_entries(entries: Iterable, query: str = "", days: Optional[int] = None,
                   now: Optional[datetime] = None) -> List:
    """Newest first, matching `query` in the title, KB or decoded detail."""
    text = (query or "").strip().casefold()
    cutoff = None
    if days:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    rows = []
    for entry in entries:
        when = getattr(entry, "when", None)
        if cutoff is not None and when is not None and when < cutoff:
            continue
        if text:
            haystack = " ".join((str(getattr(entry, "title", "")),
                                 result_label(getattr(entry, "result_code", 0)),
                                 detail_text(entry))).casefold()
            if text not in haystack:
                continue
        rows.append(entry)
    return sorted(rows, key=lambda e: getattr(e, "when", datetime.min.replace(tzinfo=timezone.utc)),
                  reverse=True)


CSV_HEADERS = ["Date", "Operation", "Update", "KB", "Result", "HRESULT", "Detail"]


def _csv_row(entry) -> List[str]:
    when = getattr(entry, "when", None)
    hresult = int(getattr(entry, "hresult", 0) or 0)
    return [
        when.astimezone().strftime("%Y-%m-%d %H:%M:%S") if when else "",
        operation_label(getattr(entry, "operation", 1)),
        str(getattr(entry, "title", "") or ""),
        kb_of_title(getattr(entry, "title", "")),
        result_label(getattr(entry, "result_code", 0)),
        f"0x{hresult & 0xFFFFFFFF:08X}" if hresult else "",
        detail_text(entry),
    ]


def entries_to_csv(entries: Iterable) -> str:
    """The filtered history as CSV text (header row included).

    `csv` owns quoting, so an update title with a comma or quote exports
    intact instead of shifting columns. Callers write it as UTF-8 with BOM so
    Excel opens non-ASCII titles correctly.
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for entry in entries:
        writer.writerow(_csv_row(entry))
    return output.getvalue()


def paginate(rows: List, page: int, page_size: int = 100):
    """(page rows, current page, total pages); page is clamped to the data."""
    size = max(1, int(page_size))
    pages = max(1, (len(rows) + size - 1) // size)
    current = min(max(0, int(page)), pages - 1)
    start = current * size
    return rows[start:start + size], current, pages
