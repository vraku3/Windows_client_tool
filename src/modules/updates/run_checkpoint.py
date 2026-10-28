"""Durable per-run checkpoints for maintenance operations (Qt-free).

A run writes its intent before the first change and each item's transition
as it happens, so a crash, a power loss or a killed installer leaves evidence
of what was being attempted instead of an empty log. The file survives until a
later run replaces it; ``status`` tells the next launch whether the previous
run finished. Nothing here raises into the operation it is watching.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

MAX_ITEMS = 500
MAX_DETAIL = 300


def checkpoint_path(app_data_dir) -> Path:
    return Path(app_data_dir) / "updates" / "run_checkpoint.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write(path: Path, data: dict) -> bool:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix="run_checkpoint.", suffix=".tmp",
                                         delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return True
    except (OSError, TypeError, ValueError):
        logger.warning("Could not write the maintenance run checkpoint", exc_info=True)
        return False
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove a temporary run checkpoint", exc_info=True)


def begin(app_data_dir, stages: Iterable[str], context: str = "run_all") -> Optional[str]:
    """Start a run checkpoint and return its id, or None if it cannot be written."""
    if not app_data_dir:
        return None
    data = {
        "run_id": uuid4().hex,
        "started": _now(),
        "started_ts": datetime.now().timestamp(),
        "pid": os.getpid(),
        "context": str(context)[:60],
        "stages": [str(s) for s in stages],
        "status": "running",
        "finished": None,
        "error": "",
        "items": {},
    }
    return data["run_id"] if _write(checkpoint_path(app_data_dir), data) else None


def load(app_data_dir) -> Optional[dict]:
    """The last checkpoint, or None when absent or unreadable."""
    if not app_data_dir:
        return None
    path = checkpoint_path(app_data_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Could not read the maintenance run checkpoint", exc_info=True)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        logger.warning("Ignoring a malformed maintenance run checkpoint")
        return None
    return data


def item(app_data_dir, item_id: str, phase: str, detail: str = "") -> bool:
    """Record one transition. No checkpoint means no run: never invent one."""
    if not app_data_dir:
        return False
    data = load(app_data_dir)
    if data is None or data.get("status") != "running":
        return False
    items = data["items"]
    items[str(item_id)[:120]] = {
        "phase": str(phase)[:40],
        "detail": str(detail or "")[:MAX_DETAIL],
        "at": _now(),
    }
    while len(items) > MAX_ITEMS:
        items.pop(next(iter(items)))
    return _write(checkpoint_path(app_data_dir), data)


def finish(app_data_dir, status: str = "completed", error: str = "") -> bool:
    if not app_data_dir:
        return False
    data = load(app_data_dir)
    if data is None:
        return False
    data["status"] = str(status)[:30]
    data["error"] = str(error or "")[:MAX_DETAIL]
    data["finished"] = _now()
    return _write(checkpoint_path(app_data_dir), data)


def clear(app_data_dir) -> None:
    if not app_data_dir:
        return
    try:
        checkpoint_path(app_data_dir).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove the maintenance run checkpoint", exc_info=True)


def _owner_alive(pid, started_ts: float) -> bool:
    try:
        import psutil
    except ImportError:                       # pragma: no cover - psutil is required
        return True
    try:
        process = psutil.Process(int(pid))
        # A recycled PID belongs to a process that started after this run.
        if started_ts and process.create_time() > float(started_ts) + 1:
            return False
        return process.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, TypeError):
        return False


def unfinished(app_data_dir) -> Optional[dict]:
    """A run still marked 'running' whose owner is gone: it never finished."""
    if not app_data_dir:
        return None
    data = load(app_data_dir)
    if data is None or data.get("status") != "running":
        return None
    if _owner_alive(data.get("pid"), data.get("started_ts") or 0):
        return None
    return data


def item_summary(data: dict) -> List[str]:
    """One line per recorded item, for a confirmation dialog."""
    rows = []
    for item_id, entry in (data.get("items") or {}).items():
        if not isinstance(entry, dict):
            continue
        phase = entry.get("phase") or "recorded"
        rows.append(f"{item_id}: {phase}" + (f" — {entry['detail']}" if entry.get("detail") else ""))
    return rows
