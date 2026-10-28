"""Per-source scan snapshots and "what changed since last scan" (Qt-free).

The scan already knows which packages were offered; keeping the previous
successful snapshot turns "12 updates" into "2 new, 1 changed, 1 no longer
offered". Only successful scans are recorded -- a failed scan must never look
like everything disappeared.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

MAX_SNAPSHOTS = 50


def history_path(app_data_dir) -> Path:
    return Path(app_data_dir) / "updates" / "scan_history.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _value(offer, name: str) -> str:
    if isinstance(offer, dict):
        return str(offer.get(name) or "")
    return str(getattr(offer, name, "") or "")


def normalize(offers: Iterable) -> List[dict]:
    """One stable, comparable row per offer; the id is the identity."""
    rows, seen = [], set()
    for offer in offers or []:
        ident = _value(offer, "winget_id") or _value(offer, "kb") or _value(offer, "id")
        name = _value(offer, "name") or _value(offer, "title") or ident
        if not ident:
            continue
        key = ident.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"id": ident, "name": name,
                     "installed": _value(offer, "installed_version"),
                     "offered": _value(offer, "available_version")})
    return rows


def changes(previous: Optional[List[dict]], current: List[dict]) -> dict:
    """New / changed / no-longer-offered, comparing by id."""
    if previous is None:
        return {"first": True, "new": [], "changed": [], "gone": []}
    before = {row["id"].casefold(): row for row in previous if isinstance(row, dict) and row.get("id")}
    after = {row["id"].casefold(): row for row in current}
    new = [row for key, row in after.items() if key not in before]
    changed = []
    for key, row in after.items():
        old = before.get(key)
        if old is not None and (old.get("offered") or "") != (row.get("offered") or ""):
            changed.append({**row, "was": old.get("offered") or "unknown"})
    gone = [row for key, row in before.items() if key not in after]
    return {"first": False, "new": new, "changed": changed, "gone": gone}


def _read(app_data_dir) -> dict:
    if not app_data_dir:
        return {"sources": {}}
    try:
        data = json.loads(history_path(app_data_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"sources": {}}
    except (OSError, ValueError):
        logger.warning("Could not read the scan history", exc_info=True)
        return {"sources": {}}
    sources = data.get("sources") if isinstance(data, dict) else None
    return {"sources": sources if isinstance(sources, dict) else {}}


def _write(app_data_dir, data: dict) -> bool:
    if not app_data_dir:
        return False
    path = history_path(app_data_dir)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix="scan_history.", suffix=".tmp",
                                         delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return True
    except (OSError, TypeError, ValueError):
        logger.warning("Could not write the scan history", exc_info=True)
        return False
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove a temporary scan history file", exc_info=True)


def record(app_data_dir, source: str, offers: Iterable) -> dict:
    """Append a successful scan and return what changed since the last one."""
    current = normalize(offers)
    data = _read(app_data_dir)
    snapshots = data["sources"].setdefault(source, [])
    previous = snapshots[-1] if snapshots else None
    result = changes(previous.get("offers") if previous else None, current)
    snapshots.append({"at": _now(), "offers": current})
    data["sources"][source] = snapshots[-MAX_SNAPSHOTS:]
    _write(app_data_dir, data)
    result["at"] = _now()
    result["previous_at"] = previous.get("at") if previous else None
    result["total"] = len(current)
    return result


def last_changes(app_data_dir, source: str) -> Optional[dict]:
    """What the last successful scan changed, or None when there was only one."""
    data = _read(app_data_dir)
    snapshots = data["sources"].get(source) or []
    if len(snapshots) < 2:
        return None
    result = changes(snapshots[-2].get("offers") or [], snapshots[-1].get("offers") or [])
    result["at"] = snapshots[-1].get("at")
    result["previous_at"] = snapshots[-2].get("at")
    result["total"] = len(snapshots[-1].get("offers") or [])
    return result


def change_lines(result: dict) -> List[str]:
    """Short human lines for a changes panel or tooltip."""
    if not result or result.get("first"):
        return []
    lines: List[str] = []
    for row in result.get("new", []):
        lines.append(f"NEW  {row['name']} — offered {row.get('offered') or 'unknown'}")
    for row in result.get("changed", []):
        lines.append(f"CHANGED  {row['name']} — {row.get('was')} → {row.get('offered')}")
    for row in result.get("gone", []):
        lines.append(f"GONE  {row['name']} — no longer offered")
    return lines
