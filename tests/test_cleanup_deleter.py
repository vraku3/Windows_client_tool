# tests/test_cleanup_deleter.py
import os
import stat
import subprocess
import time

import pytest

from modules.cleanup.cleanup_scanner import deleter as d


def _file(path, size=10, age_s=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


def test_contents_are_deleted_and_the_root_is_kept(tmp_path):
    _file(tmp_path / "a.tmp", 100)
    _file(tmp_path / "sub" / "b.tmp", 50)
    report = d.delete_path(str(tmp_path), True)
    assert tmp_path.exists() and list(tmp_path.iterdir()) == []
    assert (report.freed, report.files) == (150, 2)


def test_keep_root_false_removes_the_folder_itself(tmp_path):
    target = tmp_path / "app-1.2.3"
    _file(target / "x.dll", 7)
    report = d.delete_path(str(target), True, keep_root=False)
    assert not target.exists() and report.freed == 7


def test_a_locked_file_is_reported_not_freed(tmp_path):
    locked = _file(tmp_path / "in-use.log", 30)
    _file(tmp_path / "free.log", 20)
    # CRT open() shares read/write but not DELETE, so this handle blocks os.remove
    # with ERROR_SHARING_VIOLATION -- exactly what a running app's open log does.
    fd = os.open(str(locked), os.O_RDONLY)
    try:
        report = d.delete_path(str(tmp_path), True)
    finally:
        os.close(fd)
    assert report.freed == 20 and report.files == 1
    assert report.locked == [str(locked)]
    assert locked.exists()


def test_a_junction_is_unlinked_never_followed(tmp_path):
    outside = tmp_path / "outside"
    keep = _file(outside / "precious.txt", 99)
    cache = tmp_path / "cache"
    cache.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(cache / "link"), str(outside)],
                   check=True, capture_output=True)
    report = d.delete_path(str(cache), True)
    assert keep.exists()                          # the target is untouched
    assert not (cache / "link").exists()          # the junction itself is gone
    assert report.freed == 0


def test_a_path_over_260_chars_is_deleted(tmp_path):
    deep = tmp_path
    while len(str(deep)) < 300:
        deep = deep / ("d" * 40)
    os.makedirs(d.long_path(str(deep)))
    with open(d.long_path(str(deep / "f.bin")), "wb") as f:
        f.write(b"y" * 5)
    report = d.delete_path(str(tmp_path), True)
    assert report.freed == 5 and list(tmp_path.iterdir()) == []


def test_a_read_only_file_is_deleted(tmp_path):
    ro = _file(tmp_path / "ro.bin", 8)
    os.chmod(ro, stat.S_IREAD)
    report = d.delete_path(str(tmp_path), True)
    assert not ro.exists() and report.freed == 8


def test_files_newer_than_the_floor_are_kept(tmp_path):
    _file(tmp_path / "old.tmp", 10, age_s=3 * 86400)
    fresh = _file(tmp_path / "msi-extract" / "fresh.cab", 40)
    report = d.delete_path(str(tmp_path), True, keep_newer_than_s=24 * 3600)
    assert fresh.exists() and report.freed == 10 and report.kept_recent == 1


def test_cancel_stops_between_files(tmp_path):
    for i in range(5):
        _file(tmp_path / f"{i}.tmp", 1)
    calls = []
    report = d.delete_path(str(tmp_path), True, cancelled=lambda: calls.append(1) or len(calls) > 2)
    assert report.cancelled and report.files < 5


def test_a_single_file_item(tmp_path):
    f = _file(tmp_path / "memory.dmp", 64)
    report = d.delete_path(str(f), False)
    assert not f.exists() and report.freed == 64


def test_a_missing_path_is_nothing_not_an_error(tmp_path):
    report = d.delete_path(str(tmp_path / "gone"), True)
    assert (report.freed, report.files, report.locked, report.denied) == (0, 0, [], [])


def test_a_junction_root_is_unlinked_not_followed(tmp_path):
    outside = tmp_path / "outside"
    keep = _file(outside / "precious.txt", 99)
    root_link = tmp_path / "root_link"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(root_link), str(outside)],
                   check=True, capture_output=True)
    report = d.delete_path(str(root_link), True)
    assert keep.exists()
    assert report.freed == 0
