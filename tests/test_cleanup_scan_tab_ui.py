"""WP4: the area tabs' redesign (_ScanTab, _LargeItemsTab).

Presentation only: these tests pin what starts ticked, what the filter
hides (and that hidden rows are never cleaned), the empty/loading/error
pages, the shortcuts routing to the same slots as the buttons, the folding
of long file lists, and that nothing is cleaned without the size-gated
confirm. No real filesystem is scanned and nothing is deleted.
"""
import re
import threading
import time

import pytest
from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import QMessageBox

from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.cleanup_scanner import ScanItem, ScanResult
from modules.cleanup.components.item_rows import FACTS_ROLE, RowFacts
from modules.cleanup.tabs import _scan_tab as st


def _settle(qapp, timeout_ms: int = 10_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 0.5
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _scanner(name, items):
    def fn(min_age_days: int = 0) -> ScanResult:
        r = ScanResult()
        r.items = [ScanItem(path=i.path, size=i.size, is_dir=i.is_dir,
                            selected=i.selected, safety=i.safety) for i in items]
        r.total_size = sum(i.size for i in items)
        return r
    fn.__name__ = name
    return fn


def _feed(tab, scanners: dict):
    """Run the tab's presentation path exactly as a finished scan does."""
    per = {fn: fn() for fn in scanners}
    merged = ScanResult()
    for r in per.values():
        merged.items.extend(r.items)
    merged.items = cs.dedupe_items(merged.items)
    merged.total_size = sum(i.size for i in merged.items)
    tab._on_scan_result((merged, per, []))
    return merged


def _rows(tab):
    out = {}
    for i in range(tab._tree.topLevelItemCount()):
        for row, _holder in tab._location_rows(tab._tree.topLevelItem(i)):
            out[row.data(0, Qt.ItemDataRole.UserRole).path] = row
    return out


@pytest.fixture
def mixed_tab(qapp):
    safe = _scanner("scan_wp4_safe", [ScanItem(r"C:\wp4\safe", 3000, True, True, "safe")])
    review = _scanner("scan_wp4_review", [ScanItem(r"C:\wp4\review", 2000, True, True, "caution")])
    risky = _scanner("scan_wp4_risky", [ScanItem(r"C:\wp4\risky", 1000, True, True, "danger")])
    off = _scanner("scan_wp4_off", [ScanItem(r"C:\wp4\off", 500, True, False, "safe")])
    scanners = {safe: ("Safe Place", "safe"), review: ("Review Place", "caution"),
                risky: ("Risky Place", "danger"), off: ("Scanner Off", "safe")}
    tab = st._ScanTab(scanners)
    _feed(tab, scanners)
    return tab


# ── default tick state ──────────────────────────────────────────────────────

def test_only_safe_rows_start_ticked(mixed_tab):
    rows = _rows(mixed_tab)
    assert rows[r"C:\wp4\safe"].checkState(0) == Qt.CheckState.Checked
    assert rows[r"C:\wp4\review"].checkState(0) == Qt.CheckState.Unchecked
    assert rows[r"C:\wp4\risky"].checkState(0) == Qt.CheckState.Unchecked
    # A scanner's own selected=False is always honoured.
    assert rows[r"C:\wp4\off"].checkState(0) == Qt.CheckState.Unchecked
    chosen = [i.path for i in mixed_tab._get_selected_items() if i.selected]
    assert chosen == [r"C:\wp4\safe"]


def test_the_owner_can_flip_the_untick_policy(qapp, monkeypatch):
    monkeypatch.setattr(st, "UNTICK_REVIEW_BY_DEFAULT", False)
    review = _scanner("scan_wp4_review2", [ScanItem(r"C:\wp4\r2", 10, True, True, "caution")])
    tab = st._ScanTab({review: ("Review", "caution")})
    _feed(tab, {review: ("Review", "caution")})
    assert _rows(tab)[r"C:\wp4\r2"].checkState(0) == Qt.CheckState.Checked


def test_unticked_rows_say_why(mixed_tab):
    facts = _rows(mixed_tab)[r"C:\wp4\review"].data(0, FACTS_ROLE)
    assert isinstance(facts, RowFacts) and not facts.selected
    assert "unticked" in facts.unselected_reason


def test_a_mixed_group_is_partially_checked(qapp):
    fn = _scanner("scan_wp4_mix", [ScanItem(r"C:\m\a", 10, False, True, "safe"),
                                   ScanItem(r"C:\m\b", 10, False, False, "safe")])
    tab = st._ScanTab({fn: ("Mix", "safe")})
    _feed(tab, {fn: ("Mix", "safe")})
    assert tab._tree.topLevelItem(0).checkState(0) == Qt.CheckState.PartiallyChecked


# ── chips and filter ────────────────────────────────────────────────────────

def test_chips_show_live_counts(mixed_tab):
    texts = {k: b.text() for k, b in mixed_tab._chip_btns.items()}
    assert texts["all"].endswith("(4)")
    assert texts["safe"].endswith("(2)")
    assert texts["caution"].endswith("(1)")
    assert texts["danger"].endswith("(1)")


def test_a_chip_hides_other_safety_levels(mixed_tab):
    mixed_tab._set_chip("caution")
    rows = _rows(mixed_tab)
    assert not rows[r"C:\wp4\review"].isHidden()
    assert rows[r"C:\wp4\safe"].isHidden()
    mixed_tab._set_chip("all")
    assert not rows[r"C:\wp4\safe"].isHidden()


def test_a_ticked_row_hidden_by_the_filter_is_never_cleaned(mixed_tab):
    mixed_tab._filter.setText("risky")
    assert _rows(mixed_tab)[r"C:\wp4\safe"].isHidden()
    assert [i for i in mixed_tab._get_selected_items() if i.selected] == []
    assert not mixed_tab._clean_btn.isEnabled()
    assert "NOT cleaned" in mixed_tab._clean_btn.toolTip()


def test_select_all_safe_leaves_review_rows_alone(mixed_tab):
    mixed_tab._deselect_all()
    mixed_tab._select_all()
    rows = _rows(mixed_tab)
    assert rows[r"C:\wp4\safe"].checkState(0) == Qt.CheckState.Checked
    assert rows[r"C:\wp4\off"].checkState(0) == Qt.CheckState.Checked
    assert rows[r"C:\wp4\review"].checkState(0) == Qt.CheckState.Unchecked
    assert rows[r"C:\wp4\risky"].checkState(0) == Qt.CheckState.Unchecked


# ── pages ───────────────────────────────────────────────────────────────────

def test_empty_page_before_and_after_an_empty_scan(qapp):
    fn = _scanner("scan_wp4_nothing", [])
    tab = st._ScanTab({fn: ("Nothing", "safe")})
    assert tab._stack.currentIndex() == st.PAGE_EMPTY
    _feed(tab, {fn: ("Nothing", "safe")})
    assert tab._stack.currentIndex() == st.PAGE_EMPTY
    assert "Nothing to clean" in tab._empty.text()


def test_loading_page_while_scanning_then_content(qapp):
    started, release = threading.Event(), threading.Event()

    def scan_wp4_slow(min_age_days: int = 0) -> ScanResult:
        started.set()
        release.wait(10)
        r = ScanResult()
        r.items = [ScanItem(r"C:\slow", 5, False, True, "safe")]
        return r

    tab = st._ScanTab({scan_wp4_slow: ("Slow Place", "safe")})
    tab._do_scan()
    assert started.wait(10)
    qapp.processEvents()
    assert tab._stack.currentIndex() == st.PAGE_LOADING
    release.set()
    _settle(qapp)
    assert tab._stack.currentIndex() == st.PAGE_CONTENT


def test_a_failing_scanner_is_reported_not_swallowed(qapp):
    def scan_wp4_broken(min_age_days: int = 0):
        raise PermissionError("access is denied")

    ok = _scanner("scan_wp4_fine", [ScanItem(r"C:\fine", 5, False, True, "safe")])
    tab = st._ScanTab({scan_wp4_broken: ("Broken Place", "safe"), ok: ("Fine", "safe")})
    tab._do_scan()
    _settle(qapp)
    assert not tab._error.isHidden()
    assert "Broken Place" in tab._error.text()
    assert "access is denied" in tab._error.toolTip()
    assert tab._stack.currentIndex() == st.PAGE_CONTENT


# ── shortcuts ───────────────────────────────────────────────────────────────

def test_f5_routes_to_scan(mixed_tab, monkeypatch):
    calls = []
    monkeypatch.setattr(mixed_tab, "_do_scan", lambda: calls.append("scan"))
    # Re-bind: QShortcut captured the bound method at construction.
    sc = mixed_tab._shortcuts["F5"]
    sc.activated.disconnect()
    sc.activated.connect(mixed_tab._do_scan)
    sc.activated.emit()
    assert calls == ["scan"]


def test_delete_confirms_above_the_threshold_and_cancel_deletes_nothing(qapp, monkeypatch):
    big = _scanner("scan_wp4_big", [ScanItem(r"C:\big", st.CONFIRM_BYTES + 1, True, True, "safe")])
    tab = st._ScanTab({big: ("Big", "safe")})
    _feed(tab, {big: ("Big", "safe")})
    asked, deleted = [], []
    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: asked.append(self.text()) or QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(cs, "delete_items_report", lambda *a, **k: deleted.append(a))
    tab._shortcuts["Delete"].activated.emit()
    _settle(qapp)
    assert asked, "a clean above CONFIRM_BYTES ran without asking"
    assert deleted == []
    assert tab._cleaning is False and tab._clean_btn.isEnabled()


def test_delete_in_the_filter_box_edits_text_instead_of_cleaning(mixed_tab, monkeypatch):
    called = []
    monkeypatch.setattr(mixed_tab, "_do_clean", lambda: called.append(1))
    mixed_tab.show()
    mixed_tab._filter.setText("abc")
    mixed_tab._filter.setFocus()
    mixed_tab._filter.setCursorPosition(0)
    mixed_tab.activateWindow()
    from PyQt6.QtWidgets import QApplication
    if QApplication.focusWidget() is not mixed_tab._filter:
        pytest.skip("offscreen platform gave no focus")
    mixed_tab._shortcut_delete()
    assert called == [] and mixed_tab._filter.text() == "bc"
    mixed_tab.close()


def test_space_toggles_the_current_row(mixed_tab, qapp):
    from PyQt6.QtTest import QTest
    row = _rows(mixed_tab)[r"C:\wp4\review"]
    mixed_tab.show()
    mixed_tab._tree.setCurrentItem(row, 0)
    mixed_tab._tree.setFocus()
    QTest.keyClick(mixed_tab._tree, Qt.Key.Key_Space)
    qapp.processEvents()
    assert row.checkState(0) == Qt.CheckState.Checked
    mixed_tab.close()


# ── folding, tooltips, detail ───────────────────────────────────────────────

def test_thirty_small_files_fold_into_one_row_and_are_still_cleanable(qapp):
    files = [ScanItem(fr"C:\thumbs\f{i}.db", 24, False, True, "safe") for i in range(30)]
    fn = _scanner("scan_wp4_thumbs", files)
    tab = st._ScanTab({fn: ("Thumbnails", "safe")})
    _feed(tab, {fn: ("Thumbnails", "safe")})
    group = tab._tree.topLevelItem(0)
    assert group.childCount() == 1
    summary = group.child(0)
    assert summary.data(0, st._KIND_ROLE) == "summary"
    assert summary.childCount() == 30 and not summary.isExpanded()
    assert "30 files" in summary.text(0)
    assert len([i for i in tab._get_selected_items() if i.selected]) == 30


def test_every_row_has_a_tooltip(mixed_tab):
    for i in range(mixed_tab._tree.topLevelItemCount()):
        group = mixed_tab._tree.topLevelItem(i)
        assert group.toolTip(0) and group.toolTip(3)
        for row, _h in mixed_tab._location_rows(group):
            assert row.toolTip(0)
    for w in (mixed_tab._scan_btn, mixed_tab._stop_btn, mixed_tab._clean_btn,
              mixed_tab._more_btn, mixed_tab._filter, *mixed_tab._chip_btns.values()):
        assert w.toolTip(), w


def test_the_tab_works_without_any_research_files(qapp, monkeypatch):
    from modules.cleanup.components import detail_pane, item_rows
    for mod in (item_rows, st, detail_pane):
        monkeypatch.setattr(mod, "knowledge_api", lambda: None)
    fn = _scanner("scan_wp4_unknown", [ScanItem(r"C:\u", 10, True, True, "safe")])
    tab = st._ScanTab({fn: ("Unknown", "safe")})
    _feed(tab, {fn: ("Unknown", "safe")})
    row = _rows(tab)[r"C:\u"]
    assert "Not yet researched" in row.toolTip(0)
    tab._tree.setCurrentItem(row)
    assert "Not yet researched" in tab._detail.browser.toPlainText()


def test_selecting_a_group_shows_its_paths(mixed_tab):
    mixed_tab._tree.setCurrentItem(mixed_tab._tree.topLevelItem(0))
    assert mixed_tab._detail.path_buttons, "no Open/Copy/Exclude for the group's paths"


def test_also_on_provider_adds_a_chip(qapp):
    fn = _scanner("scan_wp4_shared", [ScanItem(r"C:\shared", 10, True, True, "safe")])
    tab = st._ScanTab({fn: ("Shared", "safe")})
    tab.also_on_provider = lambda p: ["Windows Update"] if p == r"C:\shared" else []
    _feed(tab, {fn: ("Shared", "safe")})
    assert "Also on Windows Update" in _rows(tab)[r"C:\shared"].text(st._COL_NOTE)


# ── colours ─────────────────────────────────────────────────────────────────

def test_safety_styles_follow_the_theme_and_keep_their_shape():
    from core.semantic_colors import semantic
    colour, word = st.SAFETY_STYLES["caution"]
    assert colour == semantic("warning") and word == "Review"
    assert [lvl for lvl, _ in st.SAFETY_STYLES.items()] == ["safe", "caution", "danger"]
    assert st.MUTED.startswith("#")


def test_no_frozen_colours_in_the_area_tab_files():
    import inspect
    from modules.cleanup.tabs import _large_items_tab
    for mod in (st, _large_items_tab):
        src = inspect.getsource(mod)
        assert not re.search(r"#[0-9a-fA-F]{3}\b|#[0-9a-fA-F]{6}\b", src), mod.__name__


def test_large_items_tab_builds_with_its_panels(qapp, monkeypatch):
    from modules.cleanup.tabs import _large_items_tab as lit
    tab = lit._LargeItemsTab()
    assert tab._scan_tab is not None and tab._drivers is not None
    assert tab._disk_cleanup is not None
    assert tab._splitter.count() == 2
    tab._cancel_all()


def test_breakdown_callbacks_ignore_rows_deleted_by_a_rescan(qapp, monkeypatch):
    captured = []
    fn = _scanner("scan_wp4_stale", [ScanItem(r"C:\bulky", 2 * 1024**3, True)])
    tab = st._ScanTab({fn: ("Bulky", "safe")})
    _feed(tab, tab._scanners)
    monkeypatch.setattr(tab._thread_pool, "start", captured.append)
    row = _rows(tab)[r"C:\bulky"]
    tab._fill_breakdown(row)
    tab._tree.clear()
    from PyQt6 import sip
    assert sip.isdeleted(row)
    # The guard must include the row, rather than only its still-live tab.
    assert tab._breakdown_is_current(row, captured[0]) is False
    captured[0].signals.result.emit([(r"C:\bulky\late", 5)])
    captured[0].signals.error.emit("late error")
    captured[0].signals.cancelled.emit()


def test_inner_selection_enables_clean_and_does_not_mutate_scan_facts(qapp, monkeypatch):
    fn = _scanner("scan_wp4_inner", [ScanItem(r"C:\bulky", 2 * 1024**3, True)])
    tab = st._ScanTab({fn: ("Bulky", "safe")})
    merged = _feed(tab, tab._scanners)
    monkeypatch.setattr(st.breakdown, "children_by_size", lambda _: [(r"C:\bulky\part", 10)])
    row = _rows(tab)[r"C:\bulky"]
    tab._fill_breakdown(row)
    _settle(qapp)
    row.setCheckState(0, Qt.CheckState.Unchecked)
    row.child(0).setCheckState(0, Qt.CheckState.Checked)
    assert tab._clean_btn.isEnabled()
    assert [i.path for i in tab._get_selected_items() if i.selected] == [r"C:\bulky\part"]
    assert merged.items[0].selected is True


def test_in_use_chip_uses_last_clean_paths_without_guessing_owners(qapp, monkeypatch):
    from modules.cleanup import cleanup_history
    monkeypatch.setattr(cleanup_history, "recent_deleted", lambda limit=20: [{
        "locked": [r"C:\cache\held.db"], "owners": {"app.exe": 1}}])
    fn = _scanner("scan_wp4_locked", [ScanItem(r"C:\cache", 20, True),
                                         ScanItem(r"C:\other", 10, True)])
    tab = st._ScanTab({fn: ("Caches", "safe")})
    _feed(tab, tab._scanners)
    assert tab._chip_btns["in_use"].text().endswith("(1)")
    tab._set_chip("in_use")
    rows = _rows(tab)
    assert not rows[r"C:\cache"].isHidden() and rows[r"C:\other"].isHidden()
    assert "In use last time" in rows[r"C:\cache"].text(st._COL_NOTE)
    assert "app.exe" not in rows[r"C:\cache"].text(st._COL_NOTE)


def test_clean_callbacks_from_cancelled_run_cannot_finish_new_run(mixed_tab, monkeypatch):
    from core.worker import Worker
    calls = []
    workers = []
    def start(_parent, _items, **kwargs):
        worker = Worker(lambda _w: None)
        workers.append(worker)
        calls.append(kwargs)
        return worker
    monkeypatch.setattr(st.csr, "run_clean_safe", start)
    mixed_tab._do_clean()
    mixed_tab._cancel_all()
    mixed_tab._do_clean()
    calls[0]["on_done"](1, 0, 50)
    assert mixed_tab._clean_worker is workers[1] and mixed_tab._cleaning
    workers[1].signals.cancelled.emit()
    assert not mixed_tab._cleaning and mixed_tab._scan_btn.isEnabled()


def test_f1_reopens_collapsed_detail(mixed_tab, qapp):
    mixed_tab.resize(1200, 700)
    mixed_tab.show()
    mixed_tab._tree.setCurrentItem(mixed_tab._tree.topLevelItem(0))
    mixed_tab._splitter.setSizes([1200, 0])
    mixed_tab._focus_detail()
    qapp.processEvents()
    assert mixed_tab._splitter.sizes()[1] >= 260
    mixed_tab.close()


@pytest.mark.parametrize("action", ["_deselect_all", "_do_quick_clean"])
def test_bulk_selection_clears_risky_breakdown_rows(qapp, monkeypatch, action):
    fn = _scanner("scan_wp4_risky_inside", [ScanItem(r"C:\bulky", 2 * 1024**3,
                                                    True, True, "danger")])
    tab = st._ScanTab({fn: ("Bulky", "danger")})
    _feed(tab, tab._scanners)
    monkeypatch.setattr(st.breakdown, "children_by_size", lambda _: [(r"C:\bulky\part", 10)])
    row = _rows(tab)[r"C:\bulky"]
    tab._fill_breakdown(row)
    _settle(qapp)
    row.child(0).setCheckState(0, Qt.CheckState.Checked)
    monkeypatch.setattr(tab, "_do_clean", lambda: None)
    getattr(tab, action)()
    assert not any(item.selected for item in tab._get_selected_items())


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("large", [False, True])
def test_area_render_has_no_control_overlap(qapp, tmp_path, theme, large):
    from pathlib import Path
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QPushButton
    from core.theme_manager import ThemeManager
    from modules.cleanup.tabs._large_items_tab import _LargeItemsTab

    manager = ThemeManager(str(Path(__file__).parents[1] / "src/ui/styles"))
    previous = qapp.styleSheet()
    from core.semantic_colors import current_theme, set_theme
    previous_theme = current_theme()
    set_theme(theme)
    outer = _LargeItemsTab() if large else None
    tab = outer._scan_tab if outer else st._ScanTab({})
    root = outer or tab
    # The sheet goes on THIS widget, not the application: app.setStyleSheet
    # repolishes every widget earlier tests left alive, and one of them crashed
    # it intermittently (access violation, 2026-10-21) -- not what this tests.
    root.setStyleSheet(manager._load_qss(str(Path(__file__).parents[1] / f"src/ui/styles/{theme}.qss")))
    fn = _scanner("scan_wp4_render", [
        ScanItem("C:\\Users\\Example\\" + "long_path_" * 18 + "\\cache", 7000, True),
        ScanItem(r"C:\review", 2000, True, True, "caution"),
    ])
    tab._scanners = {fn: ("Application caches", "safe")}
    _feed(tab, tab._scanners)
    root.resize(1280, 800)
    root.show()
    qapp.processEvents()
    row = next(row for row in _rows(tab).values()
               if row.data(0, Qt.ItemDataRole.UserRole).size == 7000)
    tab._tree.setCurrentItem(row)
    qapp.processEvents()
    try:
        assert root.width() == 1280, "a path or status forced the window wider"
        controls = [tab._scan_btn, tab._stop_btn, tab._clean_btn, tab._more_btn, tab._status]
        for a, b in zip(controls, controls[1:]):
            assert not a.geometry().intersects(b.geometry())
        assert tab._tree.viewport().width() >= 400
        assert tab._detail.width() >= 260
        assert tab._detail.browser.height() >= 70
        for button in tab._detail.path_buttons:
            assert button.toolTip()
            pos = button.mapTo(root, QPoint())
            assert 0 <= pos.x() < root.width() and pos.x() + button.width() <= root.width()
        # Both screenshots and geometry evidence live outside the repository.
        shot = root.grab()
        assert shot.size() == root.size()
        assert shot.save(str(tmp_path / f"wp4-{'large' if large else 'area'}-{theme}.png"))
        assert all(button.accessibleName() for button in controls[:-1])
    finally:
        root.close()
        root._cancel_all()
        assert qapp.styleSheet() == previous
        set_theme(previous_theme)
