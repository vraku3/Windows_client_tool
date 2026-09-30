"""cleanup_scanner.exclusions -- the user-set "never scan this path again"
list, and its enforcement inside scan_cache.cached_scan.

Mirrors test_cleanup_history.py's structure (real-machine path check +
tmp_path-isolated round trips) for the identically-shaped exclusions file.
"""
import json
import os

import pytest

from modules.cleanup.cleanup_scanner import exclusions as ex


# ── storage ──────────────────────────────────────────────────────────────

def test_config_path_is_under_the_real_appdata_folder():
    """Real-machine check: this box's actual %APPDATA% must be the parent
    of where the exclusions file lands, and _config_path() must be able
    to create that folder without raising (it does today via mkdir)."""
    path = ex._config_path()
    appdata = os.environ.get("APPDATA")
    assert appdata, "this machine has no APPDATA -- unexpected on Windows"
    assert os.path.commonpath([str(path), appdata]) == os.path.normpath(appdata)
    assert path.parent.is_dir()


def test_list_exclusions_on_no_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_config_path", lambda: tmp_path / "nope.json")
    assert ex.list_exclusions() == []


def test_add_then_list_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_config_path", lambda: tmp_path / "excl.json")
    assert ex.add_exclusion(r"C:\Users\me\SomeCache") is True
    assert ex.list_exclusions() == [r"C:\Users\me\SomeCache"]


def test_add_duplicate_normalized_path_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_config_path", lambda: tmp_path / "excl.json")
    ex.add_exclusion(r"C:\Users\me\Cache")
    # Same path, different case and a trailing separator -- still "the
    # same path" to Windows, so this must not add a second entry.
    added_again = ex.add_exclusion(r"c:\users\me\cache\\")
    assert added_again is False
    assert len(ex.list_exclusions()) == 1


def test_remove_exclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_config_path", lambda: tmp_path / "excl.json")
    ex.add_exclusion(r"C:\Foo")
    assert ex.remove_exclusion(r"C:\Foo") is True
    assert ex.list_exclusions() == []


def test_remove_exclusion_not_present_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_config_path", lambda: tmp_path / "excl.json")
    assert ex.remove_exclusion(r"C:\Nowhere") is False


def test_corrupt_file_is_empty_not_a_crash(tmp_path, monkeypatch, caplog):
    path = tmp_path / "excl.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ex, "_config_path", lambda: path)
    assert ex.list_exclusions() == []


def test_non_list_json_is_empty_not_a_crash(tmp_path, monkeypatch):
    path = tmp_path / "excl.json"
    path.write_text(json.dumps({"oops": "not a list"}), encoding="utf-8")
    monkeypatch.setattr(ex, "_config_path", lambda: path)
    assert ex.list_exclusions() == []


# ── matching ─────────────────────────────────────────────────────────────

def test_is_excluded_exact_match():
    assert ex.is_excluded(r"C:\Foo\Bar", [r"C:\Foo\Bar"]) is True


def test_is_excluded_nested_match():
    assert ex.is_excluded(r"C:\Foo\Bar\deep\file.txt", [r"C:\Foo\Bar"]) is True


def test_is_excluded_case_insensitive():
    assert ex.is_excluded(r"c:\foo\bar", [r"C:\Foo\Bar"]) is True


def test_is_excluded_sibling_is_not_a_match():
    """C:\\Foo2 must not be excluded by an exclusion on C:\\Foo -- a naive
    startswith() without the separator would wrongly match it."""
    assert ex.is_excluded(r"C:\Foo2\file.txt", [r"C:\Foo"]) is False


def test_is_excluded_with_no_rules_is_false():
    assert ex.is_excluded(r"C:\Anything", []) is False


# ── filter_items ─────────────────────────────────────────────────────────

class _Item:
    def __init__(self, path):
        self.path = path


def test_filter_items_removes_excluded_and_keeps_the_rest():
    items = [_Item(r"C:\Keep"), _Item(r"C:\Excluded\deep\file")]
    kept = ex.filter_items(items, [r"C:\Excluded"])
    assert [i.path for i in kept] == [r"C:\Keep"]


def test_filter_items_with_no_exclusions_is_the_same_list():
    items = [_Item(r"C:\Keep")]
    assert ex.filter_items(items, []) is items


def test_filter_items_reads_the_real_list_when_none_given(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_config_path", lambda: tmp_path / "excl.json")
    ex.add_exclusion(r"C:\Excluded")
    items = [_Item(r"C:\Keep"), _Item(r"C:\Excluded\file")]
    kept = ex.filter_items(items)
    assert [i.path for i in kept] == [r"C:\Keep"]


# ── enforcement inside scan_cache ───────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_scan_cache():
    from modules.cleanup.cleanup_scanner import scan_cache
    scan_cache.invalidate()
    yield
    scan_cache.invalidate()


def test_cached_scan_filters_an_excluded_item(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import ScanItem, ScanResult, scan_cache

    monkeypatch.setattr(ex, "_config_path", lambda: tmp_path / "excl.json")
    ex.add_exclusion(r"C:\Excluded")

    def scanner(min_age_days=0):
        r = ScanResult()
        r.items = [
            ScanItem(path=r"C:\Keep", size=10, is_dir=False),
            ScanItem(path=r"C:\Excluded\deep\file", size=90, is_dir=False),
        ]
        r.total_size = 100
        return r

    result = scan_cache.cached_scan(scanner, 0)
    assert [i.path for i in result.items] == [r"C:\Keep"]
    assert result.total_size == 10


def test_removing_an_exclusion_reveals_the_item_without_a_fresh_scan(tmp_path, monkeypatch):
    """Exclusions filter the CALLER'S COPY, never the entry cached against
    the real scanner -- so lifting a rule takes effect on the very next
    call, without waiting for the TTL or invalidating the cache, and
    without re-walking the disk."""
    from modules.cleanup.cleanup_scanner import ScanItem, ScanResult, scan_cache

    monkeypatch.setattr(ex, "_config_path", lambda: tmp_path / "excl.json")
    ex.add_exclusion(r"C:\Excluded")

    calls = []

    def scanner(min_age_days=0):
        calls.append(1)
        r = ScanResult()
        r.items = [ScanItem(path=r"C:\Excluded", size=50, is_dir=True)]
        r.total_size = 50
        return r

    filtered = scan_cache.cached_scan(scanner, 0)
    assert filtered.items == []

    ex.remove_exclusion(r"C:\Excluded")
    restored = scan_cache.cached_scan(scanner, 0)
    assert [i.path for i in restored.items] == [r"C:\Excluded"]
    assert len(calls) == 1, "the scanner should not have been re-run at all"
