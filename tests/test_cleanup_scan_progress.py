r"""A scan must say what it is doing, and Stop must actually stop it.

_ScanTab ran every scanner serially in one worker behind an indeterminate
4px bar and the word "Scanning...". System Junk is 75 scanners and App &
Game Caches is 301; measured elevated, System Junk took 35.6 seconds. Half
a minute of silence is indistinguishable from a hang, which is most of why
"it scanned for over 30 minutes" was believable.

There was also no way to stop it. `Worker.cancel()` only sets a flag —
nothing in the scan loop read it, so a cancelled sweep ran to completion
anyway, holding the shared thread pool.
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


def _pump(qapp, seconds: float = 1.0) -> None:
    """Deliver queued signals without waiting for the pool to drain."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.005)


@pytest.fixture
def blocking_scanner():
    started = threading.Event()
    release = threading.Event()

    def scan_blocking(min_age_days: int = 0) -> ScanResult:
        started.set()
        release.wait(30)
        return ScanResult()

    scan_blocking.started = started
    scan_blocking.release = release
    return scan_blocking


def scan_quick(min_age_days: int = 0) -> ScanResult:
    return ScanResult()


def test_the_status_names_the_scanner_it_is_running(qapp, blocking_scanner):
    from modules.cleanup.tabs._scan_tab import _ScanTab

    tab = _ScanTab({
        scan_quick: ("Quick One", "safe"),
        blocking_scanner: ("Slow Two", "safe"),
    })
    tab._do_scan()
    assert blocking_scanner.started.wait(10)
    _pump(qapp)

    status = tab._status.text()
    blocking_scanner.release.set()
    _settle(qapp)

    assert "2/2" in status, f"no scanner count in the status: {status!r}"
    assert "Slow Two" in status, f"status does not name the scanner: {status!r}"


def test_the_progress_bar_is_determinate_over_the_scanner_count(
        qapp, blocking_scanner):
    from modules.cleanup.tabs._scan_tab import _ScanTab

    tab = _ScanTab({
        scan_quick: ("Quick One", "safe"),
        blocking_scanner: ("Slow Two", "safe"),
    })
    tab._do_scan()
    assert blocking_scanner.started.wait(10)
    _pump(qapp)

    maximum = tab._progress.maximum()
    blocking_scanner.release.set()
    _settle(qapp)

    assert maximum == 2, f"indeterminate bar (maximum={maximum})"


def test_stop_skips_the_scanners_that_have_not_run_yet(qapp, blocking_scanner):
    from modules.cleanup.tabs._scan_tab import _ScanTab

    ran_after = threading.Event()

    def scan_after_the_slow_one(min_age_days: int = 0) -> ScanResult:
        ran_after.set()
        return ScanResult()

    tab = _ScanTab({
        blocking_scanner: ("Slow One", "safe"),
        scan_after_the_slow_one: ("Should Be Skipped", "safe"),
    })
    tab._do_scan()
    assert blocking_scanner.started.wait(10)

    tab._stop_scan()
    blocking_scanner.release.set()
    _settle(qapp)

    assert not ran_after.is_set(), (
        "Stop did not stop: the scan carried on to the next scanner")
    assert tab._scanning is False


def test_the_stop_button_is_live_only_while_a_scan_is_running(
        qapp, blocking_scanner):
    from modules.cleanup.tabs._scan_tab import _ScanTab

    tab = _ScanTab({blocking_scanner: ("Slow One", "safe")})
    assert not tab._stop_btn.isEnabled(), "Stop is live before any scan"

    tab._do_scan()
    assert blocking_scanner.started.wait(10)
    _pump(qapp, 0.3)
    assert tab._stop_btn.isEnabled(), "Stop is dead during a scan"

    blocking_scanner.release.set()
    _settle(qapp)
    assert not tab._stop_btn.isEnabled(), "Stop is still live after the scan"
