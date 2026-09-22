# -*- coding: utf-8 -*-
"""
Enhanced Browser Caches with Browser Selector

Features:
- Browser selector dropdown
- Profile-level cleanup
- Running application detection
- Smart warnings
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Module-level convenience function — delegates to EnhancedBrowserScanner
def detect_browsers(all_browsers: bool = True, browser_name: str = "All"):
    """Detect browsers and their cache sizes. Convenience wrapper for EnhancedBrowserScanner."""
    return EnhancedBrowserScanner().detect_browsers(all_browsers=all_browsers, browser_name=browser_name)


def delete_selected(categories, progress_cb=None):
    """Delete selected cache categories. Wrapper for EnhancedBrowserScanner.delete_selected."""
    return EnhancedBrowserScanner().delete_selected(categories, progress_cb=progress_cb)


# Browser definitions with paths
CHROMIUM_BROWSERS = [
    ("Brave", r"BraveSoftware\Brave-Browser\User Data", False),
    ("Chrome", r"Google\Chrome\User Data", False),
    ("Edge", r"Microsoft\Edge\User Data", False),
    ("Vivaldi", r"Vivaldi\User Data", False),
    ("Thorium", r"Thorium\User Data", False),
    ("Chromium", r"Chromium\User Data", False),
    ("Yandex", r"Yandex\YandexBrowser\User Data", False),
    ("Opera", r"Opera Software\Opera Stable", True),
    ("Opera GX", r"Opera Software\Opera GX Stable", True),
]

FIREFOX_BROWSERS = [
    ("Firefox", r"Mozilla\Firefox"),
    ("LibreWolf", r"LibreWolf"),
    ("Waterfox", r"Waterfox"),
    ("Pale Moon", r"Moonchild Productions\Pale Moon"),
]

# Browser executable mappings (consolidated)
BROWSER_EXES = {
    "Brave":      "brave.exe",
    "Chrome":     "chrome.exe",
    "Edge":       "msedge.exe",
    "Vivaldi":    "vivaldi.exe",
    "Thorium":    "thorium.exe",
    "Chromium":   "chrome.exe",
    "Yandex":     "browser.exe",
    "Opera":      "opera.exe",
    "Opera GX":   "opera.exe",
    "Firefox":    "firefox.exe",
    "LibreWolf":  "librewolf.exe",
    "Waterfox":   "waterfox.exe",
    "Pale Moon":  "palemoon.exe",
}

# All modern cache subdirectories for Chromium-based browsers.
#
# Real, live bug found 2026-09-22: this dict used to also include "Local
# Storage", "Sessions", "Tabs", "Local App Settings" and "Web Application
# History" -- and _browser_tab.py's real, live Browser Caches tab (via
# scan_browsers_robust() -> BrowserScanner2().scan()) pre-CHECKS every
# entry it finds by default (see _browser_tab.py's setCheckState(...,
# Checked) on every CacheEntry). "Local Storage" specifically is where a
# growing number of real sites keep session/login state instead of (or
# alongside) cookies -- a user opening this tab and clicking Clean without
# manually unchecking anything would delete that for every profile in
# every Chromium browser installed. Narrowed to match
# CHROMIUM_CACHE_CATEGORIES (this file's OTHER, already-correctly-scoped
# Chromium cache list, used by EnhancedBrowserScanner/detect_browsers)
# rather than re-deriving safety per entry from scratch.
CHROMIUM_CACHE_SUBDIRS = {
    "Cache":                     "HTTP Cache",
    "Cache2":                    "HTTP Cache v2",
    "Code Cache":                "Compiled JS/WASM",
    "GPUCache":                  "GPU Shader Cache",
    "Media Cache":               "Media Cache",
    "blob_storage":              "Blob Storage",
    "ShaderCache":               "Shader Cache",
    "GrShaderCache":             "General Shader Cache",
    "DawnCache":                 "Dawn WebGPU Cache",
    "Extension Cache":           "Extension Cache",
    r"Crashpad\reports":         "Crash Reports",
    r"Crashpad\pending":         "Pending Crash Reports",
    r"GrShaderCache\GPUCache":  "GPU Cache (sub)",
    r"ShaderCache\GPUCache":     "GPU Cache (sub)",
}

# Firefox cache subdirectories
FIREFOX_CACHE_SUBDIRS = {
    "cache2":               "HTTP Cache",
    "cache":                "HTTP Cache (legacy)",
    "offlineCache":         "Offline Cache",
    "thumbnails":           "Page Thumbnails",
    "startupCache":         "Startup Cache",
    r"crashes\submitted":   "Submitted Crashes",
    r"crashes\pending":     "Pending Crashes",
    "minidumps":            "Minidumps",
}


@dataclass
class CacheEntry:
    """A single cache directory within a browser profile."""
    label: str
    path: Path
    size_bytes: int = 0
    exists: bool = False
    locked: bool = False  # True if browser running or access denied

    def __post_init__(self):
        if self.size_bytes < 0:
            self.size_bytes = 0


@dataclass
class BrowserProfile:
    """A single browser profile (e.g. Default, Profile 2)."""
    name: str
    path: Path
    caches: List[CacheEntry] = None
    error: Optional[str] = None

    def __post_init__(self):
        self.caches = self.caches or []
        self._total: int = 0

    @property
    def total_bytes(self) -> int:
        if self._total:
            return self._total
        self._total = sum(c.size_bytes for c in self.caches)
        return self._total


@dataclass
class BrowserScanResult:
    """Result for one browser engine (Chrome, Edge, Firefox, etc.)."""
    name: str
    engine: str          # "chromium" | "firefox"
    is_running: bool = False
    profiles: List[BrowserProfile] = None
    error: Optional[str] = None

    def __post_init__(self):
        self.profiles = self.profiles or []

    @property
    def total_bytes(self) -> int:
        return sum(p.total_bytes for p in self.profiles)

    @property
    def total_caches(self) -> int:
        return sum(len(p.caches) for p in self.profiles)

    @property
    def locked_count(self) -> int:
        return sum(1 for p in self.profiles for c in p.caches if c.locked)

# Cache categories for Chromium
CHROMIUM_CACHE_CATEGORIES = [
    ("Cache", "HTTP Cache"),
    ("Cache2", "HTTP Cache v2"),
    ("Code Cache", "Compiled JS & WASM"),
    ("GPU Cache", "GPU Shader Cache"),
    ("Media Cache", "Media Cache"),
    ("blob_storage", "Blob Storage"),
    (r"Crashpad\reports", "Crash Reports"),
]

# Cache categories for Firefox
FIREFOX_CACHE_CATEGORIES = [
    ("cache2", "HTTP Cache"),
    ("cache", "Cache (legacy)"),
    ("offlineCache", "Offline Cache"),
    ("thumbnails", "Page Thumbnails"),
    ("startupCache", "Startup Cache"),
    (r"crashes\submitted", "Submitted Crashes"),
    (r"crashes\pending", "Pending Crashes"),
    ("minidumps", "Minidumps"),
]

# Browser executable mappings
EXE_MAP = {
    "Brave": "brave.exe",
    "Chrome": "chrome.exe",
    "Edge": "msedge.exe",
    "Vivaldi": "vivaldi.exe",
    "Thorium": "thorium.exe",
    "Chromium": "chrome.exe",
    "Yandex": "browser.exe",
    "Opera": "opera.exe",
    "Opera GX": "opera.exe",
    "Firefox": "firefox.exe",
    "LibreWolf": "librewolf.exe",
    "Waterfox": "waterfox.exe",
    "Pale Moon": "palemoon.exe",
}


@dataclass
class CacheCategory:
    """Represents a cache category with path and size."""

    label: str
    path: Path
    size_bytes: int = 0
    exists: bool = False


@dataclass
class LegacyBrowserProfile:
    name: str
    path: Path
    categories: List[CacheCategory] = None
    total_bytes: int = 0

    def __post_init__(self):
        self.categories = self.categories or []


@dataclass
class LegacyBrowserResult:
    """Represents a browser result with profiles."""

    name: str
    engine: str  # "chromium" | "firefox"
    profiles: List[LegacyBrowserProfile] = None
    is_running: bool = False
    total_bytes: int = 0

    def __post_init__(self):
        self.profiles = self.profiles or []


class BrowserScanner2:
    """
    Robust browser cache scanner — enumerates all profiles and all modern
    cache locations per browser engine.

    Usage:
        scanner = BrowserScanner2()
        results = scanner.scan()  # List[BrowserScanResult]
    """

    def _is_browser_running(self, exe_name: str) -> bool:
        """Check if a browser process is running."""
        try:
            import psutil
            exe_lower = exe_name.lower()
            for p in psutil.process_iter(["name"]):
                try:
                    if p.info["name"] and p.info["name"].lower() == exe_lower:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    logger.debug("Ignored (psutil.NoSuchProcess, psutil.AccessDenied)", exc_info=True)
        except Exception as e:
            logger.warning("BrowserScanner profile scan failed: %s", e)
        return False

    def _find_chromium_profiles(self, user_data: Path) -> List[Path]:
        """Find all Chromium-based browser profiles with maximum coverage."""
        profiles = []
        seen = set()

        # 1. Local State info_cache — canonical source
        local_state = user_data / "Local State"
        if local_state.is_file():
            try:
                with open(local_state, encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
                cache = data.get("profile", {}).get("info_cache", {})
                for name in cache:
                    p = user_data / name
                    if p.is_dir() and p not in seen:
                        profiles.append(p)
                        seen.add(p)
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.debug(f"Local State parse failed for {user_data}: {e}")

        # 2. Directory scan for profile folders
        try:
            for entry in os.scandir(user_data):
                if not entry.is_dir():
                    continue
                entry_path = Path(entry.path)
                if entry_path in seen:
                    continue
                name_lower = entry.name.lower()
                if name_lower == "default" or name_lower.startswith("profile"):
                    profiles.append(entry_path)
                    seen.add(entry_path)
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)

        return profiles

    def _find_firefox_profiles(self, profiles_dir: Path) -> List[Path]:
        """Find all Firefox profiles from Profiles folder."""
        profiles = []
        if not profiles_dir.is_dir():
            return profiles
        try:
            for entry in os.scandir(profiles_dir):
                if entry.is_dir():
                    profiles.append(Path(entry.path))
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
        profiles.sort(key=lambda p: p.name)
        return profiles

    def _scan_cache_path(self, cache_path: Path, label: str) -> CacheEntry:
        """Scan a single cache path and return a CacheEntry."""
        entry = CacheEntry(label=label, path=cache_path)
        if not cache_path.exists():
            entry.exists = False
            return entry
        entry.exists = True

        try:
            entry.size_bytes = self._dir_size(cache_path)
        except PermissionError:
            entry.locked = True
            entry.size_bytes = 0
        except OSError:
            entry.locked = True
            entry.size_bytes = 0

        return entry

    def _dir_size(self, path: Path) -> int:
        """Calculate directory size with depth limit."""
        total = 0
        try:
            for entry in path.iterdir():
                try:
                    if entry.is_file():
                        total += entry.stat().st_size
                    elif entry.is_dir():
                        for sub in entry.iterdir():
                            try:
                                if sub.is_file():
                                    total += sub.stat().st_size
                            except OSError:
                                logger.debug("Ignored OSError", exc_info=True)
                except OSError:
                    logger.debug("Ignored OSError", exc_info=True)
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
        return total

    def scan_chromium_browser(
        self,
        name: str,
        user_data: Path,
        use_appdata: bool = False,
    ) -> BrowserScanResult:
        """Scan a Chromium-based browser (Chrome, Edge, Brave, etc.)."""
        exe = BROWSER_EXES.get(name, "")
        result = BrowserScanResult(name=name, engine="chromium")
        result.is_running = self._is_browser_running(exe)

        if not user_data.is_dir():
            result.error = f"User Data folder not found: {user_data}"
            return result

        for prof_path in self._find_chromium_profiles(user_data):
            profile = BrowserProfile(name=prof_path.name, path=prof_path)
            for sub_path, label in CHROMIUM_CACHE_SUBDIRS.items():
                cache_path = prof_path / sub_path
                entry = self._scan_cache_path(cache_path, label)
                profile.caches.append(entry)
            if profile.caches:
                result.profiles.append(profile)

        return result

    def scan_firefox_browser(self, name: str, profiles_dir: Path) -> BrowserScanResult:
        """Scan a Firefox-based browser."""
        exe = BROWSER_EXES.get(name, "")
        result = BrowserScanResult(name=name, engine="firefox")
        result.is_running = self._is_browser_running(exe)

        if not profiles_dir.is_dir():
            result.error = f"Profiles folder not found: {profiles_dir}"
            return result

        for prof_path in self._find_firefox_profiles(profiles_dir):
            profile = BrowserProfile(name=prof_path.name, path=prof_path)
            for sub_path, label in FIREFOX_CACHE_SUBDIRS.items():
                cache_path = prof_path / sub_path
                entry = self._scan_cache_path(cache_path, label)
                profile.caches.append(entry)
            if profile.caches:
                result.profiles.append(profile)

        return result

    def scan(self) -> List[BrowserScanResult]:
        """
        Scan all supported browsers and return results.
        Never raises — always completes with partial results on error.
        """
        results = []
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        appdata = Path(os.environ.get("APPDATA", ""))

        # Chromium browsers
        for bname, rel_path, use_appdata in CHROMIUM_BROWSERS:
            user_data = (appdata if use_appdata else local) / rel_path
            result = self.scan_chromium_browser(bname, user_data, use_appdata)
            if result.profiles or result.error:
                results.append(result)

        # Firefox browsers
        for bname, rel_path in FIREFOX_BROWSERS:
            browser_dir = appdata / rel_path
            profiles_dir = browser_dir / "Profiles"
            result = self.scan_firefox_browser(bname, profiles_dir)
            if result.profiles or result.error:
                results.append(result)

        results.sort(key=lambda r: r.total_bytes, reverse=True)
        return results


def scan_browsers_robust() -> List[BrowserScanResult]:
    """Convenience wrapper: scan all browsers using BrowserScanner2."""
    return BrowserScanner2().scan()


def delete_cache_entries(entries: List[CacheEntry], progress_cb=None) -> Tuple[int, int]:
    """
    Delete selected cache entries (directories or files).
    Returns (freed_bytes: int, error_count: int).
    """
    freed = 0
    errors = 0
    total = len(entries)
    for i, entry in enumerate(entries):
        if not hasattr(entry, 'path') or not entry.path.exists():
            continue
        try:
            if entry.path.is_dir():
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                entry.path.unlink()
            freed += entry.size_bytes
        except Exception:
            errors += 1
        if progress_cb:
            progress_cb(i + 1, total)
    return freed, errors


class EnhancedBrowserScanner:
    """Enhanced browser cache scanner with browser selector."""

    def _is_running(self, exe: str) -> bool:
        """Check if browser is currently running."""
        try:
            import psutil

            exe_lower = exe.lower()
            for p in psutil.process_iter(["name"]):
                try:
                    if (p.info["name"] or "").lower() == exe_lower:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    logger.debug("Ignored (psutil.NoSuchProcess, psutil.AccessDenied)", exc_info=True)
        except Exception as e:
            logger.warning(f"Browser candidate scan failed: {e}")
        return False

    def _chromium_profile_names(self, user_data: Path) -> List[str]:
        """Get list of Chromium profiles."""
        names = []
        local_state = user_data / "Local State"
        if local_state.is_file():
            try:
                with open(local_state, encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
                cache = data.get("profile", {}).get("info_cache", {})
                names = [k for k in cache if (user_data / k).is_dir()]
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Browser candidate scan failed: {e}")

        # Fallback to Default profile
        if not names and (user_data / "Default").is_dir():
            names = ["Default"]
        return names

    def detect_browsers(
        self, all_browsers: bool = True, browser_name: str = "All"
    ) -> List[LegacyBrowserResult]:
        """
        Detect browsers and their cache sizes.

        Args:
            all_browsers: True = show all, or specific browser name
            browser_name: Specific browser to show
        """
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        appdata = Path(os.environ.get("APPDATA", ""))
        results = []

        # Chromium-based browsers
        for name, rel, use_appdata in CHROMIUM_BROWSERS:
            if not browser_name or all_browsers:
                user_data = (appdata if use_appdata else local) / rel
                if not user_data.is_dir():
                    continue

                browser = LegacyBrowserResult(
                    name=name,
                    engine="chromium",
                    is_running=self._is_running(EXE_MAP.get(name, "")),
                )

                for prof_name in self._chromium_profile_names(user_data):
                    prof_path = user_data / prof_name
                    cats = self._scan_chromium_profile(prof_path)
                    total = sum(c.size_bytes for c in cats)
                    browser.profiles.append(
                        LegacyBrowserProfile(
                            name=prof_name,
                            path=prof_path,
                            categories=cats,
                            total_bytes=total,
                        )
                    )

                browser.total_bytes = sum(p.total_bytes for p in browser.profiles)
                if browser.total_bytes > 0:
                    results.append(browser)

        # Firefox-based browsers
        if browser_name != "Edge" or all_browsers:
            for name, rel in FIREFOX_BROWSERS:
                if browser_name and browser_name != name:
                    continue

                browser_dir = appdata / rel
                if not browser_dir.is_dir():
                    continue

                profiles_dir = browser_dir / "Profiles"
                if not profiles_dir.is_dir():
                    continue

                browser = LegacyBrowserResult(
                    name=name,
                    engine="firefox",
                    is_running=self._is_running(EXE_MAP.get(name, "")),
                )

                for prof_path in sorted(
                    p for p in profiles_dir.iterdir() if p.is_dir()
                ):
                    cats = self._scan_firefox_profile(prof_path)
                    total = sum(c.size_bytes for c in cats)
                    browser.profiles.append(
                        LegacyBrowserProfile(
                            name=prof_path.name,
                            path=prof_path,
                            categories=cats or [],
                            total_bytes=total,
                        )
                    )

                browser.total_bytes = sum(p.total_bytes for p in browser.profiles)
                if browser.total_bytes > 0:
                    results.append(browser)

        # Sort by size descending
        results.sort(key=lambda b: b.total_bytes, reverse=True)
        return results

    def _scan_chromium_profile(self, prof_path: Path) -> List[CacheCategory]:
        """Scan Chromium profile for cache categories."""
        categories = []
        for rel_sub, label in CHROMIUM_CACHE_CATEGORIES:
            p = prof_path / rel_sub
            exists = p.exists()
            size = self._dir_size(p) if exists else 0
            categories.append(
                CacheCategory(label=label, path=p, size_bytes=size, exists=exists)
            )
        return categories

    def _scan_firefox_profile(self, prof_path: Path) -> List[CacheCategory]:
        """Scan Firefox profile for cache categories."""
        categories = []
        for rel_sub, label in FIREFOX_CACHE_CATEGORIES:
            p = prof_path / rel_sub
            exists = p.exists()
            size = self._dir_size(p) if exists else 0
            categories.append(
                CacheCategory(label=label, path=p, size_bytes=size, exists=exists)
            )
        return categories

    def _dir_size(self, path: Path) -> int:
        """Calculate directory size."""
        total = 0
        try:
            for entry in path.rglob("*"):
                try:
                    if entry.is_file():
                        total += entry.stat().st_size
                except OSError:
                    logger.debug("Ignored OSError", exc_info=True)
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
        return total

    def delete_selected(
        self, categories: List[CacheCategory], progress_cb=None
    ) -> Tuple[int, int]:
        """Delete selected cache categories.

        Returns:
            Tuple of (freed_bytes: int, error_count: int)
        """
        freed = 0
        errors = 0
        for i, cat in enumerate(categories):
            if cat.path.exists():
                try:
                    if cat.path.is_dir():
                        shutil.rmtree(cat.path, ignore_errors=False)
                    else:
                        cat.path.unlink()
                    freed += cat.size_bytes
                except Exception:
                    errors += 1
            if progress_cb:
                progress_cb(i + 1)
        return freed, errors
