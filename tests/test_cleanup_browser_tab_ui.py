"""Browser Caches tab (WP5): research rows, states, ticks, and measured cleaning.

The tab only decides what is ticked; deletion still goes through
``browser_scanner.delete_cache_entries`` -> ``delete_items_report``
(pinned by test_browser_cleanup_measured.py).
"""
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

from modules.cleanup import browser_scanner as bs
from modules.cleanup.components.item_rows import FACTS_ROLE, SAFETY_ROLE
from modules.cleanup.tabs import _browser_tab as bt


class _SyncPool:
    """Runs a Worker inline, so its signals arrive before start() returns."""

    def __init__(self):
        self.started = []

    def start(self, worker):
        self.started.append(worker)
        worker.run()


class _HeldPool:
    """Never runs the Worker: lets a test look at the in-flight state."""

    def __init__(self):
        self.started = []

    def start(self, worker):
        self.started.append(worker)


def _entry(tmp_path, profile, sub, label, size, locked=False):
    path = tmp_path / profile / sub
    path.mkdir(parents=True, exist_ok=True)
    return bs.CacheEntry(label=label, path=path, size_bytes=size, exists=True, locked=locked)


def _results(tmp_path, running=False):
    chrome_prof = bs.BrowserProfile(name="Default", path=tmp_path / "chrome" / "Default")
    chrome_prof.caches = [
        _entry(tmp_path, "chrome/Default", "Cache", "HTTP Cache", 300_000),
        _entry(tmp_path, "chrome/Default", "Code Cache", "Compiled JS/WASM", 100_000),
        _entry(tmp_path, "chrome/Default", "Unknown Thing", "Mystery Cache", 50_000),
        _entry(tmp_path, "chrome/Default", "GPUCache", "GPU Shader Cache", 0, locked=True),
        bs.CacheEntry(label="Media Cache", path=tmp_path / "chrome/Default/Media Cache"),
    ]
    chrome = bs.BrowserScanResult(name="Chrome", engine="chromium", is_running=running,
                                  profiles=[chrome_prof])
    ff_prof = bs.BrowserProfile(name="abc.default", path=tmp_path / "ff" / "abc.default")
    ff_prof.caches = [
        _entry(tmp_path, "ff/abc.default", "cache2", "Firefox HTTP Cache", 200_000),
        _entry(tmp_path, "ff/abc.default", "minidumps", "Minidumps", 80_000),
    ]
    firefox = bs.BrowserScanResult(name="Firefox", engine="firefox", profiles=[ff_prof])
    return [chrome, firefox]


@pytest.fixture
def tab():
    widget = bt._BrowserCleanupTab()
    widget.resize(1200, 700)
    yield widget
    widget._cancel_all()
    widget.deleteLater()


def _cache_rows(tab):
    return {c.text(0): c for c in tab._cache_items()}


def test_kb_id_follows_the_folder_not_the_label(tmp_path):
    prof = tmp_path / "Default"
    assert bt.kb_id("chromium", prof, prof / "Code Cache") == "browser:Code Cache"
    assert bt.kb_id("chromium", prof, prof / "Crashpad" / "reports") == "browser:Crashpad\\reports"
    assert bt.kb_id("firefox", prof, prof / "cache2") == "browser:firefox:cache2"
    assert bt.kb_id("chromium", prof, tmp_path / "Elsewhere" / "Cache") == "browser:Cache"


def test_rows_carry_research_badge_verdict_and_tooltip(tab, tmp_path):
    tab.populate(_results(tmp_path))
    rows = _cache_rows(tab)
    http = rows["HTTP Cache"]
    facts = http.data(0, FACTS_ROLE)
    assert facts.catalog_id == "browser:Cache"
    assert http.text(bt._COL_SAFETY) == "✓ Safe"
    assert http.text(bt._COL_VERDICT) == "Rebuilt automatically"
    tip = http.toolTip(0)
    assert "What:" in tip and "Before cleaning:" in tip and "Close the browser first" in tip
    # every column of the row carries the same explanation
    assert all(http.toolTip(c) == tip for c in range(len(bt.HEADERS)))


def test_unresearched_cache_says_so_and_does_not_guess(tab, tmp_path):
    tab.populate(_results(tmp_path))
    mystery = _cache_rows(tab)["Mystery Cache"]
    assert mystery.text(bt._COL_VERDICT) == "Not yet researched"
    assert "Not yet researched" in mystery.toolTip(0)
    assert "Mystery Cache folder of Chrome" in mystery.toolTip(0)


def test_review_rows_start_unticked_and_safe_rows_ticked(tab, tmp_path):
    tab.populate(_results(tmp_path))
    rows = _cache_rows(tab)
    dumps = rows["Minidumps"]           # research: caution (diagnostic data)
    assert dumps.data(bt._COL_SAFETY, SAFETY_ROLE) == "caution"
    assert dumps.text(bt._COL_SAFETY) == "! Review"
    assert dumps.checkState(0) == Qt.CheckState.Unchecked
    assert "starts unticked" in dumps.data(0, FACTS_ROLE).unselected_reason
    assert rows["HTTP Cache"].checkState(0) == Qt.CheckState.Checked
    # the parent shows the worst safety below it, never a reassuring badge
    ff = [tab._tree.topLevelItem(i) for i in range(tab._tree.topLevelItemCount())
          if tab._tree.topLevelItem(i).text(0) == "Firefox"][0]
    assert ff.data(bt._COL_SAFETY, SAFETY_ROLE) == "caution"


def test_review_default_is_behind_a_module_constant(tab, tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "UNTICK_REVIEW_BY_DEFAULT", False)
    tab.populate(_results(tmp_path))
    assert _cache_rows(tab)["Minidumps"].checkState(0) == Qt.CheckState.Checked


def test_unreadable_cache_is_listed_never_offered(tab, tmp_path):
    tab.populate(_results(tmp_path))
    rows = _cache_rows(tab)
    gpu = rows["GPU Shader Cache"]
    assert "Could not be read" in gpu.text(bt._COL_NOTE)
    assert gpu.text(bt._COL_SIZE) == "—"          # not "0 B": it was not measured
    assert not gpu.flags() & Qt.ItemFlag.ItemIsUserCheckable
    gpu.setCheckState(0, Qt.CheckState.Checked)
    assert all(not e.locked for e in tab._collect_checked())
    assert "Media Cache" not in rows                  # absent folder: not a row
    assert "could not be read" in tab._status.text()


def test_running_browser_gets_a_chip_and_the_banner(tab, tmp_path):
    tab.populate(_results(tmp_path, running=True))
    chrome = tab._tree.topLevelItem(0)
    assert chrome.text(0) == "Chrome"
    assert "Running" in chrome.text(bt._COL_NOTE) and "close first" in chrome.text(bt._COL_NOTE)
    assert "Running" in chrome.toolTip(0)
    assert "Chrome running: close first" in _cache_rows(tab)["HTTP Cache"].text(bt._COL_NOTE)
    assert not tab._warn.isHidden() and "Chrome" in tab._warn.text()


def test_exclusions_hide_rows_and_are_counted(tab, tmp_path):
    results = _results(tmp_path)
    excluded = [str(results[0].profiles[0].caches[0].path)]
    tab.populate(results, excluded)
    assert "HTTP Cache" not in [c.text(0) for c in tab._cache_items()
                                if c.parent().parent().text(0) == "Chrome"]
    assert "1 excluded" in tab._status.text()


def test_empty_loading_and_content_pages(tab, tmp_path):
    assert tab._stack.currentIndex() == bt.PAGE_EMPTY
    tab._thread_pool = _HeldPool()
    tab._do_scan()
    assert tab._stack.currentIndex() == bt.PAGE_LOADING
    assert tab._stop_btn.isEnabled() and not tab._scan_btn.isEnabled()
    tab._stop_scan()
    assert not tab._scanning and tab._scan_btn.isEnabled()
    assert tab._stack.currentIndex() == bt.PAGE_EMPTY
    tab.populate([])
    assert tab._stack.currentIndex() == bt.PAGE_EMPTY
    assert "No browser caches found" in tab._empty.text()
    tab.populate(_results(tmp_path))
    assert tab._stack.currentIndex() == bt.PAGE_CONTENT


def test_scan_result_arrives_through_the_worker(tab, tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "scan_browsers_robust", lambda: _results(tmp_path))
    tab._thread_pool = _SyncPool()
    tab._do_scan()
    assert not tab._scanning
    assert tab._stack.currentIndex() == bt.PAGE_CONTENT
    assert "HTTP Cache" in _cache_rows(tab)


def test_scan_error_shows_the_banner_and_recovers(tab, monkeypatch):
    def boom():
        raise OSError("denied")
    monkeypatch.setattr(bs, "scan_browsers_robust", boom)
    tab._thread_pool = _SyncPool()
    tab._do_scan()
    assert not tab._scanning and tab._scan_btn.isEnabled()
    assert not tab._error.isHidden()


def test_cancel_all_resets_a_stuck_scan(tab):
    tab._thread_pool = _HeldPool()
    tab._do_scan()
    tab._cancel_all()
    assert not tab._scanning and tab._scan_btn.isEnabled() and not tab._stop_btn.isEnabled()
    assert tab._workers == []


def test_a_stale_result_after_stop_is_ignored(tab, tmp_path, monkeypatch):
    pool = _HeldPool()
    tab._thread_pool = pool
    monkeypatch.setattr(bs, "scan_browsers_robust", lambda: _results(tmp_path))
    tab._do_scan()
    tab._stop_scan()
    pool.started[0]._cancelled = False      # simulate a late answer from the old worker
    pool.started[0].run()
    assert tab._tree.topLevelItemCount() == 0


def test_watchdog_resets_a_lost_completion(tab):
    tab._thread_pool = _HeldPool()
    tab._do_scan()
    tab._on_watchdog()
    assert not tab._scanning and "did not finish" in tab._status.text()


def test_clean_confirms_and_sends_only_ticked_readable_rows(tab, tmp_path, monkeypatch):
    tab.populate(_results(tmp_path))
    asked = []
    monkeypatch.setattr(bt, "_confirm_clean", lambda *a: asked.append(a) or True)
    sent = []
    monkeypatch.setattr(bs, "delete_cache_entries",
                        lambda entries, progress_cb=None: sent.append(list(entries)) or (123, 1))
    monkeypatch.setattr(bs, "scan_browsers_robust", lambda: [])
    freed = []
    tab.freed_bytes.connect(freed.append)
    tab._thread_pool = _SyncPool()
    assert tab._clean_btn.text() == "Clean selected…"
    tab._do_clean()
    assert asked and asked[0][1] == 4                 # Chrome HTTP, Code, Mystery + Firefox HTTP
    labels = sorted(e.label for e in sent[0])
    assert "Minidumps" not in labels                  # Review row was unticked
    assert "GPU Shader Cache" not in labels           # unreadable row never offered
    assert freed == [123]                             # the MEASURED bytes, not the scan size
    assert "Freed 123 B (measured)" in tab._status.text()


def test_declined_confirmation_deletes_nothing(tab, tmp_path, monkeypatch):
    tab.populate(_results(tmp_path))
    monkeypatch.setattr(bt, "_confirm_clean", lambda *a: False)
    monkeypatch.setattr(bs, "delete_cache_entries",
                        lambda *a, **k: pytest.fail("deleted without consent"))
    tab._do_clean()
    assert not tab._cleaning


def test_select_all_safe_never_ticks_review_rows(tab, tmp_path):
    tab.populate(_results(tmp_path))
    tab._deselect_all()
    assert tab._collect_checked() == []
    assert not tab._clean_btn.isEnabled()
    tab._select_all()
    labels = {e.label for e in tab._collect_checked()}
    assert "Minidumps" not in labels and "HTTP Cache" in labels
    assert tab._clean_btn.isEnabled()


def test_shortcuts_route_to_the_same_slots(tab):
    keys = {sc.key().toString() for sc in tab._shortcuts}
    assert {"F5", "Del", "Ctrl+A", "Ctrl+Shift+A", "F1"} <= keys
    for sc in tab._shortcuts:
        assert sc.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut


def test_every_control_has_a_tooltip(tab):
    for w in (tab._scan_btn, tab._stop_btn, tab._clean_btn, tab._more_btn, tab._warn):
        assert w.toolTip(), w
    for col in range(len(bt.HEADERS)):
        assert tab._tree.headerItem().toolTip(col)


def test_detail_pane_follows_the_current_row(tab, tmp_path):
    tab.populate(_results(tmp_path))
    tab._tree.setCurrentItem(_cache_rows(tab)["HTTP Cache"])
    html = tab._detail.browser.toHtml()
    assert "HTTP" in html and "What this tool will do" in html
    assert tab._detail.path_buttons            # Open / Copy / Exclude for its path
    tab._tree.setCurrentItem(tab._tree.topLevelItem(0))
    assert "Select a cache type" in tab._detail.browser.toPlainText()


def test_size_sorts_on_the_number(tab, tmp_path):
    tab.populate(_results(tmp_path))
    tab._tree.sortByColumn(bt._COL_SIZE, Qt.SortOrder.DescendingOrder)
    prof = tab._tree.topLevelItem(0).child(0)
    sizes = [prof.child(i).data(bt._COL_SIZE, bt.SIZE_ROLE) for i in range(prof.childCount())]
    assert sizes == sorted(sizes, reverse=True)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_renders_in_both_themes(tab, tmp_path, theme):
    from core import semantic_colors
    previous = semantic_colors.current_theme()
    semantic_colors.set_theme(theme)
    try:
        tab.populate(_results(tmp_path, running=True))
        tab._tree.setCurrentItem(_cache_rows(tab)["HTTP Cache"])
        tab.show()
        image = tab.grab()
        assert not image.isNull() and image.width() > 0
    finally:
        semantic_colors.set_theme(previous)
        tab.hide()
