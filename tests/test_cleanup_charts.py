"""Cleanup's QPainter charts: folding, hit-testing, painting, accessibility.

The charts are pure widgets; nothing here scans or deletes anything.
"""
import pathlib
import re
import time

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from core.semantic_colors import set_theme
from modules.cleanup.components import charts
from modules.cleanup.components.charts import (
    OTHER_KEY, BarChart, ChartCard, DonutChart, Slice, TrendChart, TrendPoint,
    fold_slices, nice_byte_ticks, percent_text, safety_mix_text,
)

GB = 1024 ** 3
MB = 1024 ** 2


@pytest.fixture(autouse=True)
def _restore_theme():
    yield
    set_theme("dark")


def _shown(widget, w=420, h=240):
    widget.resize(w, h)
    widget.show()
    QApplication.processEvents()
    return widget


def _slices(n, step=MB):
    return [Slice(f"Item {i}", (n - i) * step, key=f"k{i}") for i in range(n)]


# -- folding ------------------------------------------------------------------

def test_fold_keeps_the_largest_and_folds_the_rest_into_other():
    data = _slices(12)
    folded = fold_slices(list(reversed(data)), top_n=8)
    assert len(folded) == 9
    assert [s.key for s in folded[:8]] == [f"k{i}" for i in range(8)]   # largest first
    other = folded[-1]
    assert other.key == OTHER_KEY
    assert other.label == "Other (4)"
    assert other.bytes == sum(s.bytes for s in data[8:])
    assert other.members == tuple(s.label for s in data[8:])
    assert sum(s.bytes for s in folded) == sum(s.bytes for s in data)


def test_fold_leaves_a_short_list_alone_and_drops_empty_slices():
    data = [Slice("a", 5), Slice("zero", 0), Slice("b", 9), Slice("neg", -3)]
    folded = fold_slices(data, top_n=8)
    assert [s.label for s in folded] == ["b", "a"]


def test_fold_merges_the_safety_mix_of_what_it_folds():
    data = [Slice("a", 100), Slice("b", 10, safety_mix={"safe": 6, "caution": 4}),
            Slice("c", 5, safety="danger")]
    other = fold_slices(data, top_n=1)[-1]
    assert dict(other.safety_mix) == {"safe": 6, "caution": 4, "danger": 5}


def test_fold_respects_a_configurable_top_n():
    assert len(fold_slices(_slices(10), top_n=3)) == 4
    assert len(fold_slices(_slices(10), top_n=0)) == 2      # never fewer than one named


def test_percent_and_mix_text():
    assert percent_text(68, 100) == "68%"
    assert percent_text(1, 1000) == "<1%"
    assert percent_text(0, 100) == "0%"
    assert percent_text(5, 0) == "0%"
    assert safety_mix_text({"caution": GB, "safe": 2 * GB}) == "Safe 2.0 GB \u00b7 Review 1.0 GB"


def test_byte_ticks_are_round_numbers_covering_the_maximum():
    ticks = nice_byte_ticks(int(14.3 * GB))
    assert ticks[0] == 0 and ticks[-1] >= int(14.3 * GB)
    assert [charts.tick_label(t) for t in ticks] == ["0 B", "5 GB", "10 GB", "15 GB"]
    assert nice_byte_ticks(0) == [0]


# -- donut ----------------------------------------------------------------------

def test_donut_slices_are_the_legend_set_after_folding():
    d = _shown(DonutChart())
    d.set_data(_slices(20))
    assert len(d.slices()) == 9
    assert d.slices()[-1].key == OTHER_KEY
    assert d.total() == sum(s.bytes for s in _slices(20))


def test_donut_hit_test_by_angle():
    """Two equal slices: the first runs clockwise from 12 to 6 o'clock (the
    right half), the second is the left half; the hole is no slice."""
    d = _shown(DonutChart(), 440, 240)
    d.set_data([Slice("A", 50, key="a"), Slice("B", 50, key="b")])
    donut, _ = d._layout()
    c = donut.center()
    r = donut.width() / 2 * (1 - d.RING)       # middle of the ring
    assert d.slice_at(QPoint(int(c.x() + r), int(c.y()))) == 0
    assert d.slice_at(QPoint(int(c.x() - r), int(c.y()))) == 1
    assert d.slice_at(QPoint(int(c.x()), int(c.y()))) == -1
    assert d.slice_at(QPoint(1, 1)) == -1


def test_donut_legend_rows_are_hit_targets_too():
    d = _shown(DonutChart(), 440, 240)
    d.set_data([Slice("A", 70, key="a"), Slice("B", 30, key="b")])
    _, legend = d._layout()
    rows = d._legend_rows(legend)
    assert d.slice_at(rows[1].center()) == 1


def test_donut_click_emits_the_slice_key():
    d = _shown(DonutChart(), 440, 240)
    d.set_data([Slice("A", 50, key="a"), Slice("B", 50, key="b")])
    got = []
    d.slice_clicked.connect(got.append)
    donut, _ = d._layout()
    c = donut.center()
    r = donut.width() / 2 * (1 - d.RING)
    QTest.mouseClick(d, Qt.MouseButton.LeftButton, pos=QPoint(int(c.x() - r), int(c.y())))
    assert got == ["b"]


def test_donut_keyboard_moves_and_activates():
    d = _shown(DonutChart())
    d.set_data([Slice("A", 50, key="a"), Slice("B", 30, key="b")])
    hovered, clicked = [], []
    d.hovered.connect(hovered.append)
    d.slice_clicked.connect(clicked.append)
    d.setFocus()
    QTest.keyClick(d, Qt.Key.Key_Right)
    QTest.keyClick(d, Qt.Key.Key_Right)
    QTest.keyClick(d, Qt.Key.Key_Return)
    QTest.keyClick(d, Qt.Key.Key_Escape)
    assert hovered == ["a", "b", ""]
    assert clicked == ["b"]


@pytest.mark.parametrize("n", [0, 1, 50])
def test_donut_grab_does_not_raise(n):
    d = _shown(DonutChart())
    d.set_data(_slices(n))
    for w, h in ((420, 240), (260, 380), (120, 90)):
        d.resize(w, h)
        assert not d.grab().isNull()
    d._set_hover(0 if n else -1)
    assert not d.grab().isNull()


def test_donut_accessible_summary_lists_every_slice():
    d = _shown(DonutChart(title="Space by category"))
    data = _slices(11)
    d.set_data(data)
    summary = d.accessible_summary()
    for s in d.slices():
        assert s.label in summary
    for folded in data[8:]:               # the members of Other are named too
        assert folded.label in summary
    assert d.accessibleDescription() == summary
    d.set_data([])
    assert "Nothing to show yet" in d.accessible_summary()


def test_donut_tooltip_escapes_and_the_provider_wins():
    d = _shown(DonutChart())
    d.set_data([Slice("<script>x</script>", 10, key="x")])
    assert "<script>" not in d.hover_text(0)
    assert "&lt;script&gt;" in d.hover_text(0)
    d.set_tooltip_provider(lambda s: f"KB for {s.key}")
    assert d.hover_text(0) == "KB for x"

    def broken(_s):
        raise RuntimeError("boom")
    d.set_tooltip_provider(broken)
    assert "&lt;script&gt;" in d.hover_text(0)     # falls back, does not raise


def test_donut_repaints_in_the_new_theme():
    d = _shown(DonutChart())
    d.set_data(_slices(5))
    set_theme("dark")
    d.set_theme("dark")
    dark = d.grab().toImage()
    set_theme("light")
    d.set_theme("light")
    light = d.grab().toImage()
    assert dark != light
    assert d.colour_for(0) == charts.chart(0)


# -- bars -------------------------------------------------------------------------

def test_bars_are_the_largest_items_largest_first():
    b = _shown(BarChart(), 460, 300)
    b.set_data(_slices(25))
    assert [s.key for s in b.bars()] == [f"k{i}" for i in range(10)]


def test_bar_hit_test_by_row():
    b = _shown(BarChart(), 460, 300)
    b.set_data([Slice("A", 30, key="a"), Slice("B", 20, key="b"), Slice("C", 10, key="c")])
    rows, _, bar_left, _ = b._geometry()
    assert b.bar_at(QPoint(bar_left + 2, rows[1].center().y())) == 1
    assert b.bar_at(QPoint(bar_left + 2, rows[-1].bottom() + 30)) == -1
    got = []
    b.bar_clicked.connect(got.append)
    QTest.mouseClick(b, Qt.MouseButton.LeftButton, pos=QPoint(bar_left + 2, rows[2].center().y()))
    assert got == ["c"]


def test_bar_colour_follows_safety_and_the_summary_says_it():
    b = _shown(BarChart(), 460, 300)
    b.set_data([Slice("T", 30, key="t", safety="safe", detail=r"C:\Temp"),
                Slice("W", 20, key="w", safety="caution"),
                Slice("R", 10, key="r", safety="danger"),
                Slice("N", 5, key="n")])
    from core.semantic_colors import semantic
    assert b.colour_for(0) == semantic("success")
    assert b.colour_for(1) == semantic("warning")
    assert b.colour_for(2) == semantic("error")
    assert b.colour_for(3) == charts.chart(3)
    summary = b.accessible_summary()
    assert r"C:\Temp" in summary and "Safe" in summary and "Review" in summary
    assert "Risky" in summary


@pytest.mark.parametrize("n", [0, 1, 50])
def test_bar_grab_does_not_raise(n):
    b = _shown(BarChart(), 460, 300)
    b.set_data(_slices(n))
    for w, h in ((460, 300), (200, 60)):
        b.resize(w, h)
        assert not b.grab().isNull()


# -- trend ------------------------------------------------------------------------

def _points(n, start_days=40):
    now = time.time()
    return [TrendPoint(now - (start_days - i * 5) * 86400, (i + 1) * GB) for i in range(n)]


def test_trend_needs_two_points_and_says_so():
    t = _shown(TrendChart())
    t.set_data(_points(1), [])
    assert not t.has_enough_data()
    assert "Not enough history yet" in t.accessible_summary()
    assert t._index_at(QPoint(200, 120)) == -1
    t.set_data(_points(1), _points(2))
    assert t.has_enough_data()


def test_trend_cumulative_and_summary():
    t = _shown(TrendChart(), 460, 240)
    cleans = _points(3)
    t.set_data(cleans, _points(4))
    assert [c.value for c in t._cumulative] == [GB, 3 * GB, 6 * GB]
    summary = t.accessible_summary()
    assert "3 cleans" in summary and "6.0 GB" in summary
    assert "4 scans" in summary


def test_trend_hover_finds_the_nearest_mark():
    t = _shown(TrendChart(), 460, 240)
    t.set_data(_points(3), [])
    pt = t._mark_point(1)
    assert t._index_at(pt) == 1
    assert "Freed 2.0 GB" in t.hover_text(1)


@pytest.mark.parametrize("n", [0, 1, 2, 30])
def test_trend_grab_does_not_raise(n):
    t = _shown(TrendChart(), 460, 240)
    t.set_data(_points(n, start_days=200), _points(max(0, n - 1), start_days=200))
    for w, h in ((460, 240), (200, 100)):
        t.resize(w, h)
        assert not t.grab().isNull()


def test_trend_same_moment_points_do_not_divide_by_zero():
    now = time.time()
    t = _shown(TrendChart())
    t.set_data([TrendPoint(now, GB), TrendPoint(now, 2 * GB)], [])
    assert not t.grab().isNull()


# -- card and source rules ---------------------------------------------------------

def test_chart_card_carries_the_qss_role_and_title():
    card = ChartCard("Space by category", DonutChart(), "caption")
    assert card.objectName() == "chartCard"
    assert card.title_label.text() == "Space by category"
    assert not card.caption_label.isHidden()
    card.set_caption("")
    assert card.caption_label.isHidden()


def test_the_charts_module_has_no_colour_literal():
    src = pathlib.Path(charts.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b", src)


def test_empty_charts_paint_without_data():
    for widget in (DonutChart(), BarChart(), TrendChart()):
        _shown(widget)
        widget.set_empty_text("Nothing here", "Scan first.")
        assert not widget.grab().isNull()
        assert widget.accessible_summary().endswith("Nothing here.") or \
            "Nothing here" in widget.accessible_summary()
