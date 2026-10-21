"""WP6: the Cleanup overview dashboard (Quick Cleanup tab).

Driven with synthetic results -- no scanner runs and nothing is deleted. The
dashboard's figures, charts and lists must all describe the SAME de-duplicated
set of locations, and safety shown must never be less cautious than the
catalog's.
"""
import re
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.components import category_group as cg
from modules.cleanup.components import kpi_tiles as kt

SRC = Path(__file__).resolve().parent.parent / "src" / "modules" / "cleanup" / "components"
GB = 1024 ** 3
MB = 1024 ** 2


def _settle(qapp, seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def _result(*rows):
    r = cs.ScanResult()
    for path, size, safety in rows:
        r.items.append(cs.ScanItem(path=path, size=int(size), is_dir=True, safety=safety))
        r.total_size += int(size)
    return r


ADV = [("adv_a", "Adv A", ""), ("adv_b", "Adv B", ""), ("adv_c", "Adv C", ""),
       ("adv_d", "Adv D", "")]


def _tab(qapp, data, advanced=ADV):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", ""), ("wu", "Windows Update", ""),
                          ("dev", "Dev Tools", "")],
              advanced_categories=advanced)
    results = {cid: cs.ScanResult() for cid in list(tab._scanner_map) + list(tab._adv_scanner_map)}
    results.update(data)
    tab._start_facts_job = lambda: None          # no disk or catalog census in tests
    tab._refresh_trend = lambda: None
    tab._scanning = True
    tab._scan_targets = list(results)
    tab._results = results
    tab._total_scanned = len(results)
    tab._on_all_scanned()
    return tab


DATA = {
    "temp": _result((r"C:\Users\u\AppData\Local\Temp", 12 * GB, "safe"),
                    (r"C:\Windows\Temp", 4 * GB, "safe")),
    "wu": _result((r"C:\Windows\SoftwareDistribution\Download", 3 * GB, "caution")),
    "dev": _result((r"E:\cache\npm", 2 * GB, "safe")),
    "adv_a": _result((r"C:\a1", 300 * MB, "safe")),
    "adv_c": _result((r"C:\c1", 900 * MB, "safe")),
    # adv_b and adv_d found nothing
}


# ── Figures ──────────────────────────────────────────────────────────────

def test_compute_figures_counts_each_path_once_and_splits_review():
    rows = [(r"C:\x", 10, "safe"), (r"C:\y", 5, "caution"), (r"E:\z", 3, "danger")]
    preset = [(r"C:\x", 10), (r"c:\X", 10), (r"E:\z", 3)]          # duplicate by case
    fig = kt.compute_figures(rows, preset, categories_with_data=2)
    assert (fig.total, fig.locations) == (18, 3)
    assert (fig.review_bytes, fig.review_count) == (8, 2)
    assert (fig.preset_bytes, fig.preset_count) == (13, 2)
    assert fig.preset_on_drive == {"C:": 10, "E:": 3}


def test_kpi_tiles_show_the_synthetic_totals(qapp):
    tab = _tab(qapp, dict(DATA))
    total = sum(r.total_size for r in DATA.values())
    assert tab._tiles["total"].value.text() == cs.format_size(total)
    assert "6 locations" in tab._tiles["total"].sub.text()
    # Windows Update is caution: always in "Needs review" -- plus whatever the
    # research rates more cautiously than the catalog (never less).
    review = sum(i.size for g in tab._group_by_cid.values() for i in g.items()
                 if g.item_safety(i) != "safe")
    assert review >= 3 * GB
    assert tab._tiles["review"].value.text() == cs.format_size(review)


def test_not_offered_skips_duplicates_and_absent_paths():
    class Spec:
        def __init__(self, id, reason, label=""):
            self.id, self.disabled_reason, self.label = id, reason, label or id
    specs = [Spec("a", "Disabled: whole app data folder", "App A"),
             Spec("b", "duplicate of chrome_cache"),
             Spec("c", "Same paths as 'x'"),
             Spec("d", "holds sign-ins"),
             Spec("e", "")]
    present = {"a", "b", "c"}
    info = kt.not_offered_on_this_pc(specs, exists=lambda p: p in present,
                                     targets=lambda s: [s.id])
    assert info.count == 1 and info.reasons == (("App A", "Disabled: whole app data folder"),)
    tip = kt.not_offered_tooltip(info)
    assert "App A" in tip and "<script" not in kt.not_offered_tooltip(
        kt.NotOffered(1, (("<script>", "x"),)))


# ── Charts ───────────────────────────────────────────────────────────────

def test_donut_slices_are_the_legend_and_include_advanced(qapp):
    tab = _tab(qapp, dict(DATA))
    slices = tab._donut.slices()
    labels = [s.label for s in slices]
    with_data = {g.name() for g in tab._group_by_cid.values() if g.total_size() > 0}
    assert set(labels) == with_data            # every slice is a named row below
    assert sum(s.bytes for s in slices) == sum(r.total_size for r in DATA.values())
    assert labels == sorted(labels, key=lambda lb: -next(s.bytes for s in slices if s.label == lb))


def test_donut_folds_beyond_eight_into_other(qapp):
    adv = [(f"x{i}", f"X{i}", "") for i in range(12)]
    data = {f"x{i}": _result((rf"C:\x{i}", (i + 1) * MB, "safe")) for i in range(12)}
    tab = _tab(qapp, data, advanced=adv)
    slices = tab._donut.slices()
    assert len(slices) == 9 and slices[-1].label == "Other (4)"
    named = {s.label for s in slices[:-1]} | set(slices[-1].members)
    assert named == {f"X{i}" for i in range(12)}


def test_top_bars_are_the_ten_largest_items(qapp):
    adv = [(f"x{i}", f"X{i}", "") for i in range(14)]
    data = {f"x{i}": _result((rf"C:\dir{i}\leaf", (i + 1) * MB, "safe")) for i in range(14)}
    tab = _tab(qapp, data, advanced=adv)
    bars = tab._bars.bars()
    assert [b.bytes for b in bars] == [(i + 1) * MB for i in range(13, 3, -1)]
    assert all(b.detail.endswith("leaf") and b.safety == "safe" for b in bars)


def test_bars_never_show_less_caution_than_the_catalog(qapp):
    tab = _tab(qapp, dict(DATA))
    wu = next(b for b in tab._bars.bars() if "SoftwareDistribution" in b.detail)
    assert wu.safety in ("caution", "danger")


# ── Locations list ───────────────────────────────────────────────────────

def test_advanced_list_is_sorted_with_no_gaps(qapp):
    tab = _tab(qapp, dict(DATA))
    order = [tab._adv_box.itemAt(i).widget() for i in range(tab._adv_box.count())]
    groups = [w for w in order if isinstance(w, cg.CategoryGroup)]
    shown = [g for g in groups if not g.isHidden()]
    assert [g.name() for g in shown] == ["Adv C", "Adv A"]
    assert all(g.isHidden() for g in groups if g.total_size() == 0)
    assert "More locations (2)" in tab._show_adv_btn.text()


def test_main_rows_are_largest_first(qapp):
    tab = _tab(qapp, dict(DATA))
    order = [tab._main_box.itemAt(i).widget().name() for i in range(len(tab._legend_cards))]
    assert order == ["Temp Files", "Windows Update", "Dev Tools"]


def test_share_of_total_adds_up(qapp):
    tab = _tab(qapp, dict(DATA))
    assert sum(g.share() for g in tab._group_by_cid.values()) == pytest.approx(1.0)


def test_caution_rows_start_unticked_and_root_never_ticks_them(qapp, tmp_path):
    safe = cs.ScanItem(path=str(tmp_path / "a"), size=5, is_dir=True, safety="safe")
    review = cs.ScanItem(path=str(tmp_path / "b"), size=7, is_dir=True, safety="caution")
    group = cg.CategoryGroup("Mixed", lambda: None)
    group.set_result(_result())
    group.set_result(cs.ScanResult(items=[safe, review], total_size=12))
    root = group._tree.topLevelItem(0)
    states = {root.child(i).data(0, Qt.ItemDataRole.UserRole).path: root.child(i).checkState(0)
              for i in range(root.childCount())}
    assert states[review.path] == Qt.CheckState.Unchecked
    assert states[safe.path] == Qt.CheckState.Checked
    root.setCheckState(0, Qt.CheckState.Unchecked)
    root.setCheckState(0, Qt.CheckState.Checked)
    assert [i.path for i in group._items_to_clean()] == [safe.path]
    assert group.safety() == "caution"          # the badge is the most cautious level


def test_category_clean_asks_first(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    group = cg.CategoryGroup("Temp", lambda: None)
    group.set_result(_result((r"C:\nothing-here", 5, "safe")))
    asked, ran = [], []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: asked.append(self.text())
                        or QMessageBox.StandardButton.No)
    group._do_clean_all = lambda: ran.append(True)
    group.clean_all()
    assert asked and not ran


def test_row_tooltip_comes_from_the_research_or_says_not_researched(qapp):
    temp = cg.CategoryGroup("Temp Files", cs.scan_temp_files)
    temp.set_result(_result((r"C:\t", 1, "safe")))
    assert "What:" in temp.row_tooltip()
    unknown = cg.CategoryGroup("Mystery", lambda: None)
    assert "Not yet researched" in unknown.row_tooltip()


def test_first_sentence_does_not_cut_inside_brackets_or_abbreviations():
    text = "Download caches of npm, pip, NuGet (incl. the global folder), Cargo. Second."
    assert cg.first_sentence(text) == "Download caches of npm, pip, NuGet (incl. the global folder), Cargo."


# ── Signals and states ───────────────────────────────────────────────────

def test_snapshot_ready_carries_the_deduplicated_scan(qapp):
    from modules.cleanup.cleanup_scanner.scan_snapshot import ScanSnapshot
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    got = []
    QuickCleanupTab.snapshot_ready  # noqa: B018 -- signal exists on the class
    tab = _tab(qapp, dict(DATA))
    assert isinstance(tab.snapshot(), ScanSnapshot)
    assert tab.snapshot().total_size == sum(r.total_size for r in DATA.values())
    tab.snapshot_ready.connect(got.append)
    tab._publish_snapshot()
    assert got and got[0].total_size == tab.snapshot().total_size


def test_recommendation_navigation_is_forwarded(qapp):
    tab = _tab(qapp, dict(DATA))
    got = []
    tab.navigate.connect(lambda *a: got.append(a))
    tab._on_reco_navigate("wu", ["wu_cache"], 0)
    assert got == [("wu", ["wu_cache"], 0)]


def test_stop_resets_a_running_scan(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    import threading
    gate = threading.Event()

    def blocking(min_age_days=0):
        gate.wait(5)
        return cs.ScanResult()
    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "")], advanced_categories=[])
    tab._scanner_map["temp"] = (blocking, "Temp Files", "")
    tab._do_scan_all()
    assert tab._stop_btn.isEnabled()
    tab._on_stop_clicked()
    gate.set()
    _settle(qapp)
    assert not tab._scanning and tab._scan_all_btn.isEnabled() and not tab._stop_btn.isEnabled()
    assert "stopped" in tab._status_lbl.text().lower()


def test_theme_switch_repaints_the_charts(qapp):
    from core import semantic_colors as sc
    tab = _tab(qapp, dict(DATA))
    tab._donut.resize(420, 260)
    before = tab._donut.grab().toImage()
    sc.set_theme("light")
    try:
        tab.set_theme("light")
        after = tab._donut.grab().toImage()
    finally:
        sc.set_theme("dark")
    assert before != after


def test_no_colour_literals_in_the_overview_files():
    pattern = re.compile(r"#[0-9a-fA-F]{3}\b|#[0-9a-fA-F]{6}\b")
    for name in ("quick_cleanup_tab.py", "category_group.py", "kpi_tiles.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert not pattern.findall(text), f"{name} has colour literals"


def test_item_rows_use_shared_facts_and_research_tooltips(qapp):
    from modules.cleanup.components.item_rows import FACTS_ROLE, SIZE_ROLE, RowFacts
    group = cg.CategoryGroup("Temp Files", cs.scan_temp_files)
    group.set_result(_result((r"C:\scratch", 123, "safe")))
    child = group._tree.topLevelItem(0).child(0)
    assert isinstance(child.data(0, FACTS_ROLE), RowFacts)
    # The compact path list has one line per file; the category header already
    # shows the research subtitle. Uniform row heights must not clip two lines.
    assert child.data(0, FACTS_ROLE).subtitle == ""
    assert child.data(cg.COL_SIZE, SIZE_ROLE) == 123
    for col in range(group._tree.columnCount()):
        assert "What:" in child.toolTip(col)


def test_many_small_files_fold_without_changing_selection(qapp):
    group = cg.CategoryGroup("Files", lambda: None)
    items = [cs.ScanItem(path=rf"C:\cache\f{i}", size=10, is_dir=False) for i in range(30)]
    group.set_result(cs.ScanResult(items=items, total_size=300))
    root = group._tree.topLevelItem(0)
    visible = [root.child(i) for i in range(root.childCount()) if not root.child(i).isHidden()]
    assert len(visible) == 2
    assert "30 files" in visible[0].text(0)
    assert len(group._items_to_clean()) == 30
    group._tree.itemActivated.emit(visible[-1], 0)
    assert sum(not root.child(i).isHidden() for i in range(root.childCount())) == 30


def test_category_rescan_updates_dashboard_figures(qapp):
    tab = _tab(qapp, dict(DATA))
    group = tab.group("temp")
    group.set_result(_result((r"C:\new-temp", GB, "safe")))
    group.scan_done.emit(1, GB)
    assert tab._display["temp"].total_size == GB
    assert tab._figures.total == sum(r.total_size for r in DATA.values()) - 15 * GB


def test_an_overview_with_no_scanners_finishes(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    tab = QuickCleanupTab()
    tab.build(categories=[("unknown", "Unknown", "")], advanced_categories=[])
    tab._start_facts_job = lambda: None
    tab._refresh_trend = lambda: None
    tab.scan()
    assert not tab._scanning and tab._scanned


def _held_scan(qapp, monkeypatch):
    from modules.cleanup.components import quick_cleanup_tab as qc
    queued = []
    class Pool:
        def start(self, worker):
            queued.append(worker)
    monkeypatch.setattr(qc.QThreadPool, "globalInstance", lambda: Pool())
    tab = qc.QuickCleanupTab()
    tab.build(categories=[("temp", "Temp", "")], advanced_categories=[])
    tab._start_facts_job = lambda: None
    tab._refresh_trend = lambda: None
    tab.scan()
    return tab, queued


def test_worker_cancellation_resets_overview_immediately(qapp, monkeypatch):
    tab, queued = _held_scan(qapp, monkeypatch)
    queued[0].signals.cancelled.emit()
    assert not tab._scanning and tab._scan_all_btn.isEnabled()


def test_completed_workers_are_released(qapp, monkeypatch):
    tab, queued = _held_scan(qapp, monkeypatch)
    queued[0].signals.result.emit(cs.ScanResult())
    queued[0].signals.finished.emit()
    assert not tab._workers


def test_scan_errors_are_visible_in_the_overview(qapp, monkeypatch):
    tab, queued = _held_scan(qapp, monkeypatch)
    queued[0].signals.error.emit("Access refused")
    assert "failed" in tab._status_lbl.text().lower()
    assert "Access refused" in tab.group("temp")._err_lbl.text()


def test_chart_tables_describe_the_same_results(qapp):
    tab = _tab(qapp, dict(DATA))
    for card, count in ((tab._donut_card, len(tab._donut.slices())),
                        (tab._bars_card, len(tab._bars.bars()))):
        assert card.table.rowCount() == count
        card.table_toggle.setChecked(True)
        assert card.chart.isHidden() and not card.table.isHidden()
        assert all(card.table.item(row, 0).toolTip() for row in range(count))
        card.table_toggle.setChecked(False)
        assert not card.chart.isHidden() and card.table.isHidden()


def test_detail_dialog_contains_long_paths(qapp):
    group = cg.CategoryGroup("Paths", lambda: None)
    group.set_result(_result(("C:\\" + "long-folder-name" * 40, 123, "safe")))
    group.show_details()
    qapp.processEvents()
    try:
        assert group._details_dialog.width() <= 800
        assert group._details_dialog.minimumSizeHint().width() <= 800
    finally:
        group._details_dialog.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [900, 1440])
def test_dashboard_render_has_no_card_overlap(qapp, tmp_path, theme, width):
    from PyQt6.QtCore import QPoint
    from core import semantic_colors as sc
    previous = qapp.styleSheet()
    sc.set_theme(theme)
    qapp.setStyleSheet((SRC.parents[2] / "ui" / "styles" / f"{theme}.qss").read_text(encoding="utf-8"))
    tab = _tab(qapp, dict(DATA))
    tab._reco_pending = False  # synthetic data only; do not start machine probes
    tab._toggle_advanced()
    tab.resize(width, 960)
    tab.show()
    try:
        _settle(qapp, 0.1)
        for parent, widgets in ((tab._kpi, list(tab._tiles.values())),
                                (tab._charts, tab._charts.cards)):
            rects = []
            for widget in widgets:
                rect = widget.geometry()
                assert rect.width() >= 150 and rect.height() >= 60
                assert parent.rect().contains(rect)
                assert all(not rect.intersects(other) for other in rects)
                rects.append(rect)
        page = tab._scroll.widget()
        assert page.width() <= tab._scroll.viewport().width()
        image_path = tmp_path / f"overview-{theme}-{width}.png"
        assert page.grab().save(str(image_path))
        from PyQt6.QtGui import QImage
        image = QImage(str(image_path))
        assert not image.isNull() and image.width() == page.width()
        assert image.height() >= 600
        for group in tab._all_groups():
            if group.isVisible():
                assert group._header.width() >= 500
                for button in (group._info_btn, group._more_btn):
                    origin = button.mapTo(group, QPoint(0, 0))
                    assert origin.x() + button.width() <= group.width()
    finally:
        tab.cancel()
        tab.close()
        qapp.setStyleSheet(previous)
        sc.set_theme("dark")
