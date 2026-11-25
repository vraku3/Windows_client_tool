"""Tests for developer cleanup scanners."""
from __future__ import annotations

import os
from modules.cleanup.cleanup_scanner.scanners_dev import (
    scan_pip_cache,
    scan_npm_cache,
    scan_nuget_cache,
    scan_gradle_cache,
    scan_cargo_cache,
    scan_winget_packages,
    scan_yarn_cache,
)


def test_scanners_return_scan_result_and_handle_missing_dirs(tmp_path, monkeypatch):
    # Point env vars to isolated tmp_path
    fake_local = tmp_path / "local"
    fake_appdata = tmp_path / "appdata"
    fake_home = tmp_path / "home"

    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setattr(os.path, "expanduser", lambda path: str(fake_home) if path == "~" else path)

    # Missing directories should return empty ScanResult without crashing
    for scanner in (scan_pip_cache, scan_npm_cache, scan_nuget_cache,
                    scan_gradle_cache, scan_cargo_cache, scan_winget_packages, scan_yarn_cache):
        res = scanner()
        assert res.total_size == 0
        assert len(res.items) == 0


def test_scan_pip_cache_finds_files(tmp_path, monkeypatch):
    fake_local = tmp_path / "local"
    pip_cache = fake_local / "pip" / "cache"
    pip_cache.mkdir(parents=True)
    sample_file = pip_cache / "wheel.whl"
    sample_file.write_bytes(b"x" * 1024)

    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    res = scan_pip_cache()
    assert res.total_size >= 1024
    assert len(res.items) == 1
    assert res.items[0].safety == "safe"
