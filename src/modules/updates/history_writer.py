"""Run-history log for Update Center-style maintenance runs.

Stores a capped JSON array at {app_data_dir}/updates/history.json — mirrors
Update Center's Add-UcHistory (ts, freed, updates, wg fields), so the
maintenance report and any "last run" summary can read a consistent shape.
"""
import json
import logging
import os
from datetime import datetime
from typing import List

logger = logging.getLogger(__name__)

MAX_ENTRIES = 200


def _history_path(app_data_dir: str) -> str:
    updates_dir = os.path.join(app_data_dir, "updates")
    os.makedirs(updates_dir, exist_ok=True)
    return os.path.join(updates_dir, "history.json")


def load_history(app_data_dir: str) -> List[dict]:
    path = _history_path(app_data_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        logger.warning("Could not read history.json", exc_info=True)
        return []


def append_run(
    app_data_dir: str,
    freed_bytes: int = 0,
    updates_installed: int = 0,
    winget_count: int = 0,
    pending_restart: int = 0,
) -> None:
    """Append one run entry, capped at the most recent MAX_ENTRIES."""
    history = load_history(app_data_dir)
    history.append({
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "freed": int(freed_bytes),
        "updates": int(updates_installed),
        "wg": int(winget_count),
        "pending_restart": int(pending_restart),
    })
    if len(history) > MAX_ENTRIES:
        history = history[-MAX_ENTRIES:]
    try:
        with open(_history_path(app_data_dir), "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception:
        logger.warning("Could not write history.json", exc_info=True)
