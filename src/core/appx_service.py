"""One source of truth for AppX package enumeration.

Store Apps, Debloat, and the tweaks catalog each used to shell out to their
own `Get-AppxPackage` query with slightly different filters, so a change to
one module's query drifted away from the others. This module owns the query,
the `-AllUsers` fallback for unelevated runs, the framework/resource
filtering, and the newest-version dedup, and caches the result for a short
TTL so several modules can ask in the same minute without each spawning a
PowerShell process.
"""
import json
import logging
import os
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SELECT = ("Select-Object Name, Publisher, Version, InstallLocation, "
           "PackageFamilyName, Architecture, IsFramework, IsResourcePackage, "
           "IsPartiallyStaged | ConvertTo-Json -Compress")

#: How long a fetched package list is reused before re-querying.
CACHE_TTL_SECONDS = 60

_lock = threading.Lock()
_cache: Optional[Tuple[float, List[dict]]] = None


def fetch_packages(*, use_cache: bool = True) -> List[dict]:
    """All installed AppX packages, filtered (no frameworks/resources).

    Uses `-AllUsers` when elevated and falls back to the current user's list
    when that is refused, so read-only modules work unelevated. Caches the
    result briefly; pass `use_cache=False` to force a fresh query (e.g. right
    after an uninstall).
    """
    global _cache
    if use_cache:
        with _lock:
            if _cache is not None and time.monotonic() - _cache[0] < CACHE_TTL_SECONDS:
                return _cache[1]
    packages = _enumerate()
    if use_cache:
        with _lock:
            _cache = (time.monotonic(), packages)
    return packages


def _clean(data) -> List[dict]:
    """Normalize ConvertTo-Json output and drop frameworks/resources."""
    if isinstance(data, dict):
        data = [data]
    return [
        a for a in data
        if not a.get("IsFramework")
        and not a.get("IsResourcePackage")
        and not a.get("IsPartiallyStaged")
    ]


def _enumerate() -> List[dict]:
    from core.admin_utils import is_admin

    # -AllUsers needs elevation on some machines; try the per-user query as
    # the fallback when running unelevated.
    attempts = [True, False] if not is_admin() else [True]
    refused = ("access is denied", "requires elevation", "denied")
    for all_users in attempts:
        cmd = ("Get-AppxPackage -AllUsers | " if all_users
               else "Get-AppxPackage | ") + _SELECT
        result = subprocess.run(
            ["powershell", "-Command", cmd],
            capture_output=True, text=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            continue
        if any(marker in (result.stderr + result.stdout).lower()
               for marker in refused):
            continue
        try:
            return _clean(json.loads(result.stdout))
        except json.JSONDecodeError:
            logger.warning("Failed to parse AppxPackage output")
    return []


def dedupe_by_name(packages: List[dict]) -> List[dict]:
    """`-AllUsers` returns one row per user registration; keep the newest."""
    best: Dict[str, dict] = {}
    for package in packages:
        name = package.get("Name", "")
        if name not in best or _version_key(package.get("Version", "")) > _version_key(
            best[name].get("Version", "")
        ):
            best[name] = package
    return list(best.values())


def installed_names() -> List[str]:
    """Just the deduped package names (what Debloat's bloatware scan needs)."""
    return [a.get("Name", "") for a in dedupe_by_name(fetch_packages())]


def dir_size(path: str, max_entries: int = 30000) -> int:
    """An AppX package's on-disk size, or -1 when it is too large to scan
    within `max_entries` files. Shared by Store Apps and Debloat so both
    report the same number for the same package instead of each walking
    `InstallLocation` on their own."""
    if not path or not os.path.isdir(path):
        return 0
    total = 0
    count = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                count += 1
                if count > max_entries:
                    return -1
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    logger.debug("dir_size: giving up on this read", exc_info=True)
                    pass
    except OSError:
        logger.debug("dir_size: giving up on this read", exc_info=True)
        pass
    return total


def invalidate_cache() -> None:
    """Drop the cached list so the next call re-queries the system."""
    global _cache
    with _lock:
        _cache = None


def _version_key(version: str) -> tuple:
    key = []
    for seg in str(version).split("."):
        try:
            key.append(int(seg.split("-")[0]))
        except ValueError:
            key.append(0)
    return tuple(key)
