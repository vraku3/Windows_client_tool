"""CleanupModule's first tab is the merged Quick Cleanup dashboard, not
the old read-only Overview table -- and its 60s auto-refresh only
actually re-scans while that tab is the visible one.
"""
import tempfile
import time

from PyQt6.QtCore import QThreadPool

from modules.cleanup.cleanup_module import _CATEGORY_TAB_NAMES
from modules.cleanup.cleanup_scanner import ScanResult


def _fast_scan(min_age_days: int = 0) -> ScanResult:
    return ScanResult()


def _settle(qapp, timeout_ms: int = 10_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _stub_quick_cleanup_scanners(quick_tab) -> None:
    """Replace every scanner QuickCleanupTab's own scan()/auto_scan() would
    actually run with an instant fake, so switching TO the Quick Cleanup
    tab in a test starts no real ~101-category background sweep against
    the live filesystem. Matches tests/test_quick_cleanup_watchdog.py's own
    pattern of overriding `_scanner_map` entries.

    Without this, a real sweep's stragglers (workers QThreadPool.waitForDone
    didn't finish inside its window) can outlive the test and later deliver
    their signals into a freed widget during a DIFFERENT test's event pump
    -- a reproducible native access violation that kills the whole pytest
    process (Finding C1)."""
    for cid, (_fn, label, color) in list(quick_tab._scanner_map.items()):
        quick_tab._scanner_map[cid] = (_fast_scan, label, color)
    for cid, (_fn, label, color) in list(quick_tab._adv_scanner_map.items()):
        quick_tab._adv_scanner_map[cid] = (_fast_scan, label, color)
    quick_tab._browser_scanner = lambda: []


def _stub_scan_tab_scanners(scan_tab) -> None:
    """Same idea as _stub_quick_cleanup_scanners, for a real _ScanTab (e.g.
    System Junk) -- its own auto_scan() runs every scanner in its
    `_scanners` dict (75+ for System Junk) against the live filesystem."""
    scan_tab._scanners = {_fast_scan: ("Fake", "safe")}


def _module(qapp):
    from app import App
    from modules.cleanup.cleanup_module import CleanupModule

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = CleanupModule()
    module.on_start(app)
    widget = module.create_widget()
    module._keep_alive = widget
    _stub_quick_cleanup_scanners(module._quick)
    # Every other real _ScanTab in this module -- App & Game Caches in
    # particular merges six catalog categories (apps/games/media/comms/
    # cloud/browsers) into one dict, on the same order of scale as System
    # Junk's own sweep. This module's tests switch to every tab (see
    # test_every_category_tab_name_mapping_resolves_to_a_real_tab), so all
    # of them need stubbing, not just the one tab any single test happens
    # to name.
    for scan_tab in (
        module._sys_tab, module._app_tab, module._wu_tab,
        module._logs_tab, module._large._scan_tab, module._dev_tab,
    ):
        _stub_scan_tab_scanners(scan_tab)
    return module, app


def test_the_first_tab_is_quick_cleanup(qapp):
    module, app = _module(qapp)
    try:
        assert module._tabs.tabText(0) == "Quick Cleanup"
        assert module._tabs.widget(0) is module._quick
    finally:
        app.shutdown()


def test_clicking_a_category_card_switches_to_its_tab(qapp):
    module, app = _module(qapp)
    try:
        module._tabs.setCurrentIndex(0)
        module._quick._handle_category_clicked("browser")
        assert module._tabs.tabText(module._tabs.currentIndex()) == "Browser Caches"
        _settle(qapp)
    finally:
        app.shutdown()


def test_refresh_data_only_rescans_when_quick_tab_is_visible(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        calls = []
        monkeypatch.setattr(module._quick, "scan", lambda: calls.append(1))

        module._tabs.setCurrentIndex(1)  # System Junk, not Quick Cleanup
        module.refresh_data()
        assert calls == [], "refresh_data rescanned a tab that wasn't visible"

        module._tabs.setCurrentIndex(0)  # Quick Cleanup
        module.refresh_data()
        assert calls == [1]

        # Both setCurrentIndex calls above went through _on_tab_changed,
        # which calls the newly-current tab's own (real, un-monkeypatched)
        # auto_scan() -- System Junk's _ScanTab and then Quick Cleanup's
        # _do_scan_all() (only .scan() was monkeypatched above, not
        # .auto_scan()). Settle before teardown so those real background
        # scans finish and deliver their results while the widget tree is
        # still alive, instead of landing during a later test's own
        # QThreadPool.waitForDone() -- see
        # test_clicking_a_category_card_switches_to_its_tab.
        _settle(qapp)
    finally:
        app.shutdown()


def test_get_refresh_interval_is_60_seconds(qapp):
    module, app = _module(qapp)
    try:
        assert module.get_refresh_interval() == 60_000
    finally:
        app.shutdown()


def test_every_category_tab_name_mapping_resolves_to_a_real_tab(qapp):
    """_CATEGORY_TAB_NAMES maps 10 Quick Cleanup category ids to tab
    display-name strings, matched by exact string equality against
    self._tabs.tabText(i) in _on_category_clicked. A typo in either this
    dict or quick_cleanup_tab.py's CLEANUP_CATEGORIES would silently no-op
    a category click for that category -- only "browser" had coverage
    before this test (see test_clicking_a_category_card_switches_to_its_tab
    above); this exercises all 10."""
    module, app = _module(qapp)
    try:
        for category_id, expected_tab_name in _CATEGORY_TAB_NAMES.items():
            module._quick._handle_category_clicked(category_id)
            actual = module._tabs.tabText(module._tabs.currentIndex())
            assert actual == expected_tab_name, (
                f"category {category_id!r} navigated to tab {actual!r}, "
                f"expected {expected_tab_name!r}")
        _settle(qapp)
    finally:
        app.shutdown()
