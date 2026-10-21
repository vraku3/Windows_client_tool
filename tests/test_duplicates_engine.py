import os
import threading

import pytest

from modules.cleanup.duplicates.enumerate import ScanOptions
from modules.cleanup.duplicates.finder import find_duplicates, hash_file, ScanCancelled
from modules.cleanup.duplicates.hash_cache import HashCache
from modules.cleanup.duplicates.models import FileEntry, select_files


def scan(root, method="exact", **kwargs):
    return find_duplicates(ScanOptions(roots=[str(root)], min_size=1,
                                      method=method, use_cache=False,
                                      skip_system_folders=False, **kwargs))


def test_exact_and_same_size_different_content(tmp_path):
    (tmp_path / "a").write_bytes(b"abc")
    (tmp_path / "b").write_bytes(b"abc")
    (tmp_path / "c").write_bytes(b"xyz")
    result = scan(tmp_path)
    assert len(result.groups) == 1
    assert len(result.groups[0].files) == 2
    assert result.groups[0].reclaimable == 3


def test_samples_are_only_probable(tmp_path):
    content = bytearray(b"a" * (1024 * 1024))
    (tmp_path / "a").write_bytes(content)
    content[250_000] = ord("b")
    (tmp_path / "b").write_bytes(content)
    assert len(scan(tmp_path, "probable").groups) == 1
    assert scan(tmp_path, "probable").groups[0].confidence == "probable"
    assert not scan(tmp_path).groups
    assert not scan(tmp_path, "verified").groups


def test_hardlinks_are_one_physical_file(tmp_path):
    a = tmp_path / "a"
    a.write_bytes(b"abc")
    os.link(a, tmp_path / "b")
    result = scan(tmp_path)
    assert not result.groups
    assert result.hardlinks == 1


@pytest.mark.parametrize("method,count", [("name_size", 1), ("name", 1), ("size", 1)])
def test_name_and_size_methods(tmp_path, method, count):
    for name in ("x", "y"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "same.txt").write_bytes(b"abc" if name == "x" else b"xyz")
    groups = scan(tmp_path, method).groups
    assert len(groups) == count
    assert groups[0].confidence == "candidate"
    if method == "size":
        assert not select_files(groups, "newest")


def test_never_select_whole_group(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / name).write_bytes(b"abc")
    groups = scan(tmp_path).groups
    assert len(select_files(groups, "newest")) == 2
    assert len(select_files(groups, "oldest")) == 2


def test_cancel_during_long_hash(tmp_path):
    path = tmp_path / "large"
    path.write_bytes(b"x" * (5 * 1024 * 1024))
    stop = threading.Event()
    def progress(count):
        stop.set()
    with pytest.raises(ScanCancelled):
        hash_file(FileEntry.from_path(str(path)), cancelled=stop.is_set, on_bytes=progress)


def test_cache_hit_and_mtime_invalidation(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"abc")
    with HashCache(str(tmp_path / "hashes.sqlite")) as cache:
        entry = FileEntry.from_path(str(path))
        cache.put(entry, "full", "digest")
        assert cache.get(entry, "full") == "digest"
        os.utime(path, ns=(entry.mtime_ns + 1000000000, entry.mtime_ns + 1000000000))
        assert cache.get(FileEntry.from_path(str(path)), "full") is None
        cache.clear()
        assert cache.get(entry, "full") is None


def test_dhash_resized_copies(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    image = Image.new("RGB", (128, 128))
    image.putdata([(x * 2, y * 2, (x + y) % 256) for y in range(128) for x in range(128)])
    image.save(tmp_path / "a.png")
    image.resize((256, 256)).save(tmp_path / "b.png")
    other = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    other.save(tmp_path / "c.png")
    groups = scan(tmp_path, "images", image_threshold=3).groups
    assert len(groups) == 1
    assert {os.path.basename(f.path) for f in groups[0].files} == {"a.png", "b.png"}
    assert not select_files(groups, "oldest")


def test_cloud_placeholder_is_never_opened(tmp_path, monkeypatch):
    for name in ("a", "b"):
        (tmp_path / name).write_bytes(b"abc")
    monkeypatch.setattr("modules.cleanup.duplicates.finder.known_folders.is_cloud_placeholder", lambda p: True)
    monkeypatch.setattr("builtins.open", lambda *a, **kw: pytest.fail("Placeholder was opened"))
    assert not scan(tmp_path).groups


def test_filters_and_excluded_folders(tmp_path):
    for name in ("a.jpg", "b.jpg", "c.txt", "d.txt", "zero.jpg"):
        (tmp_path / name).write_bytes(b"" if name.startswith("zero") else b"abc")
    assert len(scan(tmp_path, include_extensions=("jpg",)).groups) == 1
    assert not scan(tmp_path, exclude_folders=(str(tmp_path),)).groups
    assert len(scan(tmp_path, exclude_extensions=("jpg",)).groups) == 1


def test_real_rescan_uses_cache_and_does_not_open_data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    for name in ("a", "b"):
        (root / name).write_bytes(b"abc")
    options = ScanOptions(roots=[str(root)], min_size=1, skip_system_folders=False,
                          cache_path=str(tmp_path / "hashes.sqlite"))
    assert find_duplicates(options).groups
    monkeypatch.setattr("builtins.open", lambda *a, **kw: pytest.fail("Cache hit read file data"))
    second = find_duplicates(options)
    assert second.cache_hits == 4
    assert second.bytes_hashed == 0


def test_hardlink_outside_scan_not_reclaimable(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "a").write_bytes(b"abc")
    os.link(root / "a", tmp_path / "outside")
    (root / "b").write_bytes(b"abc")
    groups = scan(root).groups
    assert groups[0].reclaimable == 3


def test_overlapping_roots_do_not_repeat_files(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    (child / "a").write_bytes(b"abc")
    (child / "b").write_bytes(b"abc")
    result = find_duplicates(ScanOptions(roots=[str(child), str(tmp_path)], min_size=1,
                                        use_cache=False, skip_system_folders=False))
    assert result.listed == 2
    assert result.hardlinks == 0


def test_corrupt_cache_falls_back_to_hashing(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    for name in ("a", "b"):
        (root / name).write_bytes(b"abc")
    bad_cache = tmp_path / "cache.sqlite"
    bad_cache.write_bytes(b"not a database")
    result = find_duplicates(ScanOptions(roots=[str(root)], min_size=1, skip_system_folders=False,
                                        cache_path=str(bad_cache)))
    assert result.groups
    assert result.errors and result.errors[0][0] == "Hash cache"


def test_pillow_bomb_is_a_per_file_issue(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    (tmp_path / "bomb.png").write_bytes(b"not decoded")
    def bomb(*args, **kwargs):
        raise Image.DecompressionBombError("too large")
    monkeypatch.setattr(Image, "open", bomb)
    result = scan(tmp_path, "images")
    assert not result.groups
    assert len(result.errors) == 1


def test_name_only_allows_different_sizes_but_name_size_does_not(tmp_path):
    for name, content in (("one", b"abc"), ("two", b"abcdef")):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "same.txt").write_bytes(content)
    assert len(scan(tmp_path, "name").groups) == 1
    assert not scan(tmp_path, "name_size").groups


def test_definitive_verification_reads_every_group_and_rejects_digest_collision(tmp_path, monkeypatch):
    for name, content in (("a", b"abc"), ("b", b"abc"), ("c", b"1234"), ("d", b"1234")):
        (tmp_path / name).write_bytes(content)
    result = scan(tmp_path, "verified")
    assert len(result.groups) == 2
    assert result.stage_bytes["verification"] == 14
    (tmp_path / "b").write_bytes(b"xyz")
    monkeypatch.setattr("modules.cleanup.duplicates.finder.hash_file", lambda *a, **kw: "fake-collision")
    verified = scan(tmp_path, "verified")
    assert len(verified.groups) == 1
    assert {os.path.basename(f.path) for f in verified.groups[0].files} == {"c", "d"}
