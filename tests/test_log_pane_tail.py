"""Tests for Auto-Tail / Live Follow Mode and Scrolling Ergonomics in LogPane & LogTableWidget."""
from datetime import datetime
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from core.types import LogEntry
from ui.log_pane import LogPane
from ui.log_table_widget import LogTableWidget


def _make_entry(i: int = 0, level: str = "Information", message: str = "event"):
    return LogEntry(
        timestamp=datetime(2026, 11, 18, 12, 0, i % 60),
        source="TestSource",
        level=level,
        message=f"{message} {i}",
        raw={"event_id": 1000 + i},
    )


def test_log_pane_tail_button_init_and_toggle(qapp):
    pane = LogPane(loader=lambda worker: [])
    assert hasattr(pane, "_tail_btn")
    assert pane._tail_btn.isCheckable()
    assert "Follow" in pane._tail_btn.text()
    assert not pane.is_tail_mode()
    assert not pane._table.is_follow_mode()

    # Toggle via method
    pane.set_tail_mode(True)
    assert pane.is_tail_mode()
    assert pane._tail_btn.isChecked()
    assert pane._table.is_follow_mode()

    pane.set_tail_mode(False)
    assert not pane.is_tail_mode()
    assert not pane._tail_btn.isChecked()
    assert not pane._table.is_follow_mode()

    # Toggle via table signal back to pane
    pane._table.set_follow_mode(True)
    assert pane._tail_btn.isChecked()
    assert pane.is_tail_mode()


def test_table_initial_follow_state(qapp):
    table = LogTableWidget()
    assert not table.is_follow_mode()
    assert not table.is_follow_paused()
    assert table.follow_new_events_count() == 0
    assert hasattr(table, "_follow_pill")
    assert not table._follow_pill.isVisible()


def test_auto_scroll_to_bottom_when_follow_active(qapp):
    table = LogTableWidget()
    table.resize(600, 300)
    table.show()
    qapp.processEvents()

    entries = [_make_entry(i) for i in range(100)]
    table.set_entries(entries)
    qapp.processEvents()

    v_bar = table._table.verticalScrollBar()
    # By default (follow disabled), set_entries does not force bottom scroll
    assert v_bar.value() == 0

    # Enable follow mode -> should scroll to bottom immediately
    table.set_follow_mode(True)
    table.flush_auto_scroll()
    qapp.processEvents()
    assert table.is_at_bottom()
    assert v_bar.value() == v_bar.maximum()
    assert v_bar.value() > 0

    # Append new entries -> auto scrolls to new bottom
    more_entries = [_make_entry(i) for i in range(100, 120)]
    table.append_entries(more_entries)
    table.flush_auto_scroll()
    qapp.processEvents()
    assert table.is_at_bottom()
    assert v_bar.value() == v_bar.maximum()


def test_manual_scroll_up_pauses_follow_and_shows_pill(qapp):
    table = LogTableWidget()
    table.resize(600, 300)
    table.show()
    qapp.processEvents()

    entries = [_make_entry(i) for i in range(80)]
    table.set_entries(entries)
    table.set_follow_mode(True)
    table.flush_auto_scroll()
    qapp.processEvents()
    assert table.is_at_bottom()

    paused_signals = []
    table.follow_paused_changed.connect(paused_signals.append)

    # Manually scroll up
    v_bar = table._table.verticalScrollBar()
    v_bar.setValue(10)
    qapp.processEvents()

    assert not table.is_at_bottom()
    assert table.is_follow_paused()
    assert len(paused_signals) == 1 and paused_signals[0] is True
    assert table._follow_pill.isVisible()
    assert "⬇ New events (0) — Click to follow" in table._follow_pill.text()


def test_unread_events_count_increments_while_paused(qapp):
    table = LogTableWidget()
    table.resize(600, 300)
    table.show()
    qapp.processEvents()

    entries = [_make_entry(i) for i in range(80)]
    table.set_entries(entries)
    table.set_follow_mode(True)
    table.flush_auto_scroll()
    qapp.processEvents()

    # User scrolls up
    v_bar = table._table.verticalScrollBar()
    v_bar.setValue(10)
    qapp.processEvents()
    assert table.is_follow_paused()

    # Append 5 new entries
    batch1 = [_make_entry(i) for i in range(80, 85)]
    table.append_entries(batch1)
    qapp.processEvents()

    assert table.is_follow_paused()
    assert table.follow_new_events_count() == 5
    assert "⬇ New events (5) — Click to follow" in table._follow_pill.text()
    # Scroll position must NOT have jumped to bottom
    assert v_bar.value() == 10

    # Append 7 more entries
    batch2 = [_make_entry(i) for i in range(85, 92)]
    table.append_entries(batch2)
    qapp.processEvents()

    assert table.follow_new_events_count() == 12
    assert "⬇ New events (12) — Click to follow" in table._follow_pill.text()


def test_clicking_pill_resumes_follow(qapp):
    table = LogTableWidget()
    table.resize(600, 300)
    table.show()
    qapp.processEvents()

    entries = [_make_entry(i) for i in range(80)]
    table.set_entries(entries)
    table.set_follow_mode(True)
    table.flush_auto_scroll()
    qapp.processEvents()

    # Scroll up and append
    table._table.verticalScrollBar().setValue(5)
    qapp.processEvents()
    table.append_entries([_make_entry(80), _make_entry(81)])
    qapp.processEvents()

    assert table._follow_pill.isVisible()
    assert table.follow_new_events_count() == 2

    paused_signals = []
    table.follow_paused_changed.connect(paused_signals.append)

    # Click pill
    table._follow_pill.click()
    table.flush_auto_scroll()
    qapp.processEvents()

    assert not table.is_follow_paused()
    assert table.follow_new_events_count() == 0
    assert not table._follow_pill.isVisible()
    assert len(paused_signals) == 1 and paused_signals[0] is False
    assert table.is_at_bottom()


def test_scrolling_back_to_bottom_resumes_follow(qapp):
    table = LogTableWidget()
    table.resize(600, 300)
    table.show()
    qapp.processEvents()

    entries = [_make_entry(i) for i in range(80)]
    table.set_entries(entries)
    table.set_follow_mode(True)
    table.flush_auto_scroll()
    qapp.processEvents()

    # Scroll up and append
    v_bar = table._table.verticalScrollBar()
    v_bar.setValue(5)
    qapp.processEvents()
    table.append_entries([_make_entry(80)])
    qapp.processEvents()
    assert table._follow_pill.isVisible()

    # Manually scroll back to bottom
    v_bar.setValue(v_bar.maximum())
    qapp.processEvents()

    assert not table.is_follow_paused()
    assert table.follow_new_events_count() == 0
    assert not table._follow_pill.isVisible()

    # New entries now auto-scroll
    table.append_entries([_make_entry(81)])
    table.flush_auto_scroll()
    qapp.processEvents()
    assert table.is_at_bottom()


def test_set_entries_when_follow_active(qapp):
    pane = LogPane(loader=lambda worker: [])
    pane.resize(700, 400)
    pane.show()
    qapp.processEvents()

    pane.set_tail_mode(True)
    entries = [_make_entry(i) for i in range(70)]
    pane.set_entries(entries)
    pane._table.flush_auto_scroll()
    qapp.processEvents()

    assert pane._table.is_at_bottom()

    # Scroll up in pane's table
    pane._table._table.verticalScrollBar().setValue(10)
    qapp.processEvents()
    assert pane._table.is_follow_paused()

    # Reload with 5 additional entries
    more_entries = [_make_entry(i) for i in range(75)]
    pane.set_entries(more_entries)
    qapp.processEvents()

    # Still paused, scroll position preserved, counter updated
    assert pane._table.is_follow_paused()
    assert pane._table.follow_new_events_count() == 5
    assert pane._table._follow_pill.isVisible()


def test_pane_append_entries_filtering(qapp):
    pane = LogPane(loader=lambda worker: [])
    pane.resize(700, 400)
    pane.show()
    qapp.processEvents()

    pane.set_tail_mode(True)
    pane.set_entries([_make_entry(0, level="Information")])
    pane._table.flush_auto_scroll()
    qapp.processEvents()

    # Filter critical chips
    pane._filter_level("critical")
    qapp.processEvents()

    # Append 1 info and 1 critical
    pane.append_entries([
        _make_entry(1, level="Information", message="info msg"),
        _make_entry(2, level="Critical", message="crit msg"),
    ])
    pane._table.flush_auto_scroll()
    qapp.processEvents()

    # Only critical should pass filter to table
    assert pane._table._model.rowCount() == 1
    item = pane._table._model.item(0, 2)
    assert item.text() == "Critical"
    assert pane._table.is_at_bottom()


def test_rapid_burst_throttle_and_debounce(qapp):
    table = LogTableWidget()
    table.resize(600, 300)
    table.show()
    qapp.processEvents()

    table.set_follow_mode(True)
    table.set_entries([_make_entry(0)])
    qapp.processEvents()

    # Rapid fire 50 appends
    for i in range(1, 51):
        table.append_entries([_make_entry(i)])

    # Flush any trailing timer debounced auto-scroll
    table.flush_auto_scroll()
    qapp.processEvents()

    assert table._model.rowCount() == 51
    assert table.is_at_bottom()
    assert table.follow_new_events_count() == 0


def test_clear_resets_tail_state(qapp):
    table = LogTableWidget()
    table.resize(600, 300)
    table.show()
    qapp.processEvents()

    table.set_entries([_make_entry(i) for i in range(50)])
    table.set_follow_mode(True)
    table.flush_auto_scroll()
    qapp.processEvents()

    # Scroll up and add new events
    table._table.verticalScrollBar().setValue(5)
    qapp.processEvents()
    table.append_entries([_make_entry(50), _make_entry(51)])
    qapp.processEvents()
    assert table._follow_pill.isVisible()
    assert table.follow_new_events_count() == 2

    # Clear
    table.clear()
    qapp.processEvents()
    assert table.follow_new_events_count() == 0
    assert not table.is_follow_paused()
    assert not table._follow_pill.isVisible()
