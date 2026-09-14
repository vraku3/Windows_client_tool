r"""A scan finishing after its tab is gone must not take the app down.

QuickCleanupTab and _ScanTab connect **closures** to their workers'
signals, not bound methods. Qt auto-disconnects a bound method when its
receiver QObject is destroyed; it cannot do that for a closure, because
nothing tells it what the receiver is. So the connection outlives the
widget, and a scan that lands after teardown reaches into a deleted C++
object.

Found by a crash: two tabs from earlier tests were collected while their
workers were still running, and the next test that pumped the event loop
died with a faulthandler dump inside `_res`. In the app the same shape is
reachable at shutdown, and on any path that destroys the module's widget
while a sweep is in flight — which is exactly what a slow scan makes
likely.
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


@pytest.fixture
def blocking_scanner():
    started = threading.Event()
    release = threading.Event()

    def scan_slow(min_age_days: int = 0) -> ScanResult:
        started.set()
        release.wait(30)
        return ScanResult()

    scan_slow.started = started
    scan_slow.release = release
    return scan_slow


def test_a_quick_cleanup_result_landing_after_teardown_is_dropped(
        qapp, blocking_scanner):
    from PyQt6 import sip
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")],
              advanced_categories=[])
    tab._scanner_map["temp"] = (blocking_scanner, "Temp Files", "#4caf50")
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10)

    sip.delete(tab)
    blocking_scanner.release.set()
    _settle(qapp)

    assert True
