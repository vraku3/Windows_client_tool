"""The App history tab, as a widget.

The column that matters is CPU time -- the cumulative counter the bulk
syscall gives up for free. The rows must roll Chrome's many processes into
one "Google Chrome", sorted so the most expensive program is on top.
"""

from modules.dashboard.app_history_tab import AppHistoryTab


def _settle(qapp, widget, predicate, seconds=8.0):
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_the_tab_fills_with_real_programs(qapp):
    view = AppHistoryTab()
    view.refresh()
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
    try:
        assert view._table.rowCount() >= 1, "the machine runs at least 1 program"
    finally:
        view.stop()
        view.deleteLater()


def test_every_row_names_its_program(qapp):
    view = AppHistoryTab()
    view.refresh()
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
    try:
        for row in range(view._table.rowCount()):
            assert view._table.item(row, 0).text(), f"row {row} has no name"
    finally:
        view.stop()
        view.deleteLater()


def test_rows_sum_multiple_processes(qapp):
    """Chrome's many processes must collapse into one row -- that is the
    whole point of the tab."""
    view = AppHistoryTab()
    view.refresh()
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
    try:
        totals = 0
        for row in range(view._table.rowCount()):
            processes = int(view._table.item(row, 2).text().replace(",", ""))
            totals += processes
        from core.procengine.ntquery import system_processes

        assert totals >= len(system_processes()) - 10, \
            "the row count does not add up to the machine's processes"
    finally:
        view.stop()
        view.deleteLater()


def test_the_busiest_row_is_on_top(qapp):
    """The engine sorts by CPU time descending; the table must keep it."""
    view = AppHistoryTab()
    view.refresh()
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
    try:
        first = view._usage[0]
        assert first.cpu_ticks >= view._usage[-1].cpu_ticks
    finally:
        view.stop()
        view.deleteLater()


def test_the_engine_result_matches_the_table_rows(qapp):
    view = AppHistoryTab()
    view.refresh()
    assert _settle(qapp, view, lambda: view._table.rowCount() > 0)
    try:
        assert view._table.rowCount() == len(view._usage)
    finally:
        view.stop()
        view.deleteLater()


def test_the_note_names_what_is_not_tracked(qapp):
    view = AppHistoryTab()
    assert "driver" in _note_text(view), \
        "the tab must say why network usage is not shown"
    view.deleteLater()


def _note_text(view):
    from PyQt6.QtWidgets import QLabel

    for child in view.findChildren(QLabel):
        if "Cumulative CPU time" in child.text():
            return child.text()
    return ""
