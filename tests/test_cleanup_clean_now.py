"""The module-level "Clean Now" button -- one action, reachable from any
of the 8 Cleanup tabs, that reuses QuickCleanupTab's own scan/confirm/
delete pipeline rather than a second implementation of it.
"""
import tempfile
import time

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QMessageBox

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
    for cid, (_fn, label, color) in list(quick_tab._scanner_map.items()):
        quick_tab._scanner_map[cid] = (_fast_scan, label, color)
    for cid, (_fn, label, color) in list(quick_tab._adv_scanner_map.items()):
        quick_tab._adv_scanner_map[cid] = (_fast_scan, label, color)
    quick_tab._browser_scanner = lambda: []


def _stub_scan_tab_scanners(scan_tab) -> None:
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
    for scan_tab in (
        module._sys_tab, module._app_tab, module._wu_tab,
        module._logs_tab, module._large._scan_tab, module._dev_tab,
    ):
        _stub_scan_tab_scanners(scan_tab)
    return module, app


def test_clean_now_button_exists_and_is_wired(qapp):
    module, app = _module(qapp)
    try:
        assert module._clean_now_btn.text() == "🧹  Clean Now"
        assert module._clean_now_btn.objectName() == "accentButton"
    finally:
        app.shutdown()


def test_clean_now_switches_to_quick_cleanup_tab(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        module._tabs.setCurrentIndex(1)  # System Junk
        monkeypatch.setattr(module._quick, "clean_now", lambda: None)
        module._clean_now_btn.click()
        assert module._tabs.currentWidget() is module._quick
        # setCurrentIndex above fired System Junk's own auto_scan() against
        # the stubbed-but-still-real-Worker scanners -- settle before
        # teardown so that scan can't outlive this test and land in a
        # later test's event pump (see test_cleanup_module_quick_tab.py's
        # own Finding C1 note; reproduced here without this).
        _settle(qapp)
    finally:
        app.shutdown()


def test_clean_now_before_any_scan_scans_then_cleans(qapp, monkeypatch):
    """The button must work from a module that was never opened to Quick
    Cleanup before -- clean_now() cannot assume _results is populated."""
    module, app = _module(qapp)
    try:
        quick = module._quick
        assert quick._scanned is False

        cleaned = []
        monkeypatch.setattr(quick, "_do_clean_all_safe", lambda: cleaned.append(1))

        quick.clean_now()
        _settle(qapp)

        assert quick._scanned is True
        assert cleaned == [1]
    finally:
        app.shutdown()


def test_clean_now_after_a_scan_cleans_directly_without_rescanning(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        quick = module._quick
        quick.scan()
        _settle(qapp)
        assert quick._scanned is True

        scans = []
        cleaned = []
        monkeypatch.setattr(quick, "_do_scan_all", lambda: scans.append(1))
        monkeypatch.setattr(quick, "_do_clean_all_safe", lambda: cleaned.append(1))

        quick.clean_now()

        assert scans == [], "clean_now() rescanned an already-scanned tab"
        assert cleaned == [1]
    finally:
        app.shutdown()


def test_clean_now_is_a_noop_while_a_scan_is_already_running(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        quick = module._quick
        quick._scanning = True

        scans = []
        cleaned = []
        monkeypatch.setattr(quick, "_do_scan_all", lambda: scans.append(1))
        monkeypatch.setattr(quick, "_do_clean_all_safe", lambda: cleaned.append(1))

        quick.clean_now()

        assert scans == [] and cleaned == []
    finally:
        app.shutdown()


def test_a_cancelled_scan_disarms_the_pending_clean(qapp):
    """clean_now() on an unscanned tab arms _clean_after_scan and starts a
    scan; if that scan is cancelled before finishing, the flag must not
    stay armed and fire a clean on some LATER, unrelated scan."""
    module, app = _module(qapp)
    try:
        quick = module._quick
        quick.clean_now()
        assert quick._clean_after_scan is True

        quick.cancel()
        assert quick._clean_after_scan is False

        _settle(qapp)
    finally:
        app.shutdown()


def test_clean_now_with_nothing_to_clean_does_not_raise(qapp, monkeypatch):
    """Every stubbed scanner returns an empty ScanResult, so a real
    clean_now() call (not the monkeypatched-around-_do_clean_all_safe
    version the other tests use) must reach _do_clean_all_safe's own
    early-return for "nothing safe found" without a confirm dialog or
    an exception."""
    module, app = _module(qapp)
    try:
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.Yes)
        quick = module._quick
        quick.clean_now()
        _settle(qapp)
        assert quick._scanned is True
    finally:
        app.shutdown()
