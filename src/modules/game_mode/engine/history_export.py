"""Filter, aggregate, annotate and export Game Mode history (Qt-free)."""
from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime
from typing import Iterable, List, Optional

CSV_HEADERS = ["Started", "Ended", "Duration (min)", "Preset", "Actions OK",
               "Actions failed", "Services stopped", "Apps closed", "Tasks disabled",
               "Handles before", "Handles after", "Processes before", "Processes after",
               "Restore status", "Restore issues", "Note"]


def _restore_text(entry) -> str:
    if entry.get("restore_ok") is True:
        return "Fully restored"
    if entry.get("restore_ok") is False:
        return "Partial" if entry.get("restore_restored") else "Failed"
    return "Not restored"


def filter_sessions(entries: Iterable[dict], *, query: str = "", preset: str = "",
                    restore: str = "", days: Optional[int] = None,
                    now: Optional[float] = None) -> List[dict]:
    now = time.time() if now is None else now
    cutoff = now - days * 86400 if days else None
    text = (query or "").strip().casefold()
    rows = []
    for entry in entries or []:
        started = entry.get("started")
        if cutoff is not None and (started is None or started < cutoff):
            continue
        if preset and str(entry.get("preset", "")) != preset:
            continue
        if restore and _restore_text(entry).casefold() != restore.casefold():
            continue
        if text:
            haystack = " ".join((str(entry.get("preset", "")), str(entry.get("note", "")),
                                 _restore_text(entry),
                                 " ".join(str(i) for i in entry.get("restore_issues", [])))).casefold()
            if text not in haystack:
                continue
        rows.append(entry)
    return rows


def aggregates(entries) -> dict:
    rows = list(entries or [])
    presets: dict = {}
    failures: dict = {}
    for entry in rows:
        presets[str(entry.get("preset", "Unknown"))] = presets.get(str(entry.get("preset", "Unknown")), 0) + 1
        for issue in entry.get("restore_issues", []) or []:
            failures[issue] = failures.get(issue, 0) + 1
    durations = [e["duration_s"] for e in rows
                 if isinstance(e.get("duration_s"), (int, float))]
    restored = [e for e in rows if e.get("restore_ok") is not None]
    return {
        "sessions": len(rows),
        "by_preset": presets,
        "median_duration_s": sorted(durations)[len(durations) // 2] if durations else None,
        "success_rate": (100 * sum(e.get("restore_ok") is True for e in restored) / len(restored)
                         if restored else None),
        "recurring_restore_issues": sorted(failures.items(), key=lambda item: -item[1])[:10],
        "small_sample": len(rows) < 5,
    }


def _date(value) -> str:
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError, TypeError):
        return ""


def to_csv(entries) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for entry in entries or []:
        writer.writerow([
            _date(entry.get("started")), _date(entry.get("ended")),
            int(entry.get("duration_s") or 0), entry.get("preset", ""),
            entry.get("actions_ok", 0), entry.get("actions_failed", 0),
            entry.get("services_stopped", 0), entry.get("apps_closed", 0),
            entry.get("tasks_disabled", 0), entry.get("handles_before", ""),
            entry.get("handles_after", ""), entry.get("processes_before", ""),
            entry.get("processes_after", ""), _restore_text(entry),
            " | ".join(entry.get("restore_issues", []) or []), entry.get("note", ""),
        ])
    return output.getvalue()


def to_json(entries) -> str:
    return json.dumps(list(entries or []), indent=2, ensure_ascii=False)


def set_note(entries, started, note: str) -> List[dict]:
    rows = [dict(entry) for entry in entries or []]
    text = (note or "").strip()[:500]
    for entry in rows:
        if entry.get("started") == started:
            entry["note"] = text
            break
    return rows


def trim(entries, retention: int = 100) -> List[dict]:
    rows = list(entries or [])
    keep = max(1, min(int(retention or 100), 1000))
    return rows[-keep:]
