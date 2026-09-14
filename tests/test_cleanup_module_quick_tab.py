"""CleanupModule's first tab is the merged Quick Cleanup dashboard, not
the old read-only Overview table -- and its 60s auto-refresh only
actually re-scans while that tab is the visible one.
"""
import tempfile
import time

from PyQt6.QtCore import QThreadPool


def _settle(qapp, timeout_ms: int = 10_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _module(qapp):
    from app import App
    from modules.cleanup.cleanup_module import CleanupModule

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = CleanupModule()
    module.on_start(app)
    widget = module.create_widget()
    module._keep_alive = widget
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
