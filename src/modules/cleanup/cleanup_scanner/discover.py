"""Read-only discovery of cache folders outside the enabled catalog."""
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from modules.cleanup.cleanup_scanner import catalog

logger = logging.getLogger(__name__)

CACHE_NAMES = {
    "cache", "cache_data", "code cache", "gpucache", "dawncache", "dawngraphitecache",
    "dawnwebgpucache", "grshadercache", "shadercache", "shader cache", "dxcache", "glcache",
    "vkcache", "nv_cache", "d3dscache", "crashpad", "crashdumps", "crash reports", "logs",
    "cacheddata", "cachedextensionvsixs", "cachedprofilesdata", "webcache", "htmlcache",
}
KNOWN_NOT_JUNK = (r"Microsoft\Windows\WebCache",)
NEVER_UNDER = {
    "local storage", "indexeddb", "session storage", "cookies", "network",
    "databases", "storage", "wallet",
}
ROOTS = ["%LOCALAPPDATA%", "%APPDATA%", "%PROGRAMDATA%", r"%USERPROFILE%\.cache",
         r"%USERPROFILE%\AppData\LocalLow"]


@dataclass(frozen=True)
class Candidate:
    path: str
    size: int
    name: str


@dataclass
class DiscoverResult:
    candidates: List[Candidate]
    unreadable: int = 0


def _unreadable(error, result=None):
    if result is not None:
        result.unreadable += 1
    logger.debug("Unreadable cache folder: %s", error)


def _linked(path):
    return os.path.isjunction(path) or os.path.islink(path)


def _protected(path):
    return any(part.lower() in NEVER_UNDER for part in Path(path).parts)


def size_of(path, cap=400_000, cancelled=lambda: False, result=None):
    if _linked(path):
        return 0
    total = count = 0
    for current, dirs, files in os.walk(path, onerror=lambda error: _unreadable(error, result)):
        if cancelled():
            return total
        dirs[:] = [d for d in dirs if d.lower() not in NEVER_UNDER
                       and not _linked(os.path.join(current, d))]
        for name in files:
            count += 1
            if count > cap or cancelled():
                return total
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError as exc:
                _unreadable(exc, result)
    return total


def covered(path, targets):
    p = os.path.normcase(os.path.abspath(path))
    for target in targets:
        t = os.path.normcase(os.path.abspath(target))
        if p == t or p.startswith(t.rstrip(os.sep) + os.sep) or t.startswith(p + os.sep):
            return True
    return False


def _is_or_contains(path, targets):
    p = os.path.normcase(os.path.abspath(path))
    for target in targets:
        t = os.path.normcase(os.path.abspath(target))
        if p == t or t.startswith(p.rstrip(os.sep) + os.sep):
            return True
    return False


def catalog_targets():
    return [target for spec in catalog.load_catalog().values()
            if not spec.disabled_reason for target in catalog.targets_of(spec)]


def reviewed_not_junk_targets():
    return [target for spec in catalog.load_catalog().values()
            if spec.disabled_reason for target in catalog.targets_of(spec)]


def _known_not_junk(path):
    normalized = os.path.normpath(path).replace("/", "\\").casefold()
    # The audit removed OneNote's catalog path; retain its reviewed exclusion.
    if "\\microsoft\\onenote\\" in "\\" + normalized:
        return True
    return any(normalized == suffix.casefold() or normalized.endswith("\\" + suffix.casefold())
               for suffix in KNOWN_NOT_JUNK)


def _cache_paths(roots, cancelled, result):
    seen = set()
    for raw in roots:
        if cancelled():
            return
        root = os.path.normpath(os.path.expandvars(os.fspath(raw)))
        if _protected(root) or _linked(root) or not os.path.isdir(root):
            continue
        for current, dirs, _files in os.walk(root, onerror=lambda error: _unreadable(error, result)):
            if cancelled():
                return
            dirs[:] = [d for d in dirs if d.lower() not in NEVER_UNDER
                       and not _linked(os.path.join(current, d))]
            if len(Path(current).relative_to(root).parts) > 7:
                dirs[:] = []
                continue
            for name in list(dirs):
                if name.lower() in CACHE_NAMES:
                    dirs.remove(name)
                    path = os.path.join(current, name)
                    key = os.path.normcase(os.path.abspath(path))
                    if key not in seen:
                        seen.add(key)
                        yield path


def find_uncovered_caches(min_bytes=20 * 2**20, targets=None, roots=None,
                          cancelled=lambda: False, excluded=None) -> DiscoverResult:
    result = DiscoverResult([])
    if cancelled():
        return result
    targets = catalog_targets() if targets is None else list(targets)
    excluded = reviewed_not_junk_targets() if excluded is None else list(excluded)
    rows = []
    for path in _cache_paths(ROOTS if roots is None else roots, cancelled, result):
        if cancelled():
            break
        if (_linked(path) or covered(path, targets) or _is_or_contains(path, excluded)
                or _known_not_junk(path)):
            continue
        size = size_of(path, cancelled=cancelled, result=result)
        if cancelled():
            break
        if size >= min_bytes:
            rows.append(Candidate(path, size, os.path.basename(path)))
    result.candidates = sorted(rows, key=lambda row: row.size, reverse=True)
    return result
