"""CleanupModule's dashboard (Quick Cleanup) is now an always-visible
header, not a tab -- the other seven former tabs are collapsible sections
on the same page (see docs/superpowers/specs/
2026-09-20-cleanup-single-tab-merge-design.md). Its 60s auto-refresh
always rescans the header now, since it's no longer possible to navigate
away from it the way a QTabWidget tab could be.
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
    actually run with an instant fake, so activating the module in a test
    starts no real ~101-category background sweep against the live
    filesystem. Matches tests/test_quick_cleanup_watchdog.py's own pattern
    of overriding `_scanner_map` entries.

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
    # Every real _ScanTab in this module -- App & Game Caches in particular
    # merges six catalog categories (apps/games/media/comms/cloud/browsers)
    # into one dict, on the same order of scale as System Junk's own
    # sweep. This module's tests expand every section (see
    # test_every_category_tab_name_mapping_resolves_to_a_real_section), so
    # all of them need stubbing, not just the one section any single test
    # happens to name.
    for scan_tab in (
        module._sys_tab, module._app_tab, module._wu_tab,
        module._logs_tab, module._large._scan_tab, module._dev_tab,
    ):
        _stub_scan_tab_scanners(scan_tab)
    return module, app


def test_quick_cleanup_is_always_visible_above_the_sections(qapp):
    module, app = _module(qapp)
    try:
        assert module._quick.parent() is not None
        assert set(module._sections.keys()) == {
            "System Junk", "Browser Caches", "App & Game Caches",
            "Windows Update", "Logs & Reports", "Large Items", "Dev Tools",
        }
    finally:
        app.shutdown()


def test_clicking_a_category_card_expands_its_section(qapp):
    module, app = _module(qapp)
    try:
        assert module._sections["Browser Caches"].is_expanded() is False
        module._quick._handle_category_clicked("browser")
        assert module._sections["Browser Caches"].is_expanded() is True
        _settle(qapp)
    finally:
        app.shutdown()


def test_expanding_a_section_triggers_its_auto_scan_exactly_once(qapp):
    module, app = _module(qapp)
    try:
        calls = []
        # _CollapsibleSection is connected to the ORIGINAL bound
        # module._sys_tab.auto_scan at wiring time -- replacing that
        # attribute afterward would not rewire the already-made connection.
        # auto_scan() itself calls self._do_scan(), a fresh attribute
        # lookup on self at call time, so stubbing _do_scan (not auto_scan)
        # is what a monkeypatch after construction can actually observe.
        def _fake_do_scan():
            calls.append(1)
            module._sys_tab._scanned = True  # real _do_scan's own contract
        module._sys_tab._do_scan = _fake_do_scan
        module._sections["System Junk"].set_expanded(True)
        assert calls == [1]
        module._sections["System Junk"].set_expanded(False)
        module._sections["System Junk"].set_expanded(True)
        assert calls == [1], "re-expanding an already-scanned section re-scanned it"
    finally:
        app.shutdown()


def test_refresh_data_always_rescans_the_always_visible_header(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        calls = []
        monkeypatch.setattr(module._quick, "scan", lambda: calls.append(1))
        module.refresh_data()
        assert calls == [1]
    finally:
        app.shutdown()


def test_get_refresh_interval_is_60_seconds(qapp):
    module, app = _module(qapp)
    try:
        assert module.get_refresh_interval() == 60_000
    finally:
        app.shutdown()


def test_every_category_tab_name_mapping_resolves_to_a_real_section(qapp):
    """_CATEGORY_TAB_NAMES maps 10 Quick Cleanup category ids to section
    title strings, matched by exact string equality against
    self._sections keys in _on_category_clicked. A typo in either this
    dict or quick_cleanup_tab.py's CLEANUP_CATEGORIES would silently no-op
    a category click for that category -- only "browser" had coverage
    before this test (see test_clicking_a_category_card_expands_its_section
    above); this exercises all 10."""
    module, app = _module(qapp)
    try:
        for category_id, expected_section_name in _CATEGORY_TAB_NAMES.items():
            module._quick._handle_category_clicked(category_id)
            assert module._sections[expected_section_name].is_expanded() is True, (
                f"category {category_id!r} did not expand "
                f"{expected_section_name!r}")
        _settle(qapp)
    finally:
        app.shutdown()


def test_cancel_all_tabs_still_reaches_every_section_widget(qapp):
    """_cancel_all_tabs() iterates fixed attribute names
    (_quick/_sys_tab/_browser/... ), not self._sections -- this pins that
    those attributes still exist with working _cancel_all()/cancel() after
    the QTabWidget removal, since on_stop()/on_deactivate() depend on it
    and nothing else in this file re-tests it."""
    module, app = _module(qapp)
    try:
        module.on_deactivate()  # must not raise
        module.on_stop()        # must not raise
    finally:
        app.shutdown()
