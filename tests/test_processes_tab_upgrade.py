"""The Processes tab's extra columns, filter chips, sorting and detail panel."""
import pytest

from modules.dashboard import processes_tab as pt
from modules.dashboard import process_view as pv


@pytest.fixture
def tab(qapp):
    t = pt.ProcessesTab()
    t._apply(t._read())          # no pool: synchronous
    t._apply(t._read())          # second read gives CPU rates
    return t


def _all_items(t):
    return [i for i in pt._walk(t.tree) if i.data(0, pt.PID_ROLE) is not None]


def test_extra_columns_are_hidden_by_default_except_the_defaults(tab):
    header = tab.tree.header()
    for offset, col in enumerate(pv.COLUMNS):
        assert header.isSectionHidden(pt.FIRST_EXTRA + offset) == (col.key not in pv.DEFAULT_VISIBLE)


def test_showing_a_column_fills_it(tab):
    tab._toggle_column("threads", True)
    tab._rebuild()
    idx = pt.FIRST_EXTRA + [c.key for c in pv.COLUMNS].index("threads")
    assert not tab.tree.header().isSectionHidden(idx)
    assert any(i.text(idx) for i in _all_items(tab))


def test_a_filter_chip_narrows_the_rows_and_all_restores(tab):
    everything = len(_all_items(tab))
    tab._set_filter("elevated")
    narrowed = len(_all_items(tab))
    assert narrowed <= everything
    tab._set_filter("all")
    assert len(_all_items(tab)) == everything


def test_chip_labels_carry_counts(tab):
    chip, _ = tab._chips["cpu"]
    assert chip.text().startswith("High CPU (")


def _memory_column(tab):
    group = tab.tree.topLevelItem(0)
    return [group.child(i).data(pt.MEMORY, pt.VALUE_ROLE) or 0 for i in range(group.childCount())]


def test_sorting_by_memory_orders_rows_within_a_group(tab):
    tab._sort_by(pt.MEMORY)
    values = _memory_column(tab)
    assert values == sorted(values, reverse=True)
    tab._sort_by(pt.MEMORY)            # second click flips
    values = _memory_column(tab)
    assert values == sorted(values)


def test_selecting_a_row_fills_the_detail_panel(tab):
    item = next(i for i in _all_items(tab) if i.data(0, pt.PID_ROLE) > 4)
    item.setSelected(True)
    assert f"PID {item.data(0, pt.PID_ROLE)}" in tab.detail.toPlainText()


def test_the_search_box_matches_command_lines(tab):
    info = next(i for i in tab._snapshot.by_pid.values() if i.details.cmdline)
    needle = info.details.cmdline.lower()[-12:]
    assert pt._matches(info, needle)


def test_a_refresh_keeps_the_selected_process_and_its_detail(tab):
    item = next(i for i in _all_items(tab) if i.data(0, pt.PID_ROLE) > 4)
    pid = item.data(0, pt.PID_ROLE)
    item.setSelected(True)
    tab._rebuild()
    assert f"PID {pid}" in tab.detail.toPlainText()
    assert tab._selected_pids() == [pid]


def test_name_column_is_not_squeezed_by_the_extra_columns(tab):
    assert tab.tree.header().sectionSize(0) >= 200
