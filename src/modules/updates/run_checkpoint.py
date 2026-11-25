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
        "completed_stages": [],
        "current_stage": "",
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


def stage_start(app_data_dir, stage: str) -> bool:
    """Record that a stage has begun execution."""
    if not app_data_dir:
        return False
    data = load(app_data_dir)
    if data is None or data.get("status") != "running":
        return False
    data["current_stage"] = str(stage)[:30]
    return _write(checkpoint_path(app_data_dir), data)


def stage_complete(app_data_dir, stage: str) -> bool:
    """Record that a stage completed execution."""
    if not app_data_dir:
        return False
    data = load(app_data_dir)
    if data is None or data.get("status") != "running":
        return False
    completed = data.setdefault("completed_stages", [])
    s = str(stage)[:30]
    if s not in completed:
        completed.append(s)
    return _write(checkpoint_path(app_data_dir), data)


def remaining_stages(data: Optional[dict]) -> List[str]:
    """Return the list of planned stages that have not completed."""
    if not data:
        return []
    stages = [str(s) for s in data.get("stages") or []]
    completed = set(data.get("completed_stages") or [])
    if "wu" not in completed and "wu" in stages:
        items = data.get("items") or {}
        wu_items = [v for k, v in items.items() if str(k).startswith("wu:") and isinstance(v, dict)]
        if wu_items and all(v.get("phase") in ("installed", "installed_after_reboot") for v in wu_items):
            completed.add("wu")
    return [s for s in stages if s not in completed]


def get_installed_hotfixes() -> set[str]:
    """Return set of installed HotFix IDs on Windows (e.g. {'KB5132061', ...})."""
    try:
        import ctypes
        hr = ctypes.windll.ole32.CoInitialize(None)
        try:
            import win32com.client
            wmi = win32com.client.GetObject("winmgmts:")
            return {str(obj.HotFixID).upper() for obj in wmi.InstancesOf("Win32_QuickFixEngineering") if obj.HotFixID}
        finally:
            if hr >= 0:
                ctypes.windll.ole32.CoUninitialize()
    except Exception as exc:
        logger.debug("Win32_QuickFixEngineering query failed: %s", exc)
    try:
        import subprocess
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "Get-HotFix | Select-Object -ExpandProperty HotFixID"],
            text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        return {kb.strip().upper() for kb in out.split() if kb.strip()}
    except Exception:
        return set()


def reconcile_checkpoint(app_data_dir, installed_kbs: Optional[set[str]] = None) -> Optional[dict]:
    """Reconcile an unfinished checkpoint after restart or crash.

    If WU items recorded as 'started' are now installed, updates their status.
    If all stages are now satisfied, marks run as completed_after_reboot.
    Returns the updated checkpoint data if reconciled, or None.
    """
    if not app_data_dir:
        return None
    data = unfinished(app_data_dir)
    if data is None:
        return None

    items = data.get("items") or {}
    wu_items = {k: v for k, v in items.items() if str(k).startswith("wu:") and isinstance(v, dict)}
    changed = False

    if wu_items and any(v.get("phase") in ("started", "reboot_required") for v in wu_items.values()):
        if installed_kbs is None:
            installed_kbs = get_installed_hotfixes()

        import re
        for k, v in wu_items.items():
            if v.get("phase") in ("started", "reboot_required"):
                m = re.search(r"KB\d+", k + " " + str(v.get("detail") or ""), re.IGNORECASE)
                if m and m.group(0).upper() in installed_kbs:
                    v["phase"] = "installed_after_reboot"
                    v["detail"] = "Installed successfully (verified on OS)"
                    changed = True

    completed_stages = data.setdefault("completed_stages", [])
    if wu_items and all(v.get("phase") in ("installed", "installed_after_reboot") for v in wu_items.values()):
        if "wu" not in completed_stages:
            completed_stages.append("wu")
            changed = True

    stages = data.get("stages") or []
    remaining = [s for s in stages if s not in completed_stages]

    if stages and not remaining:
        data["status"] = "completed_after_reboot"
        data["finished"] = _now()
        _write(checkpoint_path(app_data_dir), data)
        return data

    if changed:
        _write(checkpoint_path(app_data_dir), data)
        return data

    return None


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
