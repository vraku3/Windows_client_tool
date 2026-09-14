"""scan_windows_old had two real bugs: it hardcoded C:\\Windows.old (a
Windows install on D: was invisible to it, violating this codebase's own
"never a drive letter" catalog convention), and it was marked
safety="safe" -- meaning QuickCleanupTab's dashboard-level "Clean All
Safe" button would silently delete it with zero confirmation, more
confident than Microsoft's own guidance (Windows.old shares hardlinked
files with the live OS; Disk Cleanup / Storage Sense is the supported
removal path, not a raw folder delete).
"""
import os

from modules.cleanup.cleanup_scanner.scanners_system import scan_windows_old


def _fake_windows_old(tmp_path, monkeypatch):
    """Point SystemDrive at a real temp directory (pytest's tmp_path,
    cleaned up automatically) with a real Windows.old subfolder inside
    it, so the scanner's own os.path.join(SystemDrive + "\\",
    "Windows.old") lands on real, scannable content -- no os.path
    monkeypatching needed."""
    old_dir = tmp_path / "Windows.old"
    old_dir.mkdir()
    (old_dir / "stub.txt").write_text("x" * 1000)
    monkeypatch.setenv("SystemDrive", str(tmp_path))
    return str(old_dir)


def test_follows_systemdrive_rather_than_a_hardcoded_c(tmp_path, monkeypatch):
    old_dir = _fake_windows_old(tmp_path, monkeypatch)
    result = scan_windows_old()
    assert len(result.items) == 1
    assert os.path.normcase(result.items[0].path) == os.path.normcase(old_dir)


def test_safety_is_caution_not_safe(tmp_path, monkeypatch):
    _fake_windows_old(tmp_path, monkeypatch)
    result = scan_windows_old()
    assert result.items[0].safety == "caution"
