"""Real, live bug found 2026-09-22: CHROMIUM_CACHE_SUBDIRS (used by
BrowserScanner2.scan_chromium_browser, reached from the real Browser
Caches tab via _browser_tab.py -> scan_browsers_robust() ->
BrowserScanner2().scan()) used to include "Local Storage", "Sessions",
"Tabs", "Local App Settings" and "Web Application History" -- and
_browser_tab.py pre-CHECKS every CacheEntry it finds by default
(setCheckState(..., Checked)), with no per-entry safety tier at all.

"Local Storage" specifically is where a growing number of real sites keep
session/login state instead of (or alongside) cookies. A user opening
this tab and clicking Clean without manually unchecking anything would
have deleted that for every profile in every Chromium browser installed.
No test covered this path at all before this file -- which is how it
went unnoticed.
"""
import os

from modules.cleanup import browser_scanner as bs


#: Real Chromium subfolders that hold actual site/user state, never
#: disposable cache, regardless of how plausible the name sounds.
_NEVER_CACHE = {
    "Local Storage", "Session Storage", "Sessions", "Tabs",
    "Local App Settings", "Web Application History",
    "Cookies", "Login Data", "Web Data", "History", "Bookmarks",
    "Preferences", "IndexedDB",
}


def test_chromium_cache_subdirs_never_includes_real_site_state():
    offenders = _NEVER_CACHE & set(bs.CHROMIUM_CACHE_SUBDIRS)
    assert offenders == set(), (
        f"CHROMIUM_CACHE_SUBDIRS includes real site/user state, not cache: "
        f"{offenders} -- BrowserScanner2.scan_chromium_browser() reaches "
        f"this dict from the real, live Browser Caches tab, and "
        f"_browser_tab.py pre-checks every entry it finds for deletion "
        f"by default.")


def test_firefox_cache_subdirs_never_includes_real_site_state():
    offenders = _NEVER_CACHE & set(bs.FIREFOX_CACHE_SUBDIRS)
    assert offenders == set()


def test_chromium_cache_categories_never_includes_real_site_state():
    """The OTHER, already-correctly-scoped Chromium list (used by
    EnhancedBrowserScanner/detect_browsers) -- pinned too, so it can't
    quietly regress to match CHROMIUM_CACHE_SUBDIRS's old mistake."""
    names = {label for _path, label in bs.CHROMIUM_CACHE_CATEGORIES}
    paths = {path for path, _label in bs.CHROMIUM_CACHE_CATEGORIES}
    assert not (_NEVER_CACHE & names)
    assert not (_NEVER_CACHE & paths)


def test_scan_chromium_browser_never_reports_a_local_storage_entry(tmp_path):
    """End-to-end: a real profile directory containing both a genuine
    cache folder and a Local Storage folder must only ever report the
    cache one."""
    profile = tmp_path / "User Data" / "Default"
    (profile / "Cache").mkdir(parents=True)
    (profile / "Cache" / "data_1").write_bytes(b"x" * 1024)
    (profile / "Local Storage").mkdir()
    (profile / "Local Storage" / "leveldb").mkdir()
    (profile / "Local Storage" / "leveldb" / "000003.log").write_bytes(
        b"real-session-token-data")

    scanner = bs.BrowserScanner2()
    result = scanner.scan_chromium_browser(
        "Chrome", tmp_path / "User Data", use_appdata=False)

    reported_labels = {
        entry.label
        for profile_result in result.profiles
        for entry in profile_result.caches
    }
    assert "Local Storage" not in reported_labels
    assert "Tab Sessions" not in reported_labels
    assert "Tab Data" not in reported_labels
