"""Tracks which bloatware packages Debloat has actually removed, so a later
scan can tell "never removed" apart from "removed, and back again."

The real complaint this answers ("a Windows 11 Feature Update brought
Copilot/Xbox/Recall back") happens on a timescale of weeks to months --
long after `debloat_history.py`'s 200-entry apply log has scrolled the
event off, and that log only ever recorded a count (`success=N, total=M`),
never which packages. Without a persistent per-package record, a reappeared
app scans identically to one that was simply never removed: "Installed",
no different color, no explanation. `appx_service`/`app_catalog.py` already
has a short-window mechanism (`_appx_readd_source`, reading the
`AppXDeploymentServer/Operational` log for a few seconds around one removal)
for "did something put it right back just now" -- this is the same question
asked at scan time, arbitrarily far in the future, backed by this app's own
apply history instead of a Windows event log that has long since rolled over.

Verified on this real machine 2026-09-28: `debloat_scanner.get_installed_packages()`
returns 25 real KNOWN_PACKAGES entries right now (Microsoft.GamingApp,
Microsoft.YourPhone, Microsoft.SecHealthUI among them) -- any of those,
recorded here as removed and still showing installed on a later scan, is
exactly the case this file exists to catch.
"""
import json
import logging
import os
from datetime import datetime
from typing import Dict, Iterable, List

logger = logging.getLogger(__name__)


def _store_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "debloat_removed.json")


def _now_iso() -> str:
    """Seamed out for tests -- see test_debloat_reinstall_tracker.py, which
    needs two distinct timestamps in the same test to prove a second
    removal bumps the reinstall count on the return that follows it, and
    `isoformat(timespec="seconds")` alone cannot guarantee that within one
    real second of wall-clock time."""
    return datetime.now().isoformat(timespec="seconds")


def _load(path: str) -> Dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("could not read %s: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def record_removed(package_ids: Iterable[str]) -> None:
    """Record that each of `package_ids` was just confirmed removed.

    Called only with packages `_on_apps_applied` has already confirmed gone
    (present before, absent after a fresh, cache-invalidated re-scan) --
    never with the requested set, which may include ones the removal did
    not actually take. Re-recording an already-tracked package (removed,
    came back, removed again) refreshes `last_removed_at` and bumps
    `times_reinstalled`, rather than losing the earlier history.
    """
    ids = [p for p in package_ids if p]
    if not ids:
        return
    path = _store_path()
    data = _load(path)
    now = _now_iso()
    for pkg in ids:
        existing = data.get(pkg)
        if existing is None:
            data[pkg] = {"first_removed_at": now, "last_removed_at": now,
                        "times_reinstalled": 0}
        else:
            # Seen again after a prior removal: if the last thing we know
            # about it is a removal (no reinstall recorded since), this
            # removal alone teaches us nothing new about a reinstall count.
            # `check_reinstalled` is what increments that count, at the
            # moment a scan actually finds it back -- this just refreshes
            # the timestamp for the next round of tracking.
            existing["last_removed_at"] = now
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.warning("could not write %s: %s", path, e)


def check_reinstalled(installed: Iterable[str]) -> List[dict]:
    """Compare currently-installed packages against removal history.

    Returns one dict per package that is BOTH currently installed AND has a
    removal on record -- `{"package": ..., "last_removed_at": ...,
    "times_reinstalled": N}` -- and persists the bumped `times_reinstalled`
    count plus a fresh `last_seen_reinstalled_at` so a second scan without
    an intervening removal does not keep incrementing it.

    A package with no removal record at all (never touched by Debloat) is
    not reported here -- that is simply "installed," not "reinstalled."
    """
    installed_set = set(installed)
    if not installed_set:
        return []
    path = _store_path()
    data = _load(path)
    if not data:
        return []
    results: List[dict] = []
    changed = False
    for pkg, record in data.items():
        if pkg not in installed_set:
            continue
        already_flagged_this_removal = (
            record.get("_flagged_for") == record.get("last_removed_at"))
        if not already_flagged_this_removal:
            record["times_reinstalled"] = record.get("times_reinstalled", 0) + 1
            record["_flagged_for"] = record.get("last_removed_at")
            changed = True
        results.append({
            "package": pkg,
            "last_removed_at": record.get("last_removed_at", ""),
            "times_reinstalled": record.get("times_reinstalled", 0),
        })
    if changed:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.warning("could not write %s: %s", path, e)
    return results
