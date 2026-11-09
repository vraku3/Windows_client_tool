"""Verify a Microsoft Store update trigger against installed app versions.

The Store offers no completion callback. This module records the installed
versions, asks the Store to update, then re-reads them for a bounded window.
A refused enumeration is reported as "nothing verified", never as "up to
date", and a quiet window is not proof the Store has finished.

It also persists a snapshot per run so the next Run All can say which Store
apps changed since the previous one without waiting for the Store.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_SECONDS = 90
DEFAULT_INTERVAL_SECONDS = 10
SNAPSHOT_FILE = "store_snapshot.json"


def version_key(value: str) -> tuple:
    """Numeric comparison for dotted Windows package versions."""
    parts = []
    for piece in str(value or "").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def snapshot(packages) -> Dict[str, str]:
    """Newest installed version per package name; empty when nothing read."""
    best: Dict[str, str] = {}
    for package in packages or []:
        name = str(package.get("Name") or "").strip()
        version = str(package.get("Version") or "").strip()
        if not name:
            continue
        if name not in best or version_key(version) > version_key(best[name]):
            best[name] = version
    return best


def changed_apps(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    """Human-readable "Name: old -> new" for versions that moved."""
    rows = []
    for name in sorted(before):
        new = after.get(name)
        if new and new != before[name]:
            rows.append(f"{name}: {before[name]} -> {new}")
    return rows


def snapshot_path(app_data_dir) -> Optional[Path]:
    if not app_data_dir:
        return None
    return Path(app_data_dir) / "updates" / SNAPSHOT_FILE


def load_snapshot(app_data_dir) -> Optional[dict]:
    """The previous run's {"at", "apps"}; None when absent or corrupt."""
    path = snapshot_path(app_data_dir)
    if path is None:
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Could not read the Store snapshot: %s", exc)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("apps"), dict):
        logger.warning("Ignoring a malformed Store snapshot at %s", path)
        return None
    return data


def save_snapshot(app_data_dir, apps: Dict[str, str]) -> Optional[dict]:
    """Atomically persist this run's versions for the next comparison."""
    path = snapshot_path(app_data_dir)
    if path is None:
        return None
    data = {"at": datetime.now().isoformat(timespec="seconds"),
            "apps": dict(apps or {})}
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
        os.replace(temporary, path)
    except OSError as exc:
        logger.warning("Could not write the Store snapshot: %s", exc)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                logger.warning("Could not remove the temporary Store snapshot: %s", cleanup_exc)
        return None
    return data


@dataclass
class StoreVerifyResult:
    triggered: int = 0
    before_count: int = 0
    after_count: Optional[int] = None
    changed: List[str] = field(default_factory=list)
    read_refused: bool = False
    message: str = ""


def verify_store_update(
    *,
    fetch: Callable[..., Optional[List[dict]]],
    trigger: Callable[[Callable[[str], None]], int],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    log: Callable[[str], None] = lambda _line: None,
    budget_s: float = DEFAULT_BUDGET_SECONDS,
    interval_s: float = DEFAULT_INTERVAL_SECONDS,
    should_stop: Callable[[], bool] = lambda: False,
) -> StoreVerifyResult:
    """Snapshot, trigger the Store, then poll versions for `budget_s`.

    Polling stops early on the first observed version change: the Store may
    still be updating other apps, so the message says so rather than claiming
    completion.
    """
    before_list = fetch(use_cache=False)
    if before_list is None:
        log("Installed Store apps could not be read; nothing was verified.")
        return StoreVerifyResult(read_refused=True,
                                 message="Installed Store apps could not be read; "
                                         "nothing was verified.")
    before = snapshot(before_list)
    log(f"Recorded {len(before)} installed Store app version(s).")
    triggered = trigger(log)
    log(f"Store update scan requested ({triggered} trigger(s)); watching versions for "
        f"up to {int(budget_s)}s.")
    deadline = clock() + budget_s
    after: Optional[Dict[str, str]] = None
    while clock() < deadline and not should_stop():
        sleep(interval_s)
        if should_stop():
            break
        packages = fetch(use_cache=False)
        if packages is None:
            log("Installed versions could not be re-read during verification.")
            continue
        after = snapshot(packages)
        moved = changed_apps(before, after)
        if moved:
            for line in moved:
                log(f"Changed: {line}")
            return StoreVerifyResult(
                triggered=triggered, before_count=len(before), after_count=len(after),
                changed=moved,
                message=(f"{len(moved)} Store app(s) now show a newer version. "
                         "The Store may still be updating other apps."))
    if after is None:
        return StoreVerifyResult(
            triggered=triggered, before_count=len(before), read_refused=True,
            message="Store update scan was requested, but installed versions could not "
                    "be re-read; nothing is verified.")
    return StoreVerifyResult(
        triggered=triggered, before_count=len(before), after_count=len(after),
        message=(f"Store update scan requested ({triggered} trigger(s)). No installed "
                 f"version changed within {int(budget_s)}s; the Store may still be updating."))
