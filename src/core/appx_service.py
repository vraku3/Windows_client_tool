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

    A failed enumeration collapses to `[]` here, same as always -- most
    callers only ever display or scan this list, for which "nothing found"
    and "couldn't find out" are an acceptable conflation. A caller that
    reads the list as EVIDENCE (e.g. verifying an uninstall actually took)
    must not make that conflation -- use `fetch_packages_or_none` instead.
    """
    packages = fetch_packages_or_none(use_cache=use_cache)
    return packages if packages is not None else []


def fetch_packages_or_none(*, use_cache: bool = True) -> Optional[List[dict]]:
    """Same as `fetch_packages`, but preserves `_enumerate`'s distinction
    between a genuinely empty result and a failed one: returns `None` when
    the enumeration itself could not be completed, rather than silently
    turning that into "no packages installed"."""
    global _cache
    if use_cache:
        with _lock:
            if _cache is not None and time.monotonic() - _cache[0] < CACHE_TTL_SECONDS:
                return _cache[1]
    packages = _enumerate()
    if packages is None:
        return None
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


#: Hard ceiling on the PowerShell query itself. Kept well short of the
#: watchdogs above it (Debloat/Store Apps show no other timeout at all) so a
#: stuck query surfaces as a failed scan, never an indefinite "Scanning...".
_ENUMERATE_TIMEOUT_SECONDS = 60


def _run_ps_bounded(cmd: str, timeout: float = _ENUMERATE_TIMEOUT_SECONDS
                     ) -> Optional[Tuple[int, str, str]]:
    """Run a PowerShell command and guarantee return within `timeout` (plus a
    short grace period), or `None` if it could not be reaped in time.

    `subprocess.run(..., timeout=...)` does NOT guarantee this on Windows:
    on a timeout it calls `Popen.kill()` -- which only signals the immediate
    `powershell.exe` process, not any descendant it spawned -- and then
    (`subprocess.py`'s own Windows path) calls `communicate()` a SECOND time
    with no timeout at all, to drain leftover output. If a descendant is
    still alive and holding the inherited stdout/stderr pipe handles open
    (observed on this machine: Smart App Control intercepting a spawned
    child of an unsigned/unreputable exe under elevation -- see
    driver-manager-vendor-updates memory), that second read blocks on EOF
    forever, turning a declared 60s timeout into the exact "stuck on
    Scanning... for 20+ minutes" symptom reported against Debloat's Apps
    tab, with the code never reaching a `except` block that could report it.

    Fix: kill the WHOLE process tree (`taskkill /T /F`, not just the one
    PID) before ever attempting to drain output, and cap that drain itself
    with its own short timeout -- so a wedged descendant costs a few
    seconds, never forever.
    """
    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        logger.warning("AppxPackage query exceeded %ss -- killing process tree (pid %s)",
                       timeout, proc.pid)
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            # Bounded drain only -- never the unbounded second read that
            # causes the real hang. A tree-kill that still can't be reaped
            # this quickly means something is holding a handle open no
            # matter what; give up and report the query as failed instead
            # of blocking the caller further.
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning(
                "AppxPackage query's process tree (pid %s) still held its "
                "output pipe open %ss after taskkill /T /F -- giving up on "
                "the drain and reporting the query as failed",
                proc.pid, 5)
        return None


def _enumerate() -> Optional[List[dict]]:
    """Every installed AppX package, or `None` when every attempt was
    refused, errored, or produced unparseable output -- NEVER collapsed
    into `[]`. `_clean()`'s frameworks/resources filtering can validly
    reduce a real machine's packages to an empty list; a query that never
    got a usable answer must not look identical to that (see
    `fetch_packages_or_none`)."""
    from core.admin_utils import is_admin

    # -AllUsers needs elevation on some machines; try the per-user query as
    # the fallback when running unelevated.
    attempts = [True, False] if not is_admin() else [True]
    refused = ("access is denied", "requires elevation", "denied")
    for all_users in attempts:
        cmd = ("Get-AppxPackage -AllUsers | " if all_users
               else "Get-AppxPackage | ") + _SELECT
        outcome = _run_ps_bounded(cmd)
        if outcome is None:
            continue
        returncode, stdout, stderr = outcome
        if returncode != 0 or not stdout.strip():
            continue
        if any(marker in (stderr + stdout).lower() for marker in refused):
            continue
        try:
            return _clean(json.loads(stdout))
        except json.JSONDecodeError:
            logger.warning("Failed to parse AppxPackage output")
    logger.warning("AppxPackage enumeration failed -- every attempt was "
                   "refused, errored, or returned no usable output")
    return None


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
    """Just the deduped package names (what Debloat's bloatware scan needs).

    Deliberately leaves `fetch_packages()`'s `use_cache=True` default in
    place (S29): Debloat's "what's roughly installed" catalog check can
    tolerate a brief cache hit, unlike Store Apps' `_load_apps`, which
    forces `use_cache=False` because it needs current truth right before an
    uninstall decision.
    """
    return [a.get("Name", "") for a in dedupe_by_name(fetch_packages())]


def installed_names_or_none() -> Optional[List[str]]:
    """Same as `installed_names()`, but preserves a failed enumeration
    instead of collapsing it to an empty list -- Debloat's scan uses this
    so a query that could not complete is reported as a failed scan, never
    as "0 bloatware apps installed"."""
    packages = fetch_packages_or_none()
    if packages is None:
        return None
    return [a.get("Name", "") for a in dedupe_by_name(packages)]


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


def dir_size_detailed(path: str, max_entries: int = 30000) -> Tuple[int, bool]:
    """An AppX package's on-disk size, plus whether the count is only a
    partial/approximate read (too many entries, or some files could not be
    stat'd). Sibling to `dir_size()` above -- that function keeps its own
    `-1`-means-"too large" contract for its existing caller in
    `debloat_module.py`; this one is for callers (Store Apps' size scan)
    that want the partial total AND an explicit flag rather than a sentinel."""
    if not path or not os.path.isdir(path):
        return 0, False
    total, count, approximate = 0, 0, False
    try:
        for root, _, files in os.walk(path):
            for f in files:
                count += 1
                if count > max_entries:
                    return total, True  # stopped counting -- what we
                                        # have so far, marked partial
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    approximate = True
                    logger.debug("dir_size_detailed: could not stat one file",
                                exc_info=True)
    except OSError:
        approximate = True
        logger.debug("dir_size_detailed: giving up on this read", exc_info=True)
    return total, approximate


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
