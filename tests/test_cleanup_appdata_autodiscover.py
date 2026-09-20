"""scan_appdata_autodiscover's _CACHE_DIR_NAMES set, expanded from a live
directory-name census of a real %LOCALAPPDATA% (see scanners_system.py's
own comment) rather than guessed -- this pins the new names down so a
later edit can't silently drop one.
"""
import os

from modules.cleanup.cleanup_scanner.scanners_system import scan_appdata_autodiscover


def _make_cache_dir(base: str, name: str) -> None:
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "junk.bin"), "wb") as f:
        f.write(b"x" * 1024)


def test_finds_dawn_and_directx_shader_caches(tmp_path, monkeypatch):
    local = tmp_path / "local"
    local.mkdir()
    _make_cache_dir(str(local), "SomeApp/DawnGraphiteCache")
    _make_cache_dir(str(local), "SomeApp/DX9Cache")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))

    result = scan_appdata_autodiscover()

    found = {os.path.basename(item.path) for item in result.items}
    assert "DawnGraphiteCache" in found
    assert "DX9Cache" in found


def test_does_not_flag_package_cache_as_junk(tmp_path, monkeypatch):
    """MSI/Burn's own installer cache -- removing it can break a future
    repair/uninstall of an unrelated app. Deliberately excluded, unlike
    every other name in this test file."""
    local = tmp_path / "local"
    local.mkdir()
    _make_cache_dir(str(local), "SomeApp/Package Cache")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))

    result = scan_appdata_autodiscover()

    found = {os.path.basename(item.path) for item in result.items}
    assert "Package Cache" not in found
