"""Spec 5.5: modes and units."""
import os

import pytest

from modules.treesize.ui.formatting import (
    Mode, Unit, age_tier, bar_fraction, format_bytes, format_count,
    format_percent, format_value, node_value, percent_of_parent,
)
from modules.treesize.scan.filters import days_to_filetime, filetime_now
from modules.treesize.store.node_store import NodeStore, DIR
from modules.treesize.store.rollup import rollup


def _store():
    s = NodeStore()
    root = s.add(-1, "C:", attrs=DIR)
    docs = s.add(root, "Docs", attrs=DIR)
    s.add(docs, "a.bin", size=750, alloc=1024)
    s.add(root, "b.bin", size=250, alloc=256)
    s.build_child_lists()
    rollup(s)
    return s, root, docs


def test_auto_picks_the_largest_unit_that_fits():
    assert format_bytes(999) == "999 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(5 * 1024 ** 3) == "5.0 GB"
    assert format_bytes(3 * 1024 ** 4) == "3.0 TB"


def test_auto_produces_mixed_units_which_is_the_point():
    """Pro's columns mix units row to row; that is Auto doing its job."""
    assert format_bytes(2048) == "2.0 KB"
    assert format_bytes(2 * 1024 ** 2) == "2.0 MB"


def test_an_explicit_unit_overrides_auto():
    assert format_bytes(5 * 1024 ** 3, Unit.MB) == "5,120.0 MB"
    assert format_bytes(1536, Unit.B) == "1,536 B"


def test_decimals_are_configurable():
    assert format_bytes(1536, Unit.KB, decimals=0) == "2 KB"
    assert format_bytes(1536, Unit.KB, decimals=3) == "1.500 KB"


def test_bytes_are_never_shown_fractionally():
    assert format_bytes(999, Unit.B, decimals=3) == "999 B"


def test_zero_and_negative_values():
    assert format_bytes(0) == "0 B"
    assert format_bytes(-1536) == "-1.5 KB", "size deltas keep their sign"


def test_counts_and_percents():
    assert format_count(1234567) == "1,234,567"
    assert format_percent(12.345) == "12.3%"


def test_node_value_follows_the_mode():
    s, root, docs = _store()
    assert node_value(s, root, Mode.SIZE) == 1000
    assert node_value(s, root, Mode.ALLOCATED) == 1280
    assert node_value(s, root, Mode.FILES) == 2


def test_percent_of_parent():
    s, root, docs = _store()
    assert percent_of_parent(s, docs) == pytest.approx(75.0)
    assert percent_of_parent(s, root) == 100.0, "a root is the whole of itself"


def test_percent_of_a_zero_sized_parent_is_zero_not_a_crash():
    s = NodeStore()
    root = s.add(-1, "C:", attrs=DIR)
    child = s.add(root, "empty", attrs=DIR)
    s.build_child_lists()
    rollup(s)
    assert percent_of_parent(s, child) == 0.0


def test_format_value_switches_representation_with_the_mode():
    s, root, docs = _store()
    assert format_value(s, docs, Mode.SIZE) == "750 B"
    assert format_value(s, docs, Mode.ALLOCATED) == "1.0 KB"
    assert format_value(s, docs, Mode.FILES) == "1"
    assert format_value(s, docs, Mode.PERCENT) == "75.0%"


def test_bar_fraction_is_relative_to_the_parent():
    s, root, docs = _store()
    assert bar_fraction(s, docs, Mode.SIZE) == pytest.approx(0.75)
    assert bar_fraction(s, root, Mode.SIZE) == 1.0


def test_bar_fraction_follows_the_mode():
    s, root, docs = _store()
    assert bar_fraction(s, docs, Mode.FILES) == pytest.approx(0.5)


def test_bar_fraction_is_clamped_and_safe_on_empty_parents():
    s = NodeStore()
    root = s.add(-1, "C:", attrs=DIR)
    child = s.add(root, "x", attrs=DIR)
    s.build_child_lists()
    rollup(s)
    assert bar_fraction(s, child, Mode.SIZE) == 0.0


def test_unknown_mode_is_rejected_rather_than_guessed():
    s, root, _ = _store()
    with pytest.raises(ValueError):
        node_value(s, root, "nonsense")


# ---- age_tier (Last Modified colouring) ---------------------------------

def test_no_timestamp_is_not_coloured():
    """A missing mtime is unknown age, not "old" -- see filters.py's own
    "a rule that could not actually be evaluated" reasoning for min/max_age."""
    now = filetime_now()
    assert age_tier(0, now) is None


def test_a_future_timestamp_is_not_coloured():
    now = filetime_now()
    assert age_tier(now + days_to_filetime(5), now) is None


def test_recently_touched_is_active():
    now = filetime_now()
    assert age_tier(now - days_to_filetime(1), now) == "active"


def test_the_ordinary_middle_is_left_uncoloured():
    now = filetime_now()
    assert age_tier(now - days_to_filetime(30), now) is None
    assert age_tier(now - days_to_filetime(180), now) is None


def test_a_year_untouched_is_stale():
    now = filetime_now()
    assert age_tier(now - days_to_filetime(365), now) == "stale"
    assert age_tier(now - days_to_filetime(2000), now) == "stale"


def test_the_boundary_at_seven_days_is_exclusive_of_active():
    now = filetime_now()
    assert age_tier(now - days_to_filetime(6.9), now) == "active"
    assert age_tier(now - days_to_filetime(7.1), now) is None


def test_a_real_inbox_driver_on_this_machine_is_stale():
    """C:\\Windows\\System32\\drivers\\beep.sys is an inbox Windows driver
    that ships with the OS and is never touched again -- measured on this
    real machine at ~912 days old (2026-09-30), which is why it is the
    fixture rather than a synthetic timestamp: real disks do carry files
    this old, and the tier this module assigns them should say so."""
    path = r"C:\Windows\System32\drivers\beep.sys"
    if not os.path.exists(path):
        pytest.skip("beep.sys not present on this machine")
    mtime_unix = os.path.getmtime(path)
    now = filetime_now()
    from modules.treesize.scan.filters import (
        FILETIME_EPOCH_OFFSET_SECONDS, FILETIME_TICKS_PER_SECOND,
    )
    mtime_filetime = int((mtime_unix + FILETIME_EPOCH_OFFSET_SECONDS)
                         * FILETIME_TICKS_PER_SECOND)
    assert age_tier(mtime_filetime, now) == "stale"
