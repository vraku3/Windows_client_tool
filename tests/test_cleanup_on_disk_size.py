"""Cleanup sizes and freed bytes are what a file OCCUPIES, not its logical length.

Measured 2026-10-21: Free up space offered "Temporary files: 235 GB" where
~7 GB was reclaimable -- leftover sparse 2 GB test VM disks summed 232 GB
logical, 4 GB on disk.
"""
import ctypes
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from _sparse import make_sparse
from modules.cleanup.cleanup_scanner import disk_size
from modules.cleanup.cleanup_scanner._common import _make_item, get_dir_size
from modules.cleanup.cleanup_scanner.deleter import delete_path
from modules.cleanup.cleanup_scanner.disk_size import on_disk_size
from modules.cleanup.duplicates.models import DuplicateGroup, FileEntry

TWO_GB = 2 * 1024 ** 3
SMALL = 1024 * 1024          # a sparse 2 GB file allocates ~4-64 KB; anything under 1 MB proves it


def _sparse_file(path):
    make_sparse(path, TWO_GB)
    st = os.lstat(path)
    if not st.st_file_attributes & disk_size.FILE_ATTRIBUTE_SPARSE_FILE:
        pytest.skip("this volume does not support sparse files")
    return st


def test_sparse_file_counts_its_allocation_not_its_length(tmp_path):
    path = tmp_path / "disk.vhdx"
    st = _sparse_file(path)
    assert st.st_size == TWO_GB
    assert on_disk_size(str(path), st) < SMALL


def test_ordinary_file_is_st_size_with_no_extra_syscall(tmp_path, monkeypatch):
    path = tmp_path / "plain.bin"
    path.write_bytes(b"x" * 10_000)

    def boom(*args):
        raise AssertionError("an ordinary file must not cost a GetCompressedFileSizeW call")

    monkeypatch.setattr(disk_size, "_get_compressed_size", boom)
    assert on_disk_size(str(path), os.lstat(path)) == 10_000


def test_failed_query_falls_back_to_st_size(tmp_path, monkeypatch):
    path = tmp_path / "disk.vhdx"
    st = _sparse_file(path)

    def refused(_path, _high):
        ctypes.set_last_error(5)
        return disk_size.INVALID_FILE_SIZE

    monkeypatch.setattr(disk_size, "_get_compressed_size", refused)
    assert on_disk_size(str(path), st) == TWO_GB


def test_get_dir_size_and_make_item_use_on_disk_size(tmp_path):
    folder = tmp_path / "Temp"
    folder.mkdir()
    _sparse_file(folder / "leftover.vhdx")
    (folder / "plain.bin").write_bytes(b"x" * 10_000)

    total = get_dir_size(str(folder))
    assert 10_000 <= total < 10_000 + SMALL
    assert _make_item(str(folder / "leftover.vhdx")).size < SMALL
    assert _make_item(str(folder / "plain.bin")).size == 10_000


def test_deleter_reports_what_was_freed_on_disk(tmp_path):
    folder = tmp_path / "Temp"
    folder.mkdir()
    _sparse_file(folder / "leftover.vhdx")
    (folder / "plain.bin").write_bytes(b"x" * 10_000)

    report = delete_path(str(folder), True)
    assert report.files == 2
    assert 10_000 <= report.freed < 10_000 + SMALL
    assert not list(folder.iterdir())

    single = tmp_path / "one.vhdx"
    _sparse_file(single)
    assert delete_path(str(single), False).freed < SMALL


def test_duplicates_reclaimable_counts_on_disk(tmp_path):
    a, b = tmp_path / "a.vhdx", tmp_path / "b.vhdx"
    _sparse_file(a)
    _sparse_file(b)
    entries = [FileEntry.from_path(str(a)), FileEntry.from_path(str(b))]
    assert all(e.size == TWO_GB for e in entries)          # content comparison keeps logical
    group = DuplicateGroup(0, entries, "full", "exact")
    assert group.reclaimable < SMALL
    # An entry built without a measurement falls back to its logical size.
    assert FileEntry(str(a), 3, 1, 1, 1, 1).on_disk == 3


def test_a_sparse_disk_image_is_not_a_large_virtual_disk(tmp_path, monkeypatch):
    """2 GB logical, ~nothing on disk: deleting it frees nothing worth listing."""
    from modules.cleanup.cleanup_scanner import scanners_system as ss
    _sparse_file(tmp_path / "leftover.vhdx")
    monkeypatch.setattr(ss.drives, "fixed_drive_roots", lambda: [str(tmp_path)])
    assert ss._collect_virtual_disks() == []
