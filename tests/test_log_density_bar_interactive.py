"""Unit tests for interactive LogDensityBar visualizer and LogPane time scrubbing."""
from datetime import datetime, timedelta
from typing import List

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QHelpEvent, QMouseEvent, QPaintEvent, QRegion
from PyQt6.QtWidgets import QApplication

from core.types import LogEntry
from ui.log_density_bar import LogDensityBar, TimeBucket
from ui.log_pane import LogPane


def _make_entry(timestamp: datetime, level: str = "Information", source: str = "TestSrc", message: str = "Test msg") -> LogEntry:
    return LogEntry(
        timestamp=timestamp,
        level=level,
        source=source,
        message=message,
        raw={},
    )


def test_initial_state(qapp):
    bar = LogDensityBar()
    assert not bar.has_data()
    assert bar.selected_range is None
    assert bar.get_peak_burst_bucket() is None
    assert not bar.peak_burst_btn.isVisible()
    assert hasattr(bar, "time_range_selected")
    assert hasattr(bar, "bucket_clicked")
    assert hasattr(bar, "jump_requested")


def test_hover_tooltip_format(qapp):
    bar = LogDensityBar()
    base_t = datetime(2026, 11, 18, 14, 30, 0)
    bucket = TimeBucket(
        start=base_t,
        end=base_t + timedelta(minutes=15),
        total=12,
        critical=2,
        errors=3,
        warnings=4,
        info=3,
    )
    # Required format: YYYY-MM-DD HH:MM — Total: N, Errors: E, Warnings: W
    # Critical and errors combine to error density of 5
    tip = bar.format_tooltip(bucket)
    assert tip == "2026-11-18 14:30 — Total: 12, Errors: 5, Warnings: 4"


def test_hover_tooltip_mouse_move(qapp):
    bar = LogDensityBar()
    bar.resize(400, 34)
    t0 = datetime(2026, 11, 18, 10, 0, 0)
    entries = [
        _make_entry(t0, level="Error"),
        _make_entry(t0 + timedelta(hours=1), level="Warning"),
        _make_entry(t0 + timedelta(hours=2), level="Information"),
    ]
    bar.set_entries(entries)
    assert bar.has_data()

    # Move over the first bucket
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(10.0, 10.0),
        QPointF(10.0, 10.0),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bar.mouseMoveEvent(event)
    assert "Total:" in bar.toolTip()
    assert "Errors:" in bar.toolTip()
    assert "Warnings:" in bar.toolTip()


def test_interactive_click_and_drag_selection(qapp):
    bar = LogDensityBar()
    bar.resize(480, 34)
    t0 = datetime(2026, 11, 18, 8, 0, 0)
    # 5 entries over 4 hours
    entries = [
        _make_entry(t0, level="Information"),
        _make_entry(t0 + timedelta(hours=1), level="Warning"),
        _make_entry(t0 + timedelta(hours=2), level="Error"),
        _make_entry(t0 + timedelta(hours=3), level="Critical"),
        _make_entry(t0 + timedelta(hours=4), level="Information"),
    ]
    bar.set_entries(entries)

    received_ranges = []
    bar.time_range_selected.connect(lambda s, e: received_ranges.append((s, e)))

    # Press at x=100
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(100.0, 15.0),
        QPointF(100.0, 15.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bar.mousePressEvent(press_event)
    assert not bar._is_dragging

    # Drag to x=300
    drag_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(300.0, 15.0),
        QPointF(300.0, 15.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bar.mouseMoveEvent(drag_event)
    assert bar._is_dragging

    # Release at x=300
    release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(300.0, 15.0),
        QPointF(300.0, 15.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bar.mouseReleaseEvent(release_event)

    assert not bar._is_dragging
    assert len(received_ranges) == 1
    start_dt, end_dt = received_ranges[0]
    assert start_dt < end_dt
    assert bar.selected_range == (start_dt, end_dt)

    # Clear selection
    bar.clear_selection()
    assert bar.selected_range is None


def test_single_click_backwards_compatibility(qapp):
    bar = LogDensityBar()
    bar.resize(480, 34)
    t0 = datetime(2026, 11, 18, 12, 0, 0)
    entries = [
        _make_entry(t0, level="Information"),
        _make_entry(t0 + timedelta(hours=1), level="Error"),
    ]
    bar.set_entries(entries)

    clicked_buckets = []
    jump_targets = []
    drag_ranges = []
    bar.bucket_clicked.connect(lambda s, e: clicked_buckets.append((s, e)))
    bar.jump_requested.connect(lambda t: jump_targets.append(t))
    bar.time_range_selected.connect(lambda s, e: drag_ranges.append((s, e)))

    # Press and release at same x without dragging
    pos = QPointF(240.0, 15.0)
    p_ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    bar.mousePressEvent(p_ev)
    r_ev = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    bar.mouseReleaseEvent(r_ev)

    assert len(clicked_buckets) == 1
    assert len(jump_targets) == 1
    # Drag range should not fire for a simple click
    assert len(drag_ranges) == 0


def test_jump_to_peak_burst_calculation(qapp):
    bar = LogDensityBar()
    bar.resize(600, 34)
    t0 = datetime(2026, 11, 18, 10, 0, 0)

    # 3 buckets:
    # 10:00 - 10 info
    # 11:00 - 4 errors, 1 critical (Peak Burst)
    # 12:00 - 1 error, 2 warnings
    entries = []
    for _ in range(10):
        entries.append(_make_entry(t0, level="Information"))
    for _ in range(4):
        entries.append(_make_entry(t0 + timedelta(hours=1), level="Error"))
    entries.append(_make_entry(t0 + timedelta(hours=1), level="Critical"))
    entries.append(_make_entry(t0 + timedelta(hours=2), level="Error"))
    for _ in range(2):
        entries.append(_make_entry(t0 + timedelta(hours=2), level="Warning"))

    bar.set_entries(entries)

    peak = bar.get_peak_burst_bucket()
    assert peak is not None
    assert peak.critical == 1
    assert peak.errors == 4
    assert peak.error_density == 5
    assert not bar.peak_burst_btn.isHidden()
    assert bar.is_peak_burst_visible

    jump_emitted = []
    bar.jump_requested.connect(lambda t: jump_emitted.append(t))

    res = bar.jump_to_peak_burst()
    assert res is peak
    assert len(jump_emitted) == 1
    assert jump_emitted[0] == peak.start
    assert bar.selected_range == (peak.start, peak.end)


def test_log_pane_time_range_filtering_wired(qapp):
    pane = LogPane(loader=lambda _w: [])
    t0 = datetime(2026, 11, 18, 9, 0, 0)
    e1 = _make_entry(t0, message="Event at 09:00")
    e2 = _make_entry(t0 + timedelta(hours=1), message="Event at 10:00")
    e3 = _make_entry(t0 + timedelta(hours=2), message="Event at 11:00")
    e4 = _make_entry(t0 + timedelta(hours=3), message="Event at 12:00")
    pane.set_entries([e1, e2, e3, e4])

    assert pane.row_count() == 4

    # Trigger time_range_selected on density bar to select [09:30, 11:30]
    range_start = t0 + timedelta(minutes=30)
    range_end = t0 + timedelta(hours=2, minutes=30)
    pane._density_bar.time_range_selected.emit(range_start, range_end)

    # Visible entries must be filtered to that exact time range (e2 and e3)
    assert pane.row_count() == 2
    messages = [e.message for e in pane.entries()]
    assert "Event at 10:00" in messages
    assert "Event at 11:00" in messages
    assert "Event at 09:00" not in messages
    assert "Event at 12:00" not in messages


def test_log_pane_jump_to_peak_burst_button(qapp):
    pane = LogPane(loader=lambda _w: [])
    t0 = datetime(2026, 11, 18, 10, 0, 0)

    # 10:00 - Info
    e_info = _make_entry(t0, level="Information", message="Normal operation")
    # 11:00 - High Error Burst
    e_crit = _make_entry(t0 + timedelta(hours=1), level="Critical", message="Database power outage")
    e_err1 = _make_entry(t0 + timedelta(hours=1, minutes=1), level="Error", message="Disk I/O timeout")
    e_err2 = _make_entry(t0 + timedelta(hours=1, minutes=2), level="Error", message="Connection reset")
    # 12:00 - Warning
    e_warn = _make_entry(t0 + timedelta(hours=2), level="Warning", message="Disk space low")

    pane.set_entries([e_info, e_crit, e_err1, e_err2, e_warn])

    assert not pane._peak_burst_btn.isHidden()

    # Click peak burst button
    pane._peak_burst_btn.click()

    # Verify that the table selected the burst error
    selected = pane.selected_entry()
    assert selected is not None
    assert selected.level in ("Critical", "Error")
    assert selected.timestamp >= t0 + timedelta(hours=1)
    assert selected.timestamp < t0 + timedelta(hours=2)


def test_high_dpi_paint_event_no_crashes(qapp):
    bar = LogDensityBar()
    bar.resize(800, 34)
    t0 = datetime(2026, 11, 18, 10, 0, 0)
    entries = [
        _make_entry(t0 + timedelta(minutes=i * 5), level="Error" if i % 3 == 0 else "Information")
        for i in range(20)
    ]
    bar.set_entries(entries)

    # Programmatic selection
    bar.select_time_range(t0 + timedelta(minutes=10), t0 + timedelta(minutes=40))

    # Trigger repaint
    event = QPaintEvent(QRegion(QRect(0, 0, 800, 34)))
    bar.paintEvent(event)
    assert bar.selected_range is not None


def test_tooltip_help_event(qapp):
    bar = LogDensityBar()
    bar.resize(400, 34)
    t0 = datetime(2026, 11, 18, 10, 0, 0)
    entries = [
        _make_entry(t0, level="Error"),
        _make_entry(t0 + timedelta(hours=1), level="Warning"),
    ]
    bar.set_entries(entries)
    assert bar.has_data()

    # Qt sends QHelpEvent for QEvent.Type.ToolTip
    help_event = QHelpEvent(QEvent.Type.ToolTip, QPoint(10, 10), QPoint(100, 100))
    handled = bar.event(help_event)
    assert handled is True

