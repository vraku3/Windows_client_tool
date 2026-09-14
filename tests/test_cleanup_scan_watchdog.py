r"""A scan that never finishes must give up and say where it got stuck.

The cancelled-worker bug (test_cleanup_cancel_recovery.py) is fixed, but it
was only one way for a completion to go missing, and its symptom -- a tab
spinning on "Scanning..." with a disabled button, for the life of the
process -- is bad enough to be worth a backstop that does not depend on
guessing every cause in advance.

Measured worst case on this machine is 35.6s (System Junk, elevated), so a
watchdog in the minutes cannot fire on a healthy scan; if it does fire,
something is genuinely wrong and the user needs the tab back plus a
sentence naming what did not report.
"""
import threading
import time

import pytest
from PyQt6.QtCore import QThreadPool

from modules.cleanup.cleanup_scanner import ScanResult


def _settle(qapp, timeout_ms: int = 30_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 2
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _pump_until(qapp, predicate, seconds: float = 10.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def blocking_scanner():
    started = threading.Event()
    release = threading.Event()

    def scan_that_never_returns(min_age_days: int = 0) -> ScanResult:
        started.set()
        release.wait(30)
        return ScanResult()

    scan_that_never_returns.started = started
    scan_that_never_returns.release = release
    return scan_that_never_returns


def test_a_scan_that_never_finishes_gives_the_tab_back(qapp, blocking_scanner):
    from modules.cleanup.tabs._scan_tab import _ScanTab

    tab = _ScanTab({blocking_scanner: ("Stuck Scanner", "safe")})
    tab.SCAN_WATCHDOG_MS = 300
    tab._do_scan()
    assert blocking_scanner.started.wait(10)

    recovered = _pump_until(qapp, lambda: not tab._scanning)
    status = tab._status.text()
    blocking_scanner.release.set()
    _settle(qapp)

    assert recovered, "the watchdog never fired"
    assert tab._scan_btn.isEnabled()
    assert "Stuck Scanner" in status, (
        f"the watchdog did not name what it was stuck on: {status!r}")


def test_the_watchdog_does_not_fire_on_a_healthy_scan(qapp):
    from modules.cleanup.tabs._scan_tab import _ScanTab

    def scan_fast(min_age_days: int = 0) -> ScanResult:
        return ScanResult()

    tab = _ScanTab({scan_fast: ("Fast", "safe")})
    tab.SCAN_WATCHDOG_MS = 5_000
    tab._do_scan()
    _settle(qapp)

    assert tab._scanning is False
    assert "timed out" not in tab._status.text().lower()
    assert tab._watchdog.isActive() is False, "watchdog left armed after a scan"


def test_the_quick_cleanup_watchdog_recovers_a_wedged_scan(
        qapp, blocking_scanner):
    # QuickCleanupTab's own dedicated watchdog tests (naming which
    # category is stuck, not just that it recovers) live in
    # tests/test_quick_cleanup_watchdog.py, migrated there directly
    # rather than duplicated here -- this file's remaining coverage is
    # _ScanTab's watchdog, above.
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (blocking_scanner, "Temp Files", "#4caf50")
    tab.SCAN_WATCHDOG_MS = 300
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10)

    recovered = _pump_until(qapp, lambda: not tab._scanning)
    blocking_scanner.release.set()
    _settle(qapp)

    assert recovered, "the watchdog never fired"
    assert tab._scan_all_btn.isEnabled()
