from datetime import datetime, timedelta

from PyQt6.QtGui import QImage, QPainter

from modules.reliability.reliability_analysis import Metric
from modules.reliability.stability_chart import StabilityChart


def _paint(widget, width=220, height=26):
    """Render the widget for real. This is the test: a float coordinate
    would take the process down here rather than in front of the user
    (`ui/perf_graph.py`'s own test file uses the same pattern)."""
    widget.resize(width, height)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    widget.render(painter)
    painter.end()
    return image


def test_an_empty_chart_paints_and_says_so(qapp):
    chart = StabilityChart()
    _paint(chart)
    assert chart.toolTip() == "No stability index history yet."
    assert chart.latest() is None


def test_a_single_metric_paints(qapp):
    chart = StabilityChart()
    chart.set_metrics([Metric(datetime(2026, 9, 29, 10, 0), 4.4)])
    _paint(chart)
    assert chart.latest() == 4.4


def test_a_declining_history_paints_and_sorts_out_of_order_input(qapp):
    t0 = datetime(2026, 9, 4, 11, 0)
    # Deliberately out of order: set_metrics must not trust the caller's order.
    metrics = [Metric(t0 + timedelta(days=25), 4.4), Metric(t0, 10.0),
               Metric(t0 + timedelta(days=13), 1.4)]
    chart = StabilityChart()
    chart.set_metrics(metrics)
    _paint(chart)
    assert chart.latest() == 4.4
    assert "Lowest: 1.4 / 10" in chart.toolTip()


def test_a_wide_history_paints_at_a_narrow_width(qapp):
    """Several hundred hourly points (this real machine has ~600) squeezed
    into a toolbar-sized widget -- the trap `_points`' step math exists for."""
    t0 = datetime(2026, 9, 4, 0, 0)
    metrics = [Metric(t0 + timedelta(hours=i), 10.0 - (i % 10) * 0.5) for i in range(600)]
    chart = StabilityChart()
    chart.set_metrics(metrics)
    _paint(chart, width=190, height=26)
