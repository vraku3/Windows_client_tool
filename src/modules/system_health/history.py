"""System Health's own run-history log. Mirrors
modules/updates/history_writer.py's shape (a capped JSON array at
{app_data_dir}/<subfolder>/history.json) -- a separate file rather than
extending Update Center's own history, since this module's entries
({ts, action, command, returncode, findings_count}) don't fit Update
Center's ({ts, freed, updates, wg}) shape, and mixing unrelated history
streams into one file is worse than two small, separately-typed ones.
"""
import json
import logging
import os
from datetime import datetime
from typing import List

logger = logging.getLogger(__name__)

MAX_ENTRIES = 200


def _history_path(app_data_dir: str) -> str:
    health_dir = os.path.join(app_data_dir, "system_health")
    os.makedirs(health_dir, exist_ok=True)
    return os.path.join(health_dir, "history.json")


def load_history(app_data_dir: str) -> List[dict]:
    path = _history_path(app_data_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        logger.warning("Could not read system_health history.json", exc_info=True)
        return []


def append_run(app_data_dir: str, *, action: str, command: str,
               returncode: int, findings_count: int = 0) -> None:
    history = load_history(app_data_dir)
    history.append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "command": command,
        "returncode": returncode,
        "findings_count": findings_count,
    })
    history = history[-MAX_ENTRIES:]
    path = _history_path(app_data_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception:
        logger.warning("Could not write system_health history.json", exc_info=True)
