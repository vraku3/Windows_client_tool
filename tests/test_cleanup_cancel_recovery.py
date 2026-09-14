r"""A cancelled Cleanup scan must leave the tab usable.

`main_window._on_module_selected` calls `on_deactivate()` on the module you
are leaving, and `CleanupModule.on_deactivate` cancels every in-flight scan
worker. `Worker.cancel()` only sets a flag: `Worker.run` then emits
`cancelled` and NEVER `result` or `error` (core/worker.py). Nothing was
connected to `cancelled`, so the tab's completion counter never reached
zero, `_scanning` was never cleared, and `_cancel_all` reset none of it.

The tab was then dead for the life of the process -- progress bar up,
status stuck on "Scanning...", Scan button disabled, and `_do_scan*`
returning early on `if self._scanning`. Measured on the real app: click
Cleanup, glance at another module while it scans, come back to a tab that
never finishes. That is the "it scanned for over 30 minutes" report; the
scan itself takes 6-36 seconds.
"""
import threading
import time

import pytest
from PyQt6.QtCore import QThreadPool

from modules.cleanup.cleanup_scanner import ScanResult


def _settle(qapp, timeout_ms: int = 30_000) -> None:
    """Let every worker finish and every queued signal be delivered."""
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 2
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def blocking_scanner():
    """A scanner the test can hold open, so cancel really lands mid-scan."""
    started = threading.Event()
    release = threading.Event()

    def _scan(min_age_days: int = 0) -> ScanResult:
        started.set()
        release.wait(30)
        return ScanResult()

    _scan.started = started
    _scan.release = release
    return _scan


def test_quick_cleanup_is_usable_again_after_a_scan_is_cancelled(
        qapp, blocking_scanner):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (blocking_scanner, "Temp Files", "#4caf50")
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10), "scan never started"

    tab.cancel()                    # what switching modules does
    blocking_scanner.release.set()
    _settle(qapp)

    assert tab._scanning is False
    assert tab._scan_all_btn.isEnabled(), "Scan button left disabled forever"


def test_a_cancelled_quick_cleanup_scan_can_be_started_again(
        qapp, blocking_scanner):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (blocking_scanner, "Temp Files", "#4caf50")
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10)
    tab.cancel()
    blocking_scanner.release.set()
    _settle(qapp)

    blocking_scanner.release.set()
    tab._do_scan_all()

    # Not "the scanner function ran again": a measurement taken seconds ago
    # is legitimately served from the shared scan cache. What must be true
    # is that the tab ACCEPTED the request instead of returning early on a
    # stuck `_scanning`, and that it reached a finished state.
    assert tab._scanning is True, "Scan did nothing after a cancel"
    _settle(qapp)
    assert tab._scanning is False
    assert tab._scan_all_btn.isEnabled()


def test_scan_tab_is_usable_again_after_a_scan_is_cancelled(
        qapp, blocking_scanner):
    from modules.cleanup.tabs._scan_tab import _ScanTab

    tab = _ScanTab({blocking_scanner: ("Slow", "safe")})
    tab._do_scan()
    assert blocking_scanner.started.wait(10), "scan never started"

    tab._cancel_all()
    blocking_scanner.release.set()
    _settle(qapp)

    assert tab._scanning is False
    assert tab._scan_btn.isEnabled(), "Scan button left disabled forever"


def test_a_cancelled_scan_tab_rescans_on_next_activation(
        qapp, blocking_scanner):
    """A cancelled scan produced no results, so it must not count as scanned."""
    from modules.cleanup.tabs._scan_tab import _ScanTab

    tab = _ScanTab({blocking_scanner: ("Slow", "safe")})
    tab.auto_scan()
    assert blocking_scanner.started.wait(10)
    tab._cancel_all()
    blocking_scanner.release.set()
    _settle(qapp)

    blocking_scanner.release.set()
    tab.auto_scan()

    # `_scanned` must have been cleared by the cancel, or auto_scan is a
    # no-op and the tab shows nothing for the rest of the session. The
    # measurement itself may come from the shared scan cache.
    assert tab._scanning is True, "tab never rescanned"
    _settle(qapp)
    assert tab._scanning is False
    assert tab._result is not None
