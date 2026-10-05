r"""Cleanup must never remove %ProgramData%\Package Cache.

Burn bundles (VC++ redistributables, .NET, Visual Studio, vendor RGB and
driver suites) keep the setup exe their uninstall entry RUNS in there.
Found 2026-10-05: the folder was gone on the real machine and 16 uninstall
entries pointed at missing exes. Cleanup used to offer it as a "caution"
catalog entry, which the Thorough and Aggressive presets both sweep.
"""
import os

from modules.cleanup.cleanup_scanner import catalog
from modules.cleanup.cleanup_scanner import scanners_system as ss
from modules.cleanup.cleanup_scanner._common import ScanItem


def _program_data(monkeypatch, tmp_path):
    root = tmp_path / "ProgramData"
    cache = root / "Package Cache" / "{042d26ef-3dbe-4c25-95d3-4c1b11b235a7}"
    cache.mkdir(parents=True)
    (cache / "vcredist_x64.exe").write_bytes(b"MZ")
    monkeypatch.setenv("ProgramData", str(root))
    return root, cache


def _item(path, is_dir):
    return ScanItem(path=str(path), size=1, is_dir=is_dir, selected=True, safety="safe")


def test_a_bundle_exe_inside_package_cache_is_refused(monkeypatch, tmp_path):
    _root, cache = _program_data(monkeypatch, tmp_path)
    exe = cache / "vcredist_x64.exe"
    assert ss.delete_items([_item(exe, False)]) == (0, 1)
    assert exe.exists()


def test_the_package_cache_folder_itself_is_refused(monkeypatch, tmp_path):
    root, cache = _program_data(monkeypatch, tmp_path)
    assert ss.delete_items([_item(root / "Package Cache", True)]) == (0, 1)
    assert (cache / "vcredist_x64.exe").exists()


def test_a_folder_that_contains_package_cache_is_refused(monkeypatch, tmp_path):
    root, cache = _program_data(monkeypatch, tmp_path)
    assert ss.delete_items([_item(root, True)]) == (0, 1)
    assert (cache / "vcredist_x64.exe").exists()


def test_a_sibling_with_a_similar_name_is_still_cleaned(monkeypatch, tmp_path):
    root, _cache = _program_data(monkeypatch, tmp_path)
    other = root / "Package Cache Old"
    other.mkdir()
    junk = other / "junk.tmp"
    junk.write_text("x")
    assert ss.delete_items([_item(junk, False)]) == (1, 0)
    assert not junk.exists()


def test_no_catalog_entry_points_into_package_cache():
    offenders = [spec_id for spec_id, spec in catalog.load_catalog().items()
                 if any("package cache" in os.path.normcase(p).lower() for p in spec.paths)]
    assert offenders == []
