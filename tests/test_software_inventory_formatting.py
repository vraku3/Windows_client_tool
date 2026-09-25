r"""Software Inventory showed `20260907` and "0.0 MB" for unknown sizes.

Seen 2026-09-25 in the real tab: install dates as raw YYYYMMDD, and every
program whose installer never wrote EstimatedSize labelled "0.0 MB" -- a
measurement that was never taken, presented as one.
"""
from modules.software_inventory.software_module import (
    format_estimated_size, format_install_date)


def test_a_yyyymmdd_date_is_readable():
    assert format_install_date("20260907") == "2026-09-07"


def test_something_that_is_not_a_date_is_left_as_written():
    for raw in ("9/7/2026", "Sept 2026", "20261399", "abc", ""):
        assert format_install_date(raw) == raw


def test_a_missing_or_zero_size_is_a_dash_not_zero_megabytes():
    for raw in ("", None, "0", "-5", "n/a"):
        assert format_estimated_size(raw) == "\u2014", raw


def test_size_is_kilobytes_to_megabytes():
    assert format_estimated_size("1024") == "1.0 MB"
    assert format_estimated_size("121344") == "118.5 MB"
