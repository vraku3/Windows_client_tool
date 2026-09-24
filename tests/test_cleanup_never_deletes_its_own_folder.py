r"""Cleanup must never remove the folder the running app lives in.

User concern (2026-09-21): "i noticed it removed some stuff from ...\Aplicatii
\WinClientTool, is this ok?" Running every one of the 522 scanners against the
real machine (2026-09-24) found NO item in that folder, so there was no code
path to fix -- this guard is the last line of defence: `delete_items` refuses
anything inside the frozen exe's own folder or its PyInstaller runtime dir,
whatever produced the item.
"""
import os
import sys

from modules.cleanup.cleanup_scanner import scanners_system as ss
from modules.cleanup.cleanup_scanner._common import ScanItem


def _frozen(monkeypatch, exe_dir, bundle=None):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "WinClientTool-Portable.exe"))
    if bundle is not None:
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)


def test_nothing_is_protected_when_running_from_source(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert ss.protected_app_dirs() == []


def test_the_exe_folder_and_runtime_dir_are_protected_when_frozen(monkeypatch, tmp_path):
    exe_dir, bundle = tmp_path / "Apps", tmp_path / "_MEI123"
    _frozen(monkeypatch, exe_dir, bundle)
    assert [os.path.normcase(p) for p in ss.protected_app_dirs()] == [
        os.path.normcase(str(exe_dir)), os.path.normcase(str(bundle))]


def test_a_file_in_the_exe_folder_is_refused_not_deleted(monkeypatch, tmp_path):
    exe_dir = tmp_path / "Apps"
    (exe_dir / "WinClientTool").mkdir(parents=True)
    victim = exe_dir / "WinClientTool" / "STRIX_old.log"
    victim.write_text("keep me")
    _frozen(monkeypatch, exe_dir)

    deleted, errors = ss.delete_items(
        [ScanItem(path=str(victim), size=7, is_dir=False, selected=True, safety="safe")])
    assert (deleted, errors) == (0, 1)
    assert victim.exists()


def test_a_whole_subfolder_of_the_exe_folder_is_refused(monkeypatch, tmp_path):
    exe_dir = tmp_path / "Apps"
    folder = exe_dir / "WinClientTool"
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("x")
    _frozen(monkeypatch, exe_dir)
    deleted, errors = ss.delete_items(
        [ScanItem(path=str(folder), size=1, is_dir=True, selected=True, safety="safe")])
    assert (deleted, errors) == (0, 1) and (folder / "a.txt").exists()


def test_a_sibling_folder_with_a_similar_name_is_still_cleaned(monkeypatch, tmp_path):
    """`Apps2` starts with `Apps`; only a real path boundary counts."""
    exe_dir = tmp_path / "Apps"
    exe_dir.mkdir()
    other = tmp_path / "Apps2"
    other.mkdir()
    junk = other / "junk.tmp"
    junk.write_text("x")
    _frozen(monkeypatch, exe_dir)
    deleted, errors = ss.delete_items(
        [ScanItem(path=str(junk), size=1, is_dir=False, selected=True, safety="safe")])
    assert (deleted, errors) == (1, 0) and not junk.exists()


def test_from_source_ordinary_deletion_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    junk = tmp_path / "junk.tmp"
    junk.write_text("x")
    deleted, errors = ss.delete_items(
        [ScanItem(path=str(junk), size=1, is_dir=False, selected=True, safety="safe")])
    assert (deleted, errors) == (1, 0)
