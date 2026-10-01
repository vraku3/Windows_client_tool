"""Persists the last asset record built on this machine, so the Asset Record
tab can flag hardware that changed since the previous time this app ran --
a swapped drive, an added/removed DIMM, a different monitor plugged in.

Mirrors ``modules/system_health/history.py``'s shape: a small JSON file under
the app's own data dir, read/write guarded so a corrupt or missing file never
raises -- a reader gone wrong here must not break the Asset Record tab, it
must just mean "no drift to report yet."
"""
import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _snapshot_path(app_data_dir: str) -> str:
    subdir = os.path.join(app_data_dir, "hardware_inventory")
    os.makedirs(subdir, exist_ok=True)
    return os.path.join(subdir, "last_asset_snapshot.json")


def load_snapshot(app_data_dir: str) -> Optional[Dict[str, str]]:
    """The last saved record, or None (first run, or the file is unreadable)."""
    path = _snapshot_path(app_data_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("Could not read hardware_inventory snapshot at %s", path, exc_info=True)
        return None


def save_snapshot(app_data_dir: str, record: Dict[str, str]) -> None:
    path = _snapshot_path(app_data_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.warning("Could not write hardware_inventory snapshot at %s", path, exc_info=True)
