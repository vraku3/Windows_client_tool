"""Persistent, bounded Game Mode session summaries. No Windows or Qt calls."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()
MAX_ENTRIES = 100


def _path(app_data_dir, folder=None) -> Path:
    return (Path(folder) if folder is not None else Path(app_data_dir) / "game_mode") / "session_history.json"


def load_history(app_data_dir, *, folder=None) -> list[dict]:
    with _LOCK:
        try:
            with _path(app_data_dir, folder).open(encoding="utf-8") as stream:
                entries = json.load(stream)
        except FileNotFoundError:
            return []
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ValueError("Game Mode history must be an array of session objects")
        for entry in entries:
            started = entry.get("started")
            if type(started) not in (int, float) or not 0 <= started < float("inf"):
                raise ValueError("Game Mode history contains an invalid start time")
            for key in ("ended", "duration_s", "actions_total", "actions_ok", "actions_failed",
                        "actions_skipped", "services_stopped", "apps_closed", "tasks_disabled",
                        "handles_before", "handles_after", "processes_before", "processes_after",
                        "restore_restored"):
                value = entry.get(key)
                if value is not None and (type(value) not in (int, float)
                                          or not 0 <= value < float("inf")):
                    raise ValueError(f"Game Mode history contains an invalid {key}")
            if entry.get("restore_ok") is not None and type(entry["restore_ok"]) is not bool:
                raise ValueError("Game Mode history contains an invalid restore status")
            for key, fields in (("actions", ("kind", "label", "status")),
                                ("on_report", ("level", "text"))):
                records = entry.get(key, [])
                if not isinstance(records, list) or any(
                        not isinstance(r, dict) or any(not isinstance(r.get(f), str) for f in fields)
                        for r in records):
                    raise ValueError(f"Game Mode history contains invalid {key}")
            issues = entry.get("restore_issues", [])
            if not isinstance(issues, list) or any(not isinstance(i, str) for i in issues):
                raise ValueError("Game Mode history contains invalid restore issues")
        return entries[-MAX_ENTRIES:]


def _write(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(entries[-MAX_ENTRIES:], stream, indent=1, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.remove(temporary)


def record_session_start(app_data_dir, preset, actions_total, handles, processes, *,
                         started=None, actions=None, report=None, handles_after=None,
                         processes_after=None, folder=None) -> None:
    with _LOCK:
        entries = load_history(app_data_dir, folder=folder)
        started = time.time() if started is None else started
        entry = next((e for e in entries if e["started"] == started), None)
        if entry is None:
            entry = dict(started=started, ended=None, duration_s=None, restore_ok=None,
                         restore_issues=[], restore_restored=0)
            entries.append(entry)
        summary = [{"kind": e.get("kind", ""),
                    "label": e.get("label") or e.get("name") or e.get("exe") or e.get("path") or e.get("kind", "Change"),
                    "status": "ok" if e.get("done") else "failed"} for e in (actions or [])]
        messages = [{"level": r["level"], "text": r["text"]} for r in (report or [])]
        ok = sum(e["status"] == "ok" for e in summary)
        failed = max(sum(e["status"] == "failed" for e in summary),
                     sum(r["level"] == "bad" for r in messages))
        skipped = sum(r["level"] in ("warn", "info") for r in messages)
        entry.update(preset=preset, actions_total=max(actions_total, ok + failed + skipped),
                     actions_ok=ok, actions_failed=failed, actions_skipped=skipped,
                     services_stopped=sum(e["kind"] == "service_stop" and e["status"] == "ok" for e in summary),
                     apps_closed=sum(e["kind"] == "app_close" and e["status"] == "ok" for e in summary),
                     tasks_disabled=sum(e["kind"] == "task" and e["status"] == "ok" for e in summary),
                     handles_before=handles, processes_before=processes,
                     handles_after=handles_after, processes_after=processes_after,
                     actions=summary, on_report=messages)
        _write(_path(app_data_dir, folder), entries)


def record_session_end(app_data_dir, report, handles, processes, *, started=None,
                       complete=True, restored=0, folder=None) -> None:
    with _LOCK:
        entries = load_history(app_data_dir, folder=folder)
        entry = next((e for e in reversed(entries) if
                      (e["started"] == started if started is not None else e.get("ended") is None)), None)
        if entry is None:
            return
        issues = [r["text"] for r in report if r.get("level") in ("bad", "warn")]
        entry.update(restore_ok=complete and not issues, restore_issues=issues,
                     restore_restored=entry.get("restore_restored", 0) + restored)
        if complete:
            ended = entry.get("ended") or time.time()
            entry.update(ended=ended, duration_s=max(0, int(ended - entry["started"])))
        # Preserve ON's measured reduction; OFF counts include reopened programs.
        if entry.get("handles_after") is None:
            entry["handles_after"] = handles
        if entry.get("processes_after") is None:
            entry["processes_after"] = processes
        _write(_path(app_data_dir, folder), entries)


def session_stats(history) -> dict:
    durations = [e["duration_s"] for e in history if e.get("ended") is not None
                 and isinstance(e.get("duration_s"), (int, float))]
    restored = [e for e in history if e.get("restore_ok") is not None]
    return {"total_sessions": len(history),
            "average_duration_s": sum(durations) / len(durations) if durations else None,
            "success_rate": 100 * sum(e["restore_ok"] is True for e in restored) / len(restored) if restored else None}
