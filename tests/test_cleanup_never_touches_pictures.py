"""User-reported safety concern (2026-09-21): make sure Cleanup never
deletes photos. scan_large_files/scan_duplicate_files/scan_old_files walk
known_folders.user_data_dirs(), which includes Pictures and Videos (both
routinely OneDrive-redirected) -- none of "large", "duplicate", or "old"
can tell a real photo/home video apart from actual junk, since a real
photo library is exactly large, duplicated (phone backup + export) and
old (untouched since the day it was taken) by definition. These three
scanners must never look in Pictures or Videos at all.
"""
from modules.cleanup.cleanup_scanner import known_folders as kf
from modules.cleanup.cleanup_scanner import scanners_system as ss


def test_file_sweep_safe_folders_excludes_pictures_and_videos():
    assert "Pictures" not in kf.FILE_SWEEP_SAFE_FOLDERS
    assert "Videos" not in kf.FILE_SWEEP_SAFE_FOLDERS
    # Still exactly the other four -- nothing else was dropped by accident.
    assert set(kf.FILE_SWEEP_SAFE_FOLDERS) == {
        "Downloads", "Documents", "Desktop", "Music"}


def test_scan_large_files_never_looks_in_pictures_or_videos(monkeypatch, tmp_path):
    pictures = tmp_path / "Pictures"
    pictures.mkdir()
    (pictures / "real_photo.jpg").write_bytes(b"x" * (200 * 1024 * 1024))  # 200 MB

    monkeypatch.setattr(kf, "known_folder",
                        lambda name: str(pictures) if name == "Pictures" else None)
    monkeypatch.setattr(ss.known_folders, "known_folder", ss.known_folders.known_folder)

    result = ss.scan_large_files(min_size_mb=1)

    assert not any("real_photo.jpg" in item.path for item in result.items), (
        "scan_large_files found a file inside Pictures -- it must never look there")


def test_scan_old_files_never_looks_in_pictures_or_videos(monkeypatch, tmp_path):
    import os
    import time

    videos = tmp_path / "Videos"
    videos.mkdir()
    clip = videos / "home_video.mp4"
    clip.write_bytes(b"x" * 1024)
    two_years_ago = time.time() - 730 * 86400
    os.utime(clip, (two_years_ago, two_years_ago))

    monkeypatch.setattr(kf, "known_folder",
                        lambda name: str(videos) if name == "Videos" else None)
    monkeypatch.setattr(ss.known_folders, "known_folder", ss.known_folders.known_folder)

    result = ss.scan_old_files(min_age_months=1)

    assert not any("home_video.mp4" in item.path for item in result.items), (
        "scan_old_files found a file inside Videos -- it must never look there")


def test_scan_duplicate_files_never_looks_in_pictures_or_videos(monkeypatch, tmp_path):
    pictures = tmp_path / "Pictures"
    pictures.mkdir()
    payload = b"d" * (200 * 1024)
    (pictures / "photo_copy_one.jpg").write_bytes(payload)
    (pictures / "photo_copy_two.jpg").write_bytes(payload)

    monkeypatch.setattr(kf, "known_folder",
                        lambda name: str(pictures) if name == "Pictures" else None)
    monkeypatch.setattr(ss.known_folders, "known_folder", ss.known_folders.known_folder)

    result = ss.scan_duplicate_files(min_size_kb=1)

    assert not any("photo_copy" in item.path for item in result.items), (
        "scan_duplicate_files found files inside Pictures -- it must never look there")
