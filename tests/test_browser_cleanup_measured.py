"""Browser cleanup goes through the measured deleter, like every other Cleanup path.

Both browser paths used rmtree and credited the SCANNED size, so a cache held
open by a running browser was reported freed, and none of it reached History.
"""
import json
import os
from pathlib import Path

import pytest

from modules.cleanup import browser_scanner as bs
from modules.cleanup import cleanup_history as ch
from modules.cleanup.cleanup_scanner import scanners_system as ss


@pytest.fixture
def log_to(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])
    path = tmp_path / "deleted.json"
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(path))
    return path


def _cache(tmp_path, name, files):
    folder = tmp_path / "Default" / name
    folder.mkdir(parents=True)
    for fname, size in files.items():
        (folder / fname).write_bytes(b"x" * size)
    return folder


def test_entries_report_measured_bytes_not_the_scanned_size(tmp_path, log_to):
    cache = _cache(tmp_path, "Cache", {"data_0": 100, "in_use": 50})
    entry = bs.CacheEntry(label="HTTP Cache", path=cache, size_bytes=150)
    fd = os.open(str(cache / "in_use"), os.O_RDONLY)
    try:
        freed, errors = bs.delete_cache_entries([entry])
    finally:
        os.close(fd)
    assert (freed, errors) == (100, 1)
    assert cache.is_dir() and (cache / "in_use").exists()        # the cache root stays
    logged = json.loads(log_to.read_text())[-1]
    assert logged["bytes"] == 100 and logged["locked_count"] == 1


def test_categories_report_measured_bytes_and_reach_history(tmp_path, log_to):
    cache = _cache(tmp_path, "Code Cache", {"js": 70})
    cat = bs.CacheCategory(label="Compiled JS/WASM", path=cache, size_bytes=999)
    seen = []
    freed, errors = bs.delete_selected([cat], progress_cb=seen.append)
    assert (freed, errors) == (70, 0) and seen == [1]
    assert json.loads(log_to.read_text())[-1]["bytes"] == 70


def test_a_missing_cache_is_nothing_not_an_error(tmp_path, log_to):
    entry = bs.CacheEntry(label="HTTP Cache", path=Path(tmp_path / "gone"), size_bytes=10)
    assert bs.delete_cache_entries([entry]) == (0, 0)
