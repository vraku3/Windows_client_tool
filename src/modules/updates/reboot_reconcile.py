"""Post-reboot reconciliation for updates that asked for a restart (Qt-free).

Windows Update reports "installed -- restart to finish" without telling us
what the version will be afterwards, and winget may lose the read-back across
a reboot. This module persists what was attempted, then judges the next scan
honestly:

- winget/Store items are CONFIRMED only when the installed version moved
  forward, or when a successful scan no longer offers them at all;
- WU items are never called installed from a cleared reboot flag alone --
  that is reported as ``restart_cleared_unverified``.

Nothing here raises into a maintenance operation.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

CONFIRMED = "confirmed"
STILL_PENDING = "still_pending"
UNVERIFIED = "restart_cleared_unverified"
UNKNOWN = "unknown"

MAX_PENDING = 100


def pending_path(app_data_dir) -> Path:
    return Path(app_data_dir) / "updates" / "pending_restart.json"


def _read(app_data_dir) -> List[dict]:
    if not app_data_dir:
        return []
    try:
        data = json.loads(pending_path(app_data_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        logger.warning("Could not read the pending-restart list", exc_info=True)
        return []
    rows = data.get("items") if isinstance(data, dict) else None
    return [row for row in rows or [] if isinstance(row, dict) and row.get("id")]


def _write(app_data_dir, rows: List[dict]) -> bool:
    if not app_data_dir:
        return False
    path = pending_path(app_data_dir)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix="pending_restart.", suffix=".tmp",
                                         delete=False) as stream:
            temporary = stream.name
            json.dump({"updated": datetime.now().astimezone().isoformat(timespec="seconds"),
                       "items": rows[-MAX_PENDING:]}, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return True
    except (OSError, TypeError, ValueError):
        logger.warning("Could not write the pending-restart list", exc_info=True)
        return False
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove a temporary pending-restart file", exc_info=True)


def _key(row: dict) -> str:
    return f"{str(row.get('source') or 'winget').casefold()}:{str(row.get('id')).casefold()}"


def record(app_data_dir, items: Iterable[dict]) -> int:
    """Merge newly attempted items into the pending list; returns rows kept."""
    if not app_data_dir:
        return 0
    rows = {_key(row): row for row in _read(app_data_dir)}
    added = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        row = {"source": str(item.get("source") or "winget")[:20],
               "id": str(item["id"])[:200],
               "name": str(item.get("name") or item["id"])[:200],
               "version_before": item.get("version_before") or None,
               "version_offered": item.get("version_offered") or None,
               "recorded": datetime.now().astimezone().isoformat(timespec="seconds")}
        rows[_key(row)] = row
        added += 1
    _write(app_data_dir, list(rows.values()))
    return added


def load(app_data_dir) -> List[dict]:
    return _read(app_data_dir)


def forget(app_data_dir, keys: Iterable[str]) -> None:
    """Drop resolved items; keep anything still unverified."""
    if not app_data_dir:
        return
    drop = {str(key).casefold() for key in keys}
    rows = [row for row in _read(app_data_dir) if _key(row) not in drop]
    if rows:
        _write(app_data_dir, rows)
    else:
        try:
            pending_path(app_data_dir).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove the pending-restart list", exc_info=True)


def _parts(version) -> Optional[tuple]:
    text = str(version or "").strip()
    if not text or text.casefold() in ("unknown", "n/a", "-", "—"):
        return None
    numbers = re.findall(r"\d+", text)
    return tuple(int(n) for n in numbers) if numbers else None


def version_advanced(after, before) -> bool:
    """True only when both versions are comparable and `after` is newer."""
    newer, older = _parts(after), _parts(before)
    if newer is None or older is None:
        return False
    width = max(len(newer), len(older))
    return newer + (0,) * (width - len(newer)) > older + (0,) * (width - len(older))


def _offer_value(offer, name: str) -> str:
    if isinstance(offer, dict):
        return str(offer.get(name) or "")
    return str(getattr(offer, name, "") or "")


def reconcile(pending: List[dict], offers: Optional[List], reboot_pending: Optional[bool]) -> List[dict]:
    """Judge each pending item against the first scan after the reboot.

    `offers` is the scan result (None when the scan itself failed) and
    `reboot_pending` is None when the reboot state could not be read.
    """
    by_id = {}
    for offer in offers or []:
        by_id[_offer_value(offer, "winget_id").casefold()] = offer
    results = []
    for row in pending:
        source = str(row.get("source") or "winget").casefold()
        ident = str(row.get("id") or "")
        base = {"source": source, "id": ident, "name": row.get("name") or ident,
                "version_before": row.get("version_before"),
                "version_offered": row.get("version_offered"), "key": _key(row)}
        if source == "wu":
            if reboot_pending is None:
                results.append({**base, "status": UNKNOWN,
                                "detail": "Restart state could not be read after the restart."})
            elif reboot_pending:
                results.append({**base, "status": STILL_PENDING,
                                "detail": "A restart is still pending."})
            else:
                results.append({**base, "status": UNVERIFIED,
                                "detail": "Restart completed; Windows Update does not expose the installed version."})
            continue
        if offers is None:
            results.append({**base, "status": UNKNOWN, "detail": "The post-restart scan failed."})
            continue
        offer = by_id.get(ident.casefold())
        if offer is None:
            results.append({**base, "status": CONFIRMED, "detail": "No longer offered by a successful scan."})
            continue
        installed = _offer_value(offer, "installed_version")
        if version_advanced(installed, row.get("version_before")):
            results.append({**base, "status": CONFIRMED, "detail": f"Installed version is now {installed}."})
        else:
            offered = _offer_value(offer, "available_version")
            results.append({**base, "status": STILL_PENDING,
                            "detail": f"Still offered (installed {installed or 'unknown'}, offered {offered or 'unknown'})."})
    return results


def resolved(results: Iterable[dict]) -> List[str]:
    """Keys that no longer need checking (confirmed or explicitly unverifiable)."""
    return [r["key"] for r in results if r.get("status") in (CONFIRMED, UNVERIFIED)]
