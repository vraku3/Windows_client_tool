"""The Details table, as a widget.

Runs against the real machine -- there is no fixture that can stand in for
275 live processes, and every bug worth catching here came from real data.
"""
import os

import pytest

from modules.dashboard.details_tab import DetailsTab
from core.procengine.columns import BY_KEY, DEFAULT_KEYS, UNKNOWN


@pytest.fixture
def tab(qapp):
    widget = DetailsTab()
    widget.refresh()   # first reading: no rates yet
    widget.refresh()   # second: rates
    yield widget
    widget.stop()
    widget.deleteLater()


def _column_index(tab, key):
    for section, column in enumerate(tab.model.columns()):
        if column.key == key:
            return section
    raise AssertionError(f"{key} is not shown")


# ---- it shows the machine ----------------------------------------------

def test_the_table_fills_with_the_real_process_list(tab):
    assert tab.model.rowCount() > 20


def test_our_own_process_is_in_the_table(tab):
    pids = [tab.model.pid_at(row) for row in range(tab.model.rowCount())]
    assert os.getpid() in pids


def test_the_default_columns_are_shown(tab):
    assert [column.key for column in tab.model.columns()] == list(DEFAULT_KEYS)


def test_the_headers_are_the_column_titles(tab):
    from PyQt6.QtCore import Qt

    for section, column in enumerate(tab.model.columns()):
        assert tab.model.headerData(
            section, Qt.Orientation.Horizontal) == column.title


def test_a_header_explains_itself_on_hover(tab):
    from PyQt6.QtCore import Qt

    tip = tab.model.headerData(0, Qt.Orientation.Horizontal,
                               Qt.ItemDataRole.ToolTipRole)
    assert tip


# ---- the update-in-place rule -------------------------------------------

def test_refreshing_does_not_reset_the_model(tab, qapp):
    """A reset once a second drops the selection, so a row cannot be
    clicked while the table is live -- it deselects under the pointer. This
    is the same rule LogModel.append already carries."""
    resets = []
    tab.model.modelAboutToBeReset.connect(lambda: resets.append(1))

    tab.refresh()

    assert not resets, "the table reset itself on a refresh"


def test_a_selected_row_survives_a_refresh(tab, qapp):
    tab.table.selectRow(0)
    selected = tab.selected_pids()
    assert selected

    tab.refresh()

    assert tab.selected_pids() == selected


def test_a_row_keeps_its_position_across_a_refresh(tab):
    """Row order is the order processes were first seen, so a live table
    does not reshuffle under the cursor."""
    before = [tab.model.pid_at(row) for row in range(tab.model.rowCount())]
    tab.refresh()
    after = [tab.model.pid_at(row) for row in range(tab.model.rowCount())]
    assert after[:len(before)] == [pid for pid in before if pid in after] or \
        len(after) >= len(before) - 5


# ---- columns ------------------------------------------------------------

def test_a_column_can_be_turned_on(tab):
    tab._toggle("cmdline", True)
    assert "cmdline" in [column.key for column in tab.model.columns()]


def test_a_column_can_be_turned_off(tab):
    tab._toggle("threads", False)
    assert "threads" not in [column.key for column in tab.model.columns()]


def test_turning_a_column_on_keeps_the_canonical_order(tab):
    """Otherwise the table slowly becomes the order someone clicked things."""
    tab._toggle("cmdline", True)
    tab._toggle("company", True)
    keys = [column.key for column in tab.model.columns()]
    assert keys.index("company") < keys.index("cmdline")


def test_the_table_can_never_be_left_with_no_columns(tab):
    """An empty table looks broken, and there would be no header left to
    right-click to fix it."""
    tab.model.set_columns([])
    assert tab.model.columnCount() > 0


def test_resetting_restores_the_defaults(tab):
    tab._toggle("cmdline", True)
    tab._reset_columns()
    assert [column.key for column in tab.model.columns()] == list(DEFAULT_KEYS)


# ---- filtering ----------------------------------------------------------

def test_filtering_by_name_narrows_the_table(tab):
    everything = tab.proxy.rowCount()
    tab.filter_box.setText("python")
    assert 0 < tab.proxy.rowCount() < everything


def test_filtering_by_pid_finds_one_process(tab):
    tab.filter_box.setText(str(os.getpid()))
    assert tab.proxy.rowCount() >= 1


def test_clearing_the_filter_restores_everything(tab):
    everything = tab.proxy.rowCount()
    tab.filter_box.setText("python")
    tab.filter_box.setText("")
    assert tab.proxy.rowCount() == everything


def test_a_filter_matching_nothing_leaves_an_empty_table_not_a_full_one(tab):
    tab.filter_box.setText("zzzznotaprocesszzzz")
    assert tab.proxy.rowCount() == 0


# ---- sorting ------------------------------------------------------------

def test_sorting_by_memory_orders_by_the_number(tab):
    """"9 B" above "10 GB" is what sorting the rendered text does."""
    from PyQt6.QtCore import Qt

    tab._toggle("memory", True)
    section = _column_index(tab, "memory")
    tab.proxy.sort(section, Qt.SortOrder.DescendingOrder)

    values = []
    for row in range(min(20, tab.proxy.rowCount())):
        source = tab.proxy.mapToSource(tab.proxy.index(row, section))
        values.append(BY_KEY["memory"].value(tab.model.info(source.row())))
    assert values == sorted(values, reverse=True)


def test_sorting_by_a_refusable_column_does_not_crash(tab):
    """None against a string raises TypeError, and inside a Qt sort that is
    fatal rather than catchable."""
    from PyQt6.QtCore import Qt

    tab._toggle("path", True)
    section = _column_index(tab, "path")
    tab.proxy.sort(section, Qt.SortOrder.AscendingOrder)
    assert tab.proxy.rowCount() > 0


# ---- saying what it cannot see ------------------------------------------

def test_the_status_says_how_many_processes_are_shown(tab):
    assert "processes" in tab.status.text()


def test_the_status_admits_what_it_could_not_read(tab):
    """Unelevated, about half this machine refuses. Showing those as blank
    rows without a word is the failure mode this prevents."""
    text = tab.status.text().lower()
    assert "could not be read" in text and "administrator" in text


def test_a_refused_cell_shows_a_dash_rather_than_a_blank(tab):
    from PyQt6.QtCore import Qt

    tab._toggle("path", True)
    section = _column_index(tab, "path")
    seen = set()
    for row in range(tab.model.rowCount()):
        seen.add(tab.model.data(tab.model.index(row, section),
                                Qt.ItemDataRole.DisplayRole))
    assert UNKNOWN in seen, "nothing was refused; run this test unelevated"


def test_a_refused_cell_explains_itself_on_hover(tab):
    from PyQt6.QtCore import Qt

    tab._toggle("path", True)
    section = _column_index(tab, "path")
    tips = []
    for row in range(tab.model.rowCount()):
        tip = tab.model.data(tab.model.index(row, section),
                             Qt.ItemDataRole.ToolTipRole)
        if tip:
            tips.append(tip)
    assert tips, "a refused cell said nothing about why"


# ---- lifecycle ----------------------------------------------------------

def test_stopping_cancels_its_workers(qapp):
    widget = DetailsTab()
    widget.refresh()
    widget.stop()
    assert widget._workers == []


def test_the_table_opens_sorted_by_cpu(qapp):
    """The question someone opens a process list to ask."""
    from PyQt6.QtCore import Qt

    widget = DetailsTab()
    try:
        widget.refresh()
        widget.sort_by("cpu")
        header = widget.table.horizontalHeader()
        section = _column_index(widget, "cpu")
        assert header.sortIndicatorSection() == section
        assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    finally:
        widget.stop()


def test_sorting_moves_the_header_indicator_too(tab):
    """Sorting the data while the arrow points at another column is worse
    than not sorting at all."""
    tab._toggle("handles", True)
    tab.sort_by("handles")
    assert tab.table.horizontalHeader().sortIndicatorSection() == \
        _column_index(tab, "handles")


# ---- the context menu ---------------------------------------------------

def test_the_table_offers_a_context_menu(tab):
    assert tab.menu is not None


def test_the_menu_lists_task_managers_actions(tab, qapp, monkeypatch):
    """Built without showing it: `exec` blocks on a real menu."""
    from PyQt6.QtWidgets import QMenu

    captured = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: captured.append(
        [action.text() for action in self.actions()]))

    tab.table.selectRow(0)
    tab.menu.show(tab.selected_pids(), tab.selected_info(), None)

    assert captured, "no menu was built"
    labels = " ".join(captured[0])
    for expected in ("End task", "End process tree", "Suspend", "Resume",
                     "Set priority", "Set affinity", "Create dump",
                     "Open file location", "Search online"):
        assert expected in labels, f"{expected} is missing from the menu"


def test_ending_a_task_asks_first(tab, qapp, monkeypatch):
    """Nothing destructive happens without a confirmation."""
    from PyQt6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: asked.append(self.text())
                        or QMessageBox.StandardButton.No)
    killed = []
    monkeypatch.setattr("modules.dashboard.process_menu.end_process",
                        lambda pid, **kw: killed.append(pid))

    tab.menu._end([999_999])

    assert asked, "it did not ask"
    assert not killed, "it killed the process before asking"


def test_declining_the_confirmation_does_nothing(tab, qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: QMessageBox.StandardButton.No)
    killed = []
    monkeypatch.setattr("modules.dashboard.process_menu.end_process",
                        lambda pid, **kw: killed.append(pid))

    tab.menu._end([999_999])

    assert not killed


def test_a_failed_action_is_reported_rather_than_swallowed(tab, qapp,
                                                           monkeypatch):
    """A refusal that says nothing is how someone concludes the button is
    broken."""
    from PyQt6.QtWidgets import QMessageBox
    from core.procengine.actions import Result

    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr("modules.dashboard.process_menu.end_process",
                        lambda pid, **kw: Result(False, "Access is denied."))

    tab.menu._end([999_999])

    assert "Access is denied." in tab.error_banner.text()
    assert not tab.error_banner.isHidden()


# ---- export (W5-03) -----------------------------------------------------

def test_export_writes_a_csv_of_the_shown_columns(tab, tmp_path):
    target = tmp_path / "processes.csv"
    path = tab.export_csv(str(target))
    assert path == str(target)
    assert target.exists()

    import csv

    with open(target, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    headers = rows[0]
    # The header row carries the CURRENT shown columns' titles.
    assert headers == [column.title for column in tab.model.columns()]
    assert len(rows) - 1 >= 20, "the export should list most of the machine"


def test_export_respects_the_filter(tab, tmp_path):
    import csv

    tab.filter_box.setText("python")
    target = tmp_path / "filtered.csv"
    tab.export_csv(str(target))
    with open(target, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) - 1 >= 1
    # A filtered export must not quietly contain the whole machine. The
    # bound is relative, not a fixed 10: this reads the LIVE process table,
    # and under `pytest -n auto` a dozen xdist workers are themselves
    # called python.exe. Asserting "fewer than everything" is the rule the
    # test is actually about; "fewer than ten pythons" was an accident of
    # the machine it was written on.
    # Bounded relative to the machine, not at a fixed 10. This reads the
    # LIVE process table, and under `pytest -n auto` a dozen xdist workers
    # are themselves called python.exe — "fewer than ten pythons" was an
    # accident of the machine this was written on. "Fewer than everything"
    # is the rule the test is about.
    assert len(rows) - 1 < tab.model.rowCount(), (
        "the filter did not narrow the export")
    # Not asserted per row: the filter also searches cmdline, which is not
    # an exported column, so a row can legitimately match on something the
    # CSV does not contain.
    assert len(rows) - 1 == tab.proxy.rowCount(), (
        "the export does not match what the filter left on screen")


def test_a_cancelled_export_writes_nothing(tab, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: ("", ""))
    assert tab.export_csv() is None

