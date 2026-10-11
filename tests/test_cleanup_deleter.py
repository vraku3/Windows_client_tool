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
    assert not os.path.lexists(root_link)
    assert report.freed == 0


def test_a_dir_swapped_for_a_reparse_point_after_scandir_is_not_entered(tmp_path, monkeypatch):
    victim = tmp_path / "victim"
    keep = _file(victim / "keep.txt", 5)
    real = os.lstat

    class _Fake:
        st_mode = stat.S_IFDIR
        st_file_attributes = 0x410
        st_size = 0
        st_mtime = 0

    def fake(p, *a, **k):
        return _Fake() if str(p).endswith("victim") else real(p, *a, **k)

    monkeypatch.setattr(d.os, "lstat", fake)
    report = d.delete_path(str(tmp_path), True)
    assert keep.exists() and report.freed == 0


def test_already_cancelled_deletes_nothing_for_any_item_kind(tmp_path):
    f = _file(tmp_path / "a.tmp", 5)
    r1 = d.delete_path(str(f), False, cancelled=lambda: True)
    r2 = d.delete_path(str(tmp_path), True, cancelled=lambda: True)
    assert f.exists() and r1.cancelled and r2.cancelled and r1.freed == r2.freed == 0


def test_a_single_file_newer_than_the_floor_is_kept(tmp_path):
    f = _file(tmp_path / "new.cab", 5)
    r = d.delete_path(str(f), False, keep_newer_than_s=3600)
    assert f.exists() and r.kept_recent == 1 and r.freed == 0


def test_an_unreadable_root_is_denied_not_silent(tmp_path, monkeypatch):
    def boom(p, *a, **k):
        raise PermissionError(5, "denied")
    monkeypatch.setattr(d.os, "lstat", boom)
    r = d.delete_path(str(tmp_path), True)
    assert r.denied and r.freed == 0


def test_an_rmdir_refusal_other_than_not_empty_is_denied(tmp_path, monkeypatch):
    sub = tmp_path / "sub"
    sub.mkdir()

    def boom(p, *a, **k):
        e = PermissionError(5, "denied")
        e.winerror = 5
        raise e
    monkeypatch.setattr(d.os, "rmdir", boom)
    r = d.delete_path(str(tmp_path), True)
    assert r.denied == [str(sub)]


def test_a_not_empty_rmdir_is_the_expected_outcome(tmp_path, monkeypatch):
    sub = tmp_path / "sub"
    sub.mkdir()

    def boom(p, *a, **k):
        e = OSError(41, "not empty")
        e.winerror = 145
        raise e
    monkeypatch.setattr(d.os, "rmdir", boom)
    assert d.delete_path(str(tmp_path), True).denied == []


def test_a_directory_symlink_is_removed_not_followed(tmp_path):
    outside = tmp_path / "outside"
    keep = _file(outside / "precious.txt", 9)
    cache = tmp_path / "cache"
    cache.mkdir()
    res = subprocess.run(["cmd", "/c", "mklink", "/D", str(cache / "sl"), str(outside)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        pytest.skip("symlink privilege not held: " + (res.stderr or res.stdout).strip())
    report = d.delete_path(str(cache), True)
    assert keep.exists() and not os.path.lexists(cache / "sl")
    assert report.freed == 0 and report.denied == []


from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss


@pytest.fixture
def no_system_guards(monkeypatch):
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])


def test_delete_items_counts_only_what_went(tmp_path, no_system_guards):
    _file(tmp_path / "a.tmp", 100, age_s=3 * 86400)
    held = _file(tmp_path / "held.tmp", 50, age_s=3 * 86400)
    fd = os.open(str(held), os.O_RDONLY)
    try:
        report, refused, _per = ss.delete_items_report([ScanItem(str(tmp_path), 150, True)])
    finally:
        os.close(fd)
    assert report.freed == 100 and report.locked == [str(held)] and refused == []
    assert tmp_path.exists()


def test_an_unreadable_subfolder_is_skipped_not_the_whole_item(tmp_path, no_system_guards, monkeypatch):
    _file(tmp_path / "junk.tmp", 10, age_s=3 * 86400)
    real_scandir = os.scandir
    blocked = tmp_path / "other-account"
    _file(blocked / "x.tmp", 99, age_s=3 * 86400)

    def scandir(path):
        if os.path.normcase(d._short(os.fspath(path))) == os.path.normcase(str(blocked)):
            raise PermissionError(5, "Access is denied", str(blocked))
        return real_scandir(path)
    monkeypatch.setattr(d.os, "scandir", scandir)
    monkeypatch.setattr(ss.os, "walk", lambda p, onerror=None: iter([(p, [], ["junk.tmp"])]))
    report, refused, _per = ss.delete_items_report([ScanItem(str(tmp_path), 109, True)])
    assert report.freed == 10 and str(blocked) in report.denied and refused == []
    assert (blocked / "x.tmp").exists()


def test_temp_keeps_files_younger_than_its_floor(tmp_path, no_system_guards):
    _file(tmp_path / "old.tmp", 10, age_s=3 * 86400)
    fresh = _file(tmp_path / "{GUID}" / "setup.msi", 40)
    item = ScanItem(str(tmp_path), 50, True, keep_newer_than_hours=24)
    report, _, _per = ss.delete_items_report([item])
    assert fresh.exists() and report.freed == 10 and report.kept_recent == 1


def test_a_superseded_version_folder_goes_entirely(tmp_path, no_system_guards):
    old = tmp_path / "app-1.0.0"
    _file(old / "app.exe", 5)
    report, _, _per = ss.delete_items_report([ScanItem(str(old), 5, True, keep_root=False)])
    assert not old.exists() and report.freed == 5


def test_the_catalog_temp_entry_carries_the_floor_and_crash_dumps_no_longer_target_temp():
    from modules.cleanup.cleanup_scanner import catalog
    specs = catalog.load_catalog()
    assert specs["temp_files"].keep_newer_than_hours == 24
    assert not any(p.lower().rstrip("\\").endswith(r"%localappdata%\temp")
                   for p in specs["user_crash_dumps"].paths)


def _locked(path):
    return os.open(str(path), os.O_RDONLY)


def test_per_item_counts_two_locked_items(tmp_path, no_system_guards):
    a = _file(tmp_path / "a" / "x.tmp", 5, age_s=3 * 86400)
    b = _file(tmp_path / "b" / "y.tmp", 5, age_s=3 * 86400)
    fds = [_locked(a), _locked(b)]
    try:
        got = ss.delete_items([ScanItem(str(tmp_path / "a"), 5, True), ScanItem(str(tmp_path / "b"), 5, True)])
    finally:
        for fd in fds:
            os.close(fd)
    assert got == (0, 2)


def test_per_item_counts_one_clean_one_locked(tmp_path, no_system_guards):
    _file(tmp_path / "a" / "x.tmp", 5, age_s=3 * 86400)
    b = _file(tmp_path / "b" / "y.tmp", 5, age_s=3 * 86400)
    fd = _locked(b)
    try:
        got = ss.delete_items([ScanItem(str(tmp_path / "a"), 5, True), ScanItem(str(tmp_path / "b"), 5, True)])
    finally:
        os.close(fd)
    assert got == (1, 1)


def test_an_item_with_only_fresh_files_is_cleaned_not_a_problem(tmp_path, no_system_guards):
    _file(tmp_path / "new.tmp", 5)
    item = ScanItem(str(tmp_path), 5, True, keep_newer_than_hours=24)
    report, _, _per = ss.delete_items_report([item])
    assert report.kept_recent == 1
    assert ss.delete_items([item]) == (1, 0)


def test_a_skip_predicate_keeps_the_named_file_and_lists_it(tmp_path):
    keep = _file(tmp_path / "mail.ost", 5)
    _file(tmp_path / "junk.tmp", 7)
    r = d.delete_path(str(tmp_path), True, skip=lambda p, is_dir: p.endswith("mail.ost"))
    assert keep.exists() and not (tmp_path / "junk.tmp").exists()
    assert r.denied == [str(keep)] and r.freed == 7


def test_the_deleting_walk_enforces_what_the_checking_walk_could_not_see(tmp_path, no_system_guards, monkeypatch):
    monkeypatch.setattr(ss, "held_user_data", lambda *a, **k: (False, False))
    ldb = _file(tmp_path / "IndexedDB" / "x.ldb", 5)
    ost = _file(tmp_path / "mail.ost", 5)
    junk = _file(tmp_path / "junk.tmp", 5)
    report, refused, _per = ss.delete_items_report([ScanItem(str(tmp_path), 15, True)])
    assert refused == [] and not junk.exists()
    assert ldb.exists() and ost.exists()
    assert str(ost) in report.denied and str(tmp_path / "IndexedDB") in report.denied


def test_a_protected_junction_is_kept_and_listed(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    tree = tmp_path / "tree"
    tree.mkdir()
    link = tree / "IndexedDB"
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    assert out.returncode == 0, out.stderr
    r = d.delete_path(str(tree), True, skip=lambda p, is_dir: os.path.basename(p) == "IndexedDB" and is_dir)
    assert os.path.lexists(link) and r.denied == [str(link)]


def test_a_skipped_root_directory_is_untouched_and_listed(tmp_path):
    f = _file(tmp_path / "root" / "a.tmp", 5)
    r = d.delete_path(str(tmp_path / "root"), True, skip=lambda p, is_dir: True)
    assert f.exists() and r.denied == [str(tmp_path / "root")] and r.freed == 0


def test_a_file_root_named_cookies_is_kept(tmp_path, no_system_guards, monkeypatch):
    f = _file(tmp_path / "Cookies", 5)
    r = d.delete_path(str(f), False, skip=ss._protected_entry)
    assert f.exists() and r.denied == [str(f)]


def test_a_junction_root_is_typed_by_lstat_not_by_the_callers_flag(tmp_path):
    target = tmp_path / "elsewhere"
    _file(target / "keep.txt", 5)
    link = tmp_path / "IndexedDB"
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    assert out.returncode == 0, out.stderr
    r = d.delete_path(str(link), False,
                      skip=lambda p, is_dir: is_dir and os.path.basename(p).lower() == "indexeddb")
    assert os.path.lexists(link) and r.denied == [str(link)]
    assert (target / "keep.txt").exists()


from modules.cleanup.cleanup_scanner import _common


def test_size_skips_links_and_matches_what_a_clean_frees(tmp_path):
    outside = tmp_path / "outside"
    _file(outside / "big.bin", 1000)
    cache = tmp_path / "cache"
    _file(cache / "a.bin", 30)
    _file(cache / "sub" / "b.bin", 12)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(cache / "link"), str(outside)],
                   check=True, capture_output=True)
    measured = _common.get_dir_size(str(cache))
    assert measured == 42
    assert d.delete_path(str(cache), True).freed == measured


def test_size_skips_cloud_placeholders(tmp_path, monkeypatch):
    _file(tmp_path / "local.bin", 10)
    _file(tmp_path / "cloud.bin", 5000)
    real = _common.os.scandir

    class Entry:
        def __init__(self, e):
            self._e, self.name, self.path = e, e.name, e.path

        def is_dir(self, follow_symlinks=True):
            return self._e.is_dir(follow_symlinks=follow_symlinks)

        def is_symlink(self):
            return False

        def is_junction(self):
            return False

        def stat(self, follow_symlinks=True):
            st = self._e.stat(follow_symlinks=follow_symlinks)
            if self.name == "cloud.bin":
                return os.stat_result((st.st_mode, 0, 0, 0, 0, 0, st.st_size, 0, 0, 0)), 0x400000
            return st, 0

    def scandir(path):
        return [Entry(e) for e in real(path)]
    monkeypatch.setattr(_common, "_attrs", lambda st_and_attr: st_and_attr[1])
    monkeypatch.setattr(_common, "_stat", lambda entry: entry.stat(follow_symlinks=False))
    monkeypatch.setattr(_common.os, "scandir", scandir)
    assert _common.get_dir_size(str(tmp_path)) == 10


# ---- fix round 1: one link/placeholder rule for size and delete -------------

class _FakeStat:
    def __init__(self, st, attrs):
        self._st, self.st_file_attributes = st, attrs

    def __getattr__(self, name):
        return getattr(self._st, name)


def _flag_names(monkeypatch, flags):
    """Entries with these names report a faked st_file_attributes word, through
    the production extraction (no patching of the extractor)."""
    real = os.scandir

    class Entry:
        def __init__(self, e):
            self._e, self.name, self.path = e, e.name, e.path

        def is_dir(self, follow_symlinks=True):
            return self._e.is_dir(follow_symlinks=follow_symlinks)

        def is_symlink(self):
            return self._e.is_symlink()

        def is_junction(self):
            return self._e.is_junction()

        def stat(self, follow_symlinks=True):
            st = self._e.stat(follow_symlinks=follow_symlinks)
            if self.name in flags:
                return _FakeStat(st, st.st_file_attributes | flags[self.name])
            return st

    monkeypatch.setattr(os, "scandir", lambda p: [Entry(e) for e in real(p)])


@pytest.mark.parametrize("flag", [0x400000, 0x40000, 0x1000])
def test_each_placeholder_flag_is_cloud_only_for_size_and_delete(tmp_path, monkeypatch, flag):
    _file(tmp_path / "local.bin", 10)
    _file(tmp_path / "cloud.bin", 5000)
    _flag_names(monkeypatch, {"cloud.bin": flag})
    assert _common.get_dir_size(str(tmp_path)) == 10
    report = d.delete_path(str(tmp_path), True)
    assert (report.freed, report.files, report.cloud_only) == (10, 1, 1)
    assert (tmp_path / "cloud.bin").exists()


def test_a_junction_root_sizes_zero_and_frees_zero(tmp_path):
    target = tmp_path / "target"
    _file(target / "f.bin", 500)
    root = tmp_path / "root"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(root), str(target)],
                   check=True, capture_output=True)
    assert _common.get_dir_size(str(root)) == 0
    assert d.delete_path(str(root), True).freed == 0
    assert (target / "f.bin").exists()


def test_a_flagged_directory_is_never_enumerated(tmp_path, monkeypatch):
    _file(tmp_path / "online" / "x.bin", 99)
    _file(tmp_path / "keep.bin", 4)
    _flag_names(monkeypatch, {"online": 0x400000})
    seen = []
    inner = os.scandir
    monkeypatch.setattr(os, "scandir", lambda p: (seen.append(os.path.basename(p)), inner(p))[1])
    assert _common.get_dir_size(str(tmp_path)) == 4
    d.delete_path(str(tmp_path), True)
    assert "online" not in seen
    assert (tmp_path / "online" / "x.bin").exists()


def test_size_and_delete_agree_on_a_mixed_tree(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    _file(outside / "big.bin", 1000)
    cache = tmp_path / "cache"
    _file(cache / "normal.bin", 21)
    _file(cache / "sub" / "n2.bin", 4)
    _file(cache / "cloud.bin", 7000)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(cache / "link"), str(outside)],
                   check=True, capture_output=True)
    _flag_names(monkeypatch, {"cloud.bin": 0x1000})
    measured = _common.get_dir_size(str(cache))
    report = d.delete_path(str(cache), True)
    assert measured == 25 == report.freed
    assert report.cloud_only == 1 and (outside / "big.bin").exists()


def test_deletereport_merges_cloud_only():
    a, b = d.DeleteReport(cloud_only=1), d.DeleteReport(cloud_only=2)
    a.merge(b)
    assert a.cloud_only == 3


def test_a_root_reparse_file_is_an_ordinary_file(tmp_path, monkeypatch):
    f = _file(tmp_path / "hydrated.bin", 77, age_s=7200)
    real = os.lstat

    def lstat(p, *a, **k):
        st = real(p, *a, **k)
        return _FakeStat(st, st.st_file_attributes | 0x400) if str(p).endswith("hydrated.bin") else st
    monkeypatch.setattr(os, "lstat", lstat)
    kept = d.delete_path(str(f), False, keep_newer_than_s=86400)
    assert (kept.kept_recent, kept.freed) == (1, 0) and f.exists()
    gone = d.delete_path(str(f), False, keep_newer_than_s=3600)
    assert (gone.freed, gone.files) == (77, 1) and not f.exists()
