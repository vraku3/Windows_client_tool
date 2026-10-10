import os
import subprocess
import time

import pytest

from _sparse import make_sparse
from modules.cleanup.cleanup_scanner import catalog, discover


def cache(root, relative="App/Cache", size=32):
    path = root / relative
    path.mkdir(parents=True)
    make_sparse(path / "payload", size)
    return path


def test_cache_found_and_sorted(tmp_path):
    small = cache(tmp_path)
    big = cache(tmp_path, "Other/GPUCache", 128)
    rows = discover.find_uncovered_caches(1, targets=[], roots=[str(tmp_path)], excluded=[])
    assert [(r.path, r.size, r.name) for r in rows.candidates] == [
        (str(big), 128, "GPUCache"), (str(small), 32, "Cache")]


def test_covered_cache_excluded(tmp_path):
    path = cache(tmp_path)
    assert discover.find_uncovered_caches(1, [str(path)], [str(tmp_path)], excluded=[]).candidates == []


@pytest.mark.parametrize("ancestor", ["Local Storage", "IndexedDB"])
def test_sensitive_ancestor_excluded(tmp_path, ancestor):
    path = cache(tmp_path, f"App/{ancestor}/Cache")
    assert discover.find_uncovered_caches(1, [], [str(tmp_path)], excluded=[]).candidates == []
    assert discover.find_uncovered_caches(1, [], [str(path.parent)], excluded=[]).candidates == []


def test_minimum_size(tmp_path):
    cache(tmp_path)
    assert discover.find_uncovered_caches(33, [], [str(tmp_path)], excluded=[]).candidates == []


def test_cancelled_before_walk(tmp_path):
    cache(tmp_path)
    assert discover.find_uncovered_caches(1, [], [str(tmp_path)], lambda: True, excluded=[]).candidates == []


def test_disabled_spec_not_coverage(tmp_path, monkeypatch):
    path = cache(tmp_path)
    spec = catalog.ScannerSpec("disabled", "Disabled", [str(path)], disabled_reason="Unsafe")
    monkeypatch.setattr(catalog, "load_catalog", lambda: {spec.id: spec})
    rows = discover.find_uncovered_caches(1, roots=[str(tmp_path)], excluded=[])
    assert [row.path for row in rows.candidates] == [str(path)]


def test_scan_ui(qapp, monkeypatch):
    from modules.cleanup.tabs import _discover_tab as ui
    monkeypatch.setattr(ui, "find_uncovered_caches", lambda **kwargs: discover.DiscoverResult([
        discover.Candidate("small", 32, "Cache"),
        discover.Candidate("big", 128, "GPUCache")], 0))
    tab = ui._DiscoverTab()
    tab._scan_btn.click()
    deadline = time.monotonic() + 5
    while not tab._scan_btn.isEnabled() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert tab._table.rowCount() == 2
    assert tab._table.item(0, 1).text() == "big"
    assert "Found 2 folders" in tab._status.text()
    tab._cancel_all()
    tab.close()


def test_cancel_before_scan(qapp):
    from modules.cleanup.tabs._discover_tab import _DiscoverTab
    tab = _DiscoverTab()
    tab._cancel_all()
    tab.close()


def test_scan_failure_shown(qapp, monkeypatch):
    from modules.cleanup.tabs import _discover_tab as ui

    def fail(**kwargs):
        raise OSError("discovery refused")

    monkeypatch.setattr(ui, "find_uncovered_caches", fail)
    tab = ui._DiscoverTab()
    tab._scan_btn.click()
    deadline = time.monotonic() + 5
    while not tab._scan_btn.isEnabled() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert tab._status.text() == "Scan failed: discovery refused"
    tab.close()


def test_overlapping_roots_not_double_counted(tmp_path):
    path = cache(tmp_path)
    rows = discover.find_uncovered_caches(1, [], [str(tmp_path), str(path.parent)], excluded=[])
    assert len(rows.candidates) == 1


def test_depth_limit(tmp_path):
    cache(tmp_path, "/".join(["nested"] * 8 + ["Cache"]))
    assert discover.find_uncovered_caches(1, [], [str(tmp_path)], excluded=[]).candidates == []


def test_cache_beneath_excluded_app_root_reported(tmp_path):
    path = cache(tmp_path, "MathWorks/ServiceHost/logs")
    rows = discover.find_uncovered_caches(
        1, targets=[], roots=[str(tmp_path)], excluded=[str(tmp_path / "MathWorks")])
    assert [row.path for row in rows.candidates] == [str(path)]


def test_cache_equal_to_excluded_target_not_reported(tmp_path):
    path = cache(tmp_path)
    assert discover.find_uncovered_caches(
        1, targets=[], roots=[str(tmp_path)], excluded=[str(path)]).candidates == []


def test_cache_containing_excluded_target_not_reported(tmp_path):
    path = cache(tmp_path)
    reviewed = path / "reviewed"
    reviewed.mkdir()
    assert discover.find_uncovered_caches(
        1, targets=[], roots=[str(tmp_path)], excluded=[str(reviewed)]).candidates == []


@pytest.mark.parametrize("relative", ["Microsoft/Windows/WebCache", "microsoft/WINDOWS/webcache"])
def test_known_not_junk_webcache_excluded(tmp_path, relative):
    cache(tmp_path, relative)
    assert discover.find_uncovered_caches(
        1, targets=[], roots=[str(tmp_path)], excluded=[]).candidates == []


def test_disabled_spec_excluded_by_default(tmp_path, monkeypatch):
    path = cache(tmp_path, "OneNote/16.0/cache")
    spec = catalog.ScannerSpec("disabled", "Disabled", [str(path)], disabled_reason="Unsafe")
    monkeypatch.setattr(catalog, "load_catalog", lambda: {spec.id: spec})
    assert discover.find_uncovered_caches(1, roots=[str(tmp_path)]).candidates == []


def test_removed_onenote_catalog_path_still_excluded(tmp_path):
    cache(tmp_path, "Microsoft/OneNote/16.0/cache")
    assert discover.find_uncovered_caches(
        1, targets=[], roots=[str(tmp_path)], excluded=[]).candidates == []


def test_junctions_are_not_discovered_or_sized(tmp_path):
    target = cache(tmp_path, "App/Cache")
    outside = cache(tmp_path, "Outside/Cache", 128)
    links = [tmp_path / "Alias", tmp_path / "GPUCache", target / "nested"]
    try:
        for link, destination in zip(links, [target.parent, target, outside]):
            made = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(destination)],
                capture_output=True, text=True)
            if made.returncode:
                pytest.skip(f"Cannot create junction: {made.stdout} {made.stderr}")
        rows = discover.find_uncovered_caches(
            1, [], [str(tmp_path / "App"), str(links[0]), str(links[1])], excluded=[])
        assert [(row.path, row.size) for row in rows.candidates] == [(str(target), 32)]
        result = discover.find_uncovered_caches(1, [], [str(tmp_path)], excluded=[])
        assert [(row.path, row.size) for row in result.candidates] == [
            (str(outside), 128), (str(target), 32)]
        assert discover.size_of(links[1]) == 0
    finally:
        for link in links:
            if os.path.isjunction(link):
                os.rmdir(link)


@pytest.mark.parametrize("found", [False, True])
def test_refused_folder_count_and_status(tmp_path, monkeypatch, qapp, found):
    from modules.cleanup.tabs._discover_tab import _DiscoverTab
    target = cache(tmp_path)
    real_walk = os.walk

    def refused_walk(path, **kwargs):
        kwargs["onerror"](PermissionError(13, "Access denied", str(path)))
        if found:
            yield from real_walk(path, **kwargs)

    monkeypatch.setattr(discover.os, "walk", refused_walk)
    result = discover.find_uncovered_caches(1, [], [str(tmp_path)], excluded=[])
    assert result.unreadable == (2 if found else 1)
    assert [row.path for row in result.candidates] == ([str(target)] if found else [])
    tab = _DiscoverTab()
    try:
        tab._show_rows(result, 20)
        assert tab._status.text().endswith(
            f" {result.unreadable} protected folders were skipped (normal: mostly Windows' own).")
        if not found:
            assert tab._status.text() == ("Nothing uncovered above 20 MB. 1 protected folders were "
                                          "skipped (normal: mostly Windows' own).")
    finally:
        tab.close()
