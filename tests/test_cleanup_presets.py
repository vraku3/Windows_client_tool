"""Cleanup's preset system: a plain safety-level allow-set per preset,
plus a hard exclusion list (orphaned profiles, virtual disks) no preset
-- not even Aggressive -- may ever override.
"""
import pytest

from modules.cleanup.cleanup_presets import (
    PRESETS, NEVER_INCLUDED, preset_names, items_for_preset,
)
from modules.cleanup.cleanup_scanner import ScanItem, ScanResult


def _result(*, safe=0, caution=0, danger=0):
    r = ScanResult()
    for n, safety in ((safe, "safe"), (caution, "caution"), (danger, "danger")):
        for i in range(n):
            r.items.append(ScanItem(path=f"C:\\{safety}_{i}", size=100, is_dir=False, safety=safety))
    return r


def test_preset_names_includes_custom():
    names = preset_names()
    assert set(names) == {"light", "thorough", "aggressive", "custom"}


def test_light_includes_only_safe_items():
    results = {"a": _result(safe=2, caution=1, danger=1)}
    items = items_for_preset("light", results, {"a": "scan_temp_files"})
    assert len(items) == 2
    assert all(i.safety == "safe" for i in items)


def test_thorough_includes_safe_and_caution():
    results = {"a": _result(safe=2, caution=3, danger=1)}
    items = items_for_preset("thorough", results, {"a": "scan_temp_files"})
    assert len(items) == 5
    assert all(i.safety in ("safe", "caution") for i in items)


def test_aggressive_includes_everything_except_never_included():
    results = {
        "a": _result(safe=1, caution=1, danger=1),
        "b": _result(danger=1),
    }
    id_to_scanner = {"a": "scan_temp_files", "b": "scan_orphaned_user_profiles"}
    items = items_for_preset("aggressive", results, id_to_scanner)
    # "a"'s 3 items included; "b"'s single danger item excluded regardless
    # of Aggressive's own safety_levels allowing "danger" in general.
    assert len(items) == 3
    assert all(i.path.startswith("C:\\") and "b" not in i.path for i in items)


def test_never_included_scanner_names_match_the_real_scanners():
    # Cross-check against the actual codebase: these three scanner names
    # must exist as real functions, or this exclusion list is silently
    # protecting nothing.
    from modules.cleanup import cleanup_scanner as cs
    for name in NEVER_INCLUDED:
        assert hasattr(cs, name), f"{name} is not a real scanner function"


def test_custom_raises_value_error():
    with pytest.raises(ValueError):
        items_for_preset("custom", {}, {})


def test_unrecognized_preset_raises_value_error():
    with pytest.raises(ValueError):
        items_for_preset("does_not_exist", {}, {})
