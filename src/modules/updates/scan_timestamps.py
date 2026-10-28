"""Persist per-source successful scan timestamps without Qt dependencies."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

logger = logging.getLogger(__name__)
_lock = RLock()


def _parse(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def load_timestamps(app_data_dir) -> dict:
    with _lock:
        try:
            data = json.loads((Path(app_data_dir) / "updates" / "scan_timestamps.json").read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Expected a source mapping")
            return {source: entry for source, entry in data.items()
                    if isinstance(entry, dict) and entry.get("ok") is True
                    and _parse(entry.get("ts")) is not None
                    and isinstance(entry.get("count"), int) and entry["count"] >= 0}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Could not load scan timestamps: %s", exc)
            return {}


def record_scan(app_data_dir, source, ok, count):
    if source not in ("wu", "winget", "store"):
        raise ValueError(f"Unknown scan source: {source}")
    if not ok:
        return
    with _lock:
        data = load_timestamps(app_data_dir)
        data[source] = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "ok": True, "count": count}
        directory = Path(app_data_dir) / "updates"
        directory.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             suffix=".tmp", delete=False) as handle:
                temp_path = Path(handle.name)
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, directory / "scan_timestamps.json")
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()


def elapsed_text(ts_str) -> str:
    timestamp = _parse(ts_str)
    if timestamp is None:
        return "unknown"
    seconds = max(0, (datetime.now(timezone.utc) - timestamp).total_seconds())
    for unit, length in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= length:
            amount = int(seconds // length)
            return f"{amount} {unit}{'s' if amount != 1 else ''} ago"
    return "just now"


def is_stale(ts_str, max_hours=4) -> bool:
    timestamp = _parse(ts_str)
    return timestamp is None or (datetime.now(timezone.utc) - timestamp).total_seconds() > max_hours * 3600
