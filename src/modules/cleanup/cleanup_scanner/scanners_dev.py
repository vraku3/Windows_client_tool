"""Cleanup scanners: dev category (auto-split from cleanup_scanner.py)."""
import logging
import os

from modules.cleanup.cleanup_scanner._common import (
    ScanResult, _make_item,
)

logger = logging.getLogger(__name__)

def scan_winget_packages(min_age_days: int = 0) -> ScanResult:
    """Windows Package Manager (WinGet) downloaded package cache."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    winget_dir = os.path.join(local, r"Microsoft\WinGet\Packages")
    if not os.path.isdir(winget_dir):
        return result
    for pkg in os.listdir(winget_dir):
        pkg_path = os.path.join(winget_dir, pkg)
        item = _make_item(pkg_path, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_yarn_cache(min_age_days: int = 0) -> ScanResult:
    """Yarn package manager cache."""
    result = ScanResult()
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    targets = [
        os.path.join(appdata, r"yarn\Cache"),
        os.path.join(os.path.expanduser("~"), r".config\yarn\Berry\cache"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_pip_cache(min_age_days: int = 0) -> ScanResult:
    """Python pip download cache."""
    result = ScanResult()
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"pip\cache"),
        os.path.join(home, r".cache\pip"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_npm_cache(min_age_days: int = 0) -> ScanResult:
    """Node.js npm package cache."""
    result = ScanResult()
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", "")
    targets = [
        os.path.join(appdata, r"npm-cache\_cacache"),
        os.path.join(home, r".npm\_cacache"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_nuget_cache(min_age_days: int = 0) -> ScanResult:
    """.NET NuGet global packages cache."""
    result = ScanResult()
    home = os.path.expanduser("~")
    target = os.path.join(home, r".nuget\packages")
    if os.path.isdir(target):
        item = _make_item(target, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_gradle_cache(min_age_days: int = 0) -> ScanResult:
    """Gradle build tool cache."""
    result = ScanResult()
    home = os.path.expanduser("~")
    target = os.path.join(home, r".gradle\caches")
    if os.path.isdir(target):
        item = _make_item(target, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_cargo_cache(min_age_days: int = 0) -> ScanResult:
    """Rust Cargo package registry cache."""
    result = ScanResult()
    home = os.path.expanduser("~")
    target = os.path.join(home, r".cargo\registry\cache")
    if os.path.isdir(target):
        item = _make_item(target, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

__all__ = [
    'scan_winget_packages',
    'scan_yarn_cache',
    'scan_pip_cache',
    'scan_npm_cache',
    'scan_nuget_cache',
    'scan_gradle_cache',
    'scan_cargo_cache',
]