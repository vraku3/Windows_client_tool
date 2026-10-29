"""Check and repair drift from an active Game Mode journal (Qt-free)."""
from __future__ import annotations

from typing import List

OK, DRIFT, UNKNOWN = "ok", "drift", "unknown"


def audit(system, actions: List[dict]) -> List[dict]:
    rows = []
    for entry in actions or []:
        if not entry.get("done"):
            continue
        kind = entry.get("kind")
        label = entry.get("label") or entry.get("name") or entry.get("exe") or kind or "change"
        if kind == "service_stop":
            state = system.service(entry.get("name", ""))
            actual = None if state is None or state.running is None else (
                "Running" if state.running else "Stopped")
            expected = "Stopped"
        elif kind == "task":
            enabled = system.task_enabled(entry.get("path", ""))
            actual = None if enabled is None else ("Enabled" if enabled else "Disabled")
            expected = "Disabled"
        elif kind == "plan":
            actual = system.active_plan()
            expected = entry.get("after")
        elif kind == "registry":
            if "after" not in entry:
                rows.append({"kind": kind, "label": label, "expected": "",
                             "actual": "", "status": UNKNOWN, "entry": entry})
                continue
            exists, value, _kind = system.reg_get(entry.get("path", ""), entry.get("name", ""),
                                                  entry.get("hive", "HKCU"))
            actual = None if exists is None else (value if exists else "<absent>")
            expected = entry.get("after")
        else:
            rows.append({"kind": kind, "label": label, "expected": "",
                         "actual": "", "status": UNKNOWN, "entry": entry})
            continue
        if actual is None:
            status = UNKNOWN
        elif actual == expected:
            status = OK
        else:
            status = DRIFT
        rows.append({"kind": kind, "label": label, "expected": expected,
                     "actual": actual, "status": status, "entry": entry})
    return rows


def repair(system, rows: List[dict]) -> List[dict]:
    """Re-apply only drifting rows; returns the same rows with a repair note."""
    for row in rows:
        if row.get("status") != DRIFT:
            continue
        entry = row.get("entry") or {}
        kind = row.get("kind")
        error = None
        if kind == "service_stop":
            error = system.stop_service(entry.get("name", ""))
        elif kind == "task":
            error = system.set_task_enabled(entry.get("path", ""), False)
        elif kind == "plan":
            error = system.set_plan(entry.get("after", ""))
        elif kind == "registry":
            error = system.reg_set(entry.get("path", ""), entry.get("name", ""),
                                   entry.get("after"), entry.get("type", 4),
                                   entry.get("hive", "HKCU"))
        row["repaired"] = not error
        row["repair_error"] = error or ""
    return rows
