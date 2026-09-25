r"""A number handed to a table cell must show as text, not vanish.

`QTableWidgetItem(9)` is PyQt's item-TYPE overload, so it made an empty cell
without an error. Found 2026-09-24: Store Apps' Architecture (enum as a number)
and System Restore's Type (RestorePointType as a number) were blank on every
row of the real machine.
"""
from core.table_ui import centered_item
from modules.restore_manager.restore_module import restore_point_type_name


def test_an_int_shows_as_its_text(qapp):
    assert centered_item(9).text() == "9"


def test_none_is_an_empty_cell_not_the_word_none(qapp):
    assert centered_item(None).text() == ""


def test_zero_is_shown(qapp):
    assert centered_item(0).text() == "0"


def test_strings_are_unchanged(qapp):
    assert centered_item("X64").text() == "X64"
    assert centered_item("x", sortable=True).text() == "x"


def test_restore_point_types_have_names():
    assert restore_point_type_name(0) == "Application install"
    assert restore_point_type_name("12") == "System settings change"
    assert restore_point_type_name(10) == "Device driver install"


def test_an_unknown_restore_point_type_still_says_something():
    assert restore_point_type_name(77) == "Type 77"
    assert restore_point_type_name(None) == "Unknown"
    assert restore_point_type_name("Custom") == "Custom"
