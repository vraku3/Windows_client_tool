"""Two real gaps found reading src/modules/perfmon/ in full:

1. `PerfMonStore.query()` had no caller anywhere in the app -- every sample
   it ever wrote to perfmon.db was write-only. `HistoryDashboard` is the
   read side, wired to a new "History" tab.
2. `AlertRule`s fire into the log, but the chart the alert is about never
   showed the threshold that would trigger it. `_QtLineChart.set_threshold`
   draws the exact number from the same `AlertRule`, never a re-guessed one.
"""
import sys

from PyQt6.QtWidgets import QApplication

QApplication.instance() or QApplication(sys.argv)

from modules.perfmon.perfmon_alerts import AlertRule  # noqa: E402
from modules.perfmon.perfmon_charts import HistoryDashboard, PerfMonDashboard, _QtLineChart  # noqa: E402


def test_chart_has_no_threshold_line_by_default():
    chart = _QtLineChart("CPU", "%")
    assert chart._threshold is None


def test_set_threshold_stores_the_exact_value_and_label():
    chart = _QtLineChart("CPU", "%")
    chart.set_threshold(90, "alert > 90")
    assert chart._threshold == 90
    assert chart._threshold_label == "alert > 90"


def test_set_threshold_none_clears_it():
    chart = _QtLineChart("CPU", "%")
    chart.set_threshold(90, "alert > 90")
    chart.set_threshold(None)
    assert chart._threshold is None


def test_chart_with_a_threshold_paints_without_raising():
    """The real regression risk here: drawing a line that isn't the data
    line at all crashes on a bad Qt call, not on a wrong number."""
    chart = _QtLineChart("CPU", "%", y_range=(0, 100))
    chart.set_threshold(90, "alert > 90")
    for v in (10, 50, 95, 88):
        chart.add_point(v)
    chart.resize(300, 160)
    pix = chart.grab()
    assert pix.width() == 300 and pix.height() == 160


def test_dashboard_wires_thresholds_from_matching_alert_rules():
    dash = PerfMonDashboard()
    alerts = [
        AlertRule(counter="cpu_total", operator=">", threshold=90, duration_sec=300),
        AlertRule(counter="memory_percent", operator=">", threshold=85, duration_sec=60),
    ]
    dash.set_alert_thresholds(alerts)
    assert dash.cpu_chart._plot_widget._threshold == 90
    assert dash.memory_chart._plot_widget._threshold == 85
    # No rule targets disk_percent -- it must stay unset, not inherit one.
    assert dash.disk_chart._plot_widget._threshold is None


def test_dashboard_clears_a_threshold_when_its_rule_is_disabled():
    dash = PerfMonDashboard()
    dash.set_alert_thresholds([
        AlertRule(counter="cpu_total", operator=">", threshold=90, duration_sec=300),
    ])
    assert dash.cpu_chart._plot_widget._threshold == 90
    dash.set_alert_thresholds([
        AlertRule(counter="cpu_total", operator=">", threshold=90, duration_sec=300, enabled=False),
    ])
    assert dash.cpu_chart._plot_widget._threshold is None


def test_history_dashboard_load_replays_a_series_per_counter():
    dash = HistoryDashboard()
    has_data = dash.load({
        "cpu_total": [("t1", 10.0), ("t2", 20.0), ("t3", 30.0)],
        "memory_percent": [],
        "disk_percent": [],
    })
    assert has_data is True
    assert list(dash.charts["cpu_total"]._data) == [10.0, 20.0, 30.0]
    assert list(dash.charts["memory_percent"]._data) == []


def test_history_dashboard_load_with_no_rows_anywhere_reports_no_data():
    dash = HistoryDashboard()
    has_data = dash.load({"cpu_total": [], "memory_percent": [], "disk_percent": []})
    assert has_data is False


def test_history_dashboard_paints_a_replayed_series_without_raising():
    dash = HistoryDashboard()
    dash.load({
        "cpu_total": [("t%d" % i, float(i % 100)) for i in range(50)],
        "memory_percent": [],
        "disk_percent": [],
    })
    dash.resize(400, 200)
    pix = dash.grab()
    assert pix.width() == 400
