"""Task Manager's Processes tab, as a widget.

The readable view: three groups, apps rolled up to one row each. Against the
real machine, like everything else in this engine.
"""
import pytest

from modules.dashboard.processes_tab import (MEMORY, PID_ROLE,
                                             ProcessesTab, VALUE_ROLE, _heat)
from core.procengine.grouping import GROUP_ORDER


@pytest.fixture
def tab(qapp):
    widget = ProcessesTab()
    widget.view_mode.setCurrentIndex(1)  # these tests exercise grouped mode
    widget.refresh()
    widget.refresh()
    yield widget
    widget.stop()
    widget.deleteLater()


def _headers(tab):
    return [tab.tree.topLevelItem(index).text(0)
            for index in range(tab.tree.topLevelItemCount())]


def _all_items(tab):
    from modules.dashboard.processes_tab import _walk
    return list(_walk(tab.tree))


# ---- the shape of it ----------------------------------------------------

def test_the_tree_is_built_from_the_real_machine(tab):
    assert tab.tree.topLevelItemCount() > 0


def test_the_groups_are_task_managers_three(tab):
    headers = _headers(tab)
    for name in GROUP_ORDER:
        assert any(header.startswith(name) for header in headers), \
            f"{name} is missing"


def test_each_group_header_carries_its_count(tab):
    for header in _headers(tab):
        assert "(" in header and ")" in header


def test_group_headers_are_not_selectable(tab):
    """A header is a label, not a process. Selecting one and pressing End
    task would be a question with no answer."""
    from PyQt6.QtCore import Qt

    for index in range(tab.tree.topLevelItemCount()):
        item = tab.tree.topLevelItem(index)
        assert not (item.flags() & Qt.ItemFlag.ItemIsSelectable)


def test_an_app_row_carries_a_pid(tab):
    pids = [item.data(0, PID_ROLE) for item in _all_items(tab)]
    assert any(pid is not None for pid in pids)


def test_a_multi_process_app_is_one_row_with_children(tab):
    """Chrome's twenty-one processes are one row someone can decide about."""
    parents = [item for item in _all_items(tab)
               if item.childCount() > 1 and item.data(0, PID_ROLE) is not None]
    assert parents, "nothing was rolled up; the tab is a flat list"


# ---- the numbers --------------------------------------------------------

def test_the_value_columns_are_populated(tab):
    rows = [item for item in _all_items(tab)
            if item.data(0, PID_ROLE) is not None]
    assert any(item.text(MEMORY) for item in rows)


def test_a_cell_keeps_its_sortable_value_not_just_the_text(tab):
    rows = [item for item in _all_items(tab)
            if item.data(0, PID_ROLE) is not None]
    assert any(item.data(MEMORY, VALUE_ROLE) is not None for item in rows)


def test_an_apps_memory_is_the_sum_of_its_processes(tab):
    """The number beside "Google Chrome" has to be what Chrome costs."""
    parents = [item for item in _all_items(tab)
               if item.childCount() > 1 and item.data(0, PID_ROLE) is not None]
    if not parents:
        pytest.skip("nothing is rolled up right now")
    parent = parents[0]
    children = sum(parent.child(index).data(MEMORY, VALUE_ROLE) or 0
                   for index in range(parent.childCount()))
    assert parent.data(MEMORY, VALUE_ROLE) == children


# ---- the heat tint ------------------------------------------------------

def test_the_busiest_cell_is_tinted():
    assert _heat(100, 100) is not None


def test_a_quiet_cell_is_not_tinted():
    """A wash of faint colour over every cell tells nobody anything."""
    assert _heat(1, 1000) is None


def test_nothing_is_tinted_when_there_is_no_value():
    assert _heat(None, 100) is None
    assert _heat(0, 100) is None


def test_the_tint_never_exceeds_full_strength():
    """A value above the ceiling (a rate measured across a short tick) must
    not produce an out-of-range alpha, which Qt would reject."""
    colour = _heat(500, 100)
    assert colour is not None
    assert 0 <= colour.alpha() <= 255


def test_the_tint_is_translucent_so_selection_still_reads(tab):
    colour = _heat(100, 100)
    assert colour.alpha() < 255


# ---- filtering ----------------------------------------------------------

def test_filtering_narrows_the_tree(tab):
    before = len(_all_items(tab))
    tab.filter_box.setText("zzzznotarealprocesszzz")
    assert len(_all_items(tab)) < before


def test_a_filter_matching_nothing_empties_the_tree(tab):
    tab.filter_box.setText("zzzznotarealprocesszzz")
    assert tab.tree.topLevelItemCount() == 0


def test_clearing_the_filter_restores_the_tree(tab):
    before = len(_all_items(tab))
    tab.filter_box.setText("zzzznotarealprocesszzz")
    tab.filter_box.setText("")
    assert len(_all_items(tab)) == before


# ---- staying usable while live ------------------------------------------

def test_an_expanded_app_stays_open_across_a_refresh(tab):
    """A rebuild once a second that folds the tree shut is unusable."""
    parents = [item for item in _all_items(tab)
               if item.childCount() > 1 and item.data(0, PID_ROLE) is not None]
    if not parents:
        pytest.skip("nothing is rolled up right now")
    parent = parents[0]
    pid = parent.data(0, PID_ROLE)
    parent.setExpanded(True)

    tab.refresh()

    again = next((item for item in _all_items(tab)
                  if item.data(0, PID_ROLE) == pid), None)
    assert again is not None and again.isExpanded()


def test_a_selected_row_survives_a_refresh(tab):
    rows = [item for item in _all_items(tab)
            if item.data(0, PID_ROLE) is not None]
    rows[0].setSelected(True)
    pid = rows[0].data(0, PID_ROLE)

    tab.refresh()

    assert pid in tab._selected_pids()


def test_the_end_button_is_disabled_until_something_is_selected(qapp):
    widget = ProcessesTab()
    try:
        widget.refresh()
        assert not widget.end_button.isEnabled()
    finally:
        widget.stop()


# ---- saying what it cannot see ------------------------------------------

def test_the_status_admits_what_it_could_not_read(tab):
    text = tab.status.text().lower()
    assert "processes" in text
    assert "could not be read" in text


# ---- lifecycle ----------------------------------------------------------

def test_stopping_cancels_its_workers(qapp):
    widget = ProcessesTab()
    widget.refresh()
    widget.stop()
    assert widget._workers == []


def test_the_tint_follows_the_theme(qapp):
    """A colour frozen for the dark pane reads as a pale smear on the light
    one -- the reason core.semantic_colors exists."""
    from core import semantic_colors

    was = semantic_colors.current_theme()
    try:
        semantic_colors.set_theme("dark")
        dark = _heat(100, 100)
        semantic_colors.set_theme("light")
        light = _heat(100, 100)
    finally:
        semantic_colors.set_theme(was)

    assert (dark.red(), dark.green(), dark.blue()) != \
           (light.red(), light.green(), light.blue())
