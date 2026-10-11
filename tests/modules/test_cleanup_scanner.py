import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))

from modules.cleanup.cleanup_scanner import (
    ScanItem, ScanResult, get_dir_size, format_size, delete_items
)


def test_get_dir_size_empty(tmp_path):
    assert get_dir_size(str(tmp_path)) == 0


def test_get_dir_size_with_files(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    (tmp_path / "b.txt").write_bytes(b"y" * 200)
    assert get_dir_size(str(tmp_path)) == 300


def test_format_size_bytes():
    assert format_size(500) == "500 B"   # whole bytes carry no decimal:
    # the five formatters disagreed here (0.0 B vs 0 B) until they were
    # consolidated on core.formatting (audit #18).


def test_format_size_kb():
    assert format_size(2048) == "2.0 KB"


def test_format_size_mb():
    assert format_size(1024 * 1024) == "1.0 MB"


def test_scan_result_selected_size():
    result = ScanResult(items=[
        ScanItem("/a", 100, False, selected=True),
        ScanItem("/b", 200, False, selected=False),
        ScanItem("/c", 300, True, selected=True),
    ])
    result.total_size = 600
    assert result.selected_size() == 400


def test_delete_items_removes_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"hello")
    item = ScanItem(str(f), 5, False, selected=True)
    deleted, errors = delete_items([item])
    assert deleted == 1
    assert errors == 0
    assert not f.exists()


def test_delete_items_removes_dir(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "file.txt").write_bytes(b"data")
    item = ScanItem(str(d), 4, True, selected=True, keep_root=False)
    deleted, errors = delete_items([item])
    assert deleted == 1
    assert errors == 0
    assert not d.exists()


def test_delete_items_skips_unselected(tmp_path):
    f = tmp_path / "keep.txt"
    f.write_bytes(b"keep")
    item = ScanItem(str(f), 4, False, selected=False)
    deleted, errors = delete_items([item])
    assert deleted == 0
    assert f.exists()


def test_delete_items_handles_missing_file():
    item = ScanItem("/nonexistent/path/doesnotexist.txt", 0, False, selected=True)
    deleted, errors = delete_items([item])
    assert errors == 0  # missing files silently skipped
