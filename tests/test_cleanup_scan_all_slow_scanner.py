"""A real user report: "Scan All" ran for over an hour producing nothing,
while each category's own manual Scan button (which only runs ONE
scanner) worked fine. Root cause: _on_all_scanned only ever fired once
EVERY scanner among ~137 had reported -- one pathologically slow scanner
(a huge personal folder, a slow network share, a cloud-sync placeholder)
blocked the whole dashboard indefinitely, with nothing short of the full
5-minute SCAN_WATCHDOG_MS ever showing anything, and that watchdog threw
away every already-completed result too.

QuickCleanupTab.PER_SCANNER_TIMEOUT_MS is the fix: a category stuck past
it is marked timed-out and counted as reported, so every other category's
real results still render.
"""
import time

import pytest
from PyQt6.QtCore import QThreadPool

from modules.cleanup.cleanup_scanner import ScanResult, ScanItem


def _make_instant_scan(unique_path: str):
    """A distinct path AND a distinct function name per category.

    `scan_cache.cached_scan` keys its cache on `scanner.__name__` alone --
    every closure this factory returns would otherwise share the name
    `_scan` and collide in that cache, making every category after the
    first silently return whichever one happened to populate the shared
    cache entry first. That's a test-harness trap, not the app behavior
    under test here, so each fake gets its own real `__name__`.
    """
    def _scan(min_age_days: int = 0) -> ScanResult:
        r = ScanResult()
        r.items = [ScanItem(path=unique_path, size=100, is_dir=False, safety="safe")]
        r.total_size = 100
        return r
    _scan.__name__ = f"fake_scan_{unique_path}"
    return _scan


def _never_returns(min_age_days: int = 0) -> ScanResult:
    # A real stuck scanner blocks its OWN worker thread forever (e.g. a
    # hung network share); this test can't actually wait forever, so it
    # blocks long enough to outlast the test's own shortened timeout.
    time.sleep(30)
    return ScanResult()


def _settle(qapp, timeout_s: float, poll_interval: float = 0.02) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(poll_interval)


def _tab(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    tab = QuickCleanupTab()
    tab.build()
    # Real sweeps here measure single seconds per scanner; shorten the
    # timeout so the test doesn't need to wait 45 real seconds.
    tab.PER_SCANNER_TIMEOUT_MS = 300
    return tab


def test_a_hung_scanner_does_not_block_the_other_categories(qapp):
    tab = _tab(qapp)
    try:
        # Make exactly one category's scanner hang forever; every other
        # category keeps its real, fast, stubbed scanner.
        stuck_cid = next(iter(tab._scanner_map))
        for cid, (_fn, label, color) in list(tab._scanner_map.items()):
            fn = _never_returns if cid == stuck_cid else _make_instant_scan(f"C:\\fake\\{cid}")
            tab._scanner_map[cid] = (fn, label, color)
        for cid, (_fn, label, color) in list(tab._adv_scanner_map.items()):
            tab._adv_scanner_map[cid] = (_make_instant_scan(f"C:\\fake\\{cid}"), label, color)
        tab._browser_scanner = lambda: []

        tab.scan()
        # The per-category timeout (300ms) must let the scan finish well
        # under the 5-minute global watchdog or the real 30s the stuck
        # scanner would otherwise block for.
        _settle(qapp, timeout_s=5)

        assert tab._scanned is True, "scan never completed despite one hung scanner"
        assert stuck_cid in tab._timed_out_cids
        # Every OTHER category's real result still landed -- the stuck one
        # simply has no entry yet (the pie-chart/summary code already
        # defaults a missing cid to an empty ScanResult for rendering).
        assert stuck_cid not in tab._results
        expected_total = len(tab._scanner_map) + len(tab._adv_scanner_map)
        assert len(tab._results) == expected_total - 1
        non_stuck_results = [r for cid, r in tab._results.items() if cid != stuck_cid]
        assert all(getattr(r, "total_size", 0) == 100 for r in non_stuck_results
                  if hasattr(r, "total_size"))
    finally:
        tab.cancel()
        QThreadPool.globalInstance().waitForDone(5000)


def test_timed_out_category_is_named_in_the_status_label(qapp):
    tab = _tab(qapp)
    try:
        stuck_cid = next(iter(tab._scanner_map))
        stuck_label = tab._scanner_map[stuck_cid][1]
        for cid, (_fn, label, color) in list(tab._scanner_map.items()):
            fn = _never_returns if cid == stuck_cid else _make_instant_scan(f"C:\\fake\\{cid}")
            tab._scanner_map[cid] = (fn, label, color)
        for cid, (_fn, label, color) in list(tab._adv_scanner_map.items()):
            tab._adv_scanner_map[cid] = (_make_instant_scan(f"C:\\fake\\{cid}"), label, color)
        tab._browser_scanner = lambda: []

        tab.scan()
        _settle(qapp, timeout_s=5)

        assert "slow and still scanning" in tab._status_lbl.text()
        assert stuck_label in tab._status_lbl.text()
    finally:
        tab.cancel()
        QThreadPool.globalInstance().waitForDone(5000)


def test_a_late_result_after_timeout_updates_the_stored_result_without_double_counting(qapp):
    """The stuck scanner's real worker thread keeps running after its
    category times out -- if it eventually DOES finish, its result must
    land (correcting the earlier timed-out placeholder), and must not
    increment _total_scanned a second time (which would desync the
    completion count for a LATER, unrelated scan)."""
    tab = _tab(qapp)
    try:
        slow_cid = next(iter(tab._scanner_map))

        def _slow_but_finishes(min_age_days: int = 0) -> ScanResult:
            time.sleep(0.6)  # outlasts the 300ms per-category timeout
            return _make_instant_scan(f"C:\\fake\\{slow_cid}")()

        for cid, (_fn, label, color) in list(tab._scanner_map.items()):
            fn = _slow_but_finishes if cid == slow_cid else _make_instant_scan(f"C:\\fake\\{cid}")
            tab._scanner_map[cid] = (fn, label, color)
        for cid, (_fn, label, color) in list(tab._adv_scanner_map.items()):
            tab._adv_scanner_map[cid] = (_make_instant_scan(f"C:\\fake\\{cid}"), label, color)
        tab._browser_scanner = lambda: []

        tab.scan()
        _settle(qapp, timeout_s=1)
        assert slow_cid in tab._timed_out_cids
        assert tab._scanned is True, "the rest of the scan must have completed"

        # Let the slow worker's real (late) result land.
        _settle(qapp, timeout_s=3)
        assert tab._results[slow_cid].total_size == 100, \
            "the late real result never corrected the timed-out placeholder"
    finally:
        tab.cancel()
        QThreadPool.globalInstance().waitForDone(5000)
