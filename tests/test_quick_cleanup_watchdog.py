r"""QuickCleanupTab's scan loop had no backstop for a scan that never
finishes -- _OverviewTab and _ScanTab both already needed one (see
tests/test_cleanup_scan_watchdog.py), and QuickCleanupTab was the one
tab in this module missing it.
"""
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
    import threading
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
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50"), ("prefetch", "Prefetch", "#ffb74d")],
             advanced_categories=[])

    def scan_fast_prefetch(min_age_days: int = 0) -> ScanResult:
        return ScanResult()

    tab._scanner_map["temp"] = (blocking_scanner, "Temp Files", "#4caf50")
    tab._scanner_map["prefetch"] = (scan_fast_prefetch, "Prefetch", "#ffb74d")
    tab.SCAN_WATCHDOG_MS = 300
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10)

    recovered = _pump_until(qapp, lambda: not tab._scanning)
    status = tab._status_lbl.text()
    blocking_scanner.release.set()
    _settle(qapp)

    assert recovered, "the watchdog never fired"
    assert tab._scan_all_btn.isEnabled()
    assert tab._scanned is False, "a scan that never finished must not count as scanned"
    assert "Temp Files" in status, (
        f"the watchdog did not name what it was stuck on: {status!r}")
    assert "Prefetch" not in status, (
        f"a category that DID finish was named as stuck too: {status!r}")


def test_the_watchdog_does_not_fire_on_a_healthy_scan(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    def scan_fast(min_age_days: int = 0) -> ScanResult:
        return ScanResult()

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (scan_fast, "Temp Files", "#4caf50")
    tab.SCAN_WATCHDOG_MS = 5_000
    tab._do_scan_all()
    _settle(qapp)

    assert tab._scanning is False
    assert tab._scanned is True
    assert tab._watchdog.isActive() is False, "watchdog left armed after a scan"


def test_auto_scan_is_a_no_op_once_already_scanned(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    calls = []

    def scan_fast(min_age_days: int = 0) -> ScanResult:
        calls.append(1)
        return ScanResult()

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (scan_fast, "Temp Files", "#4caf50")

    tab.auto_scan()
    _settle(qapp)
    assert len(calls) == 1

    tab.auto_scan()  # second call must be a no-op
    _settle(qapp)
    assert len(calls) == 1, "auto_scan re-scanned even though _scanned was already True"


def test_freed_bytes_is_emitted_after_a_successful_clean(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from modules.cleanup import cleanup_scanner as cs
    from modules.cleanup import clean_safe_runner as csr

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    item = cs.ScanItem(path=r"C:\fake\path", size=500, is_dir=False, safety="safe")
    result = cs.ScanResult()
    result.items = [item]
    tab._results = {"temp": result}

    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))
    monkeypatch.setattr(csr.QMessageBox, "exec", lambda self: csr.QMessageBox.StandardButton.Ok)

    emitted = []
    tab.freed_bytes.connect(emitted.append)
    tab._do_clean_all_safe()
    _settle(qapp)

    assert emitted == [500]
