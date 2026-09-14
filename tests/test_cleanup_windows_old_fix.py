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
import tempfile

from modules.cleanup.cleanup_scanner.scanners_system import scan_windows_old


def _fake_windows_old(monkeypatch):
    """Point SystemDrive at a real temp directory with a real
    Windows.old subfolder inside it, so the scanner's own
    os.path.join(SystemDrive + "\\", "Windows.old") lands on real,
    scannable content -- no os.path monkeypatching needed."""
    tmp = tempfile.mkdtemp()
    old_dir = os.path.join(tmp, "Windows.old")
    os.makedirs(old_dir)
    with open(os.path.join(old_dir, "stub.txt"), "w") as f:
        f.write("x" * 1000)
    monkeypatch.setenv("SystemDrive", tmp)
    return old_dir


def test_follows_systemdrive_rather_than_a_hardcoded_c(monkeypatch):
    old_dir = _fake_windows_old(monkeypatch)
    result = scan_windows_old()
    assert len(result.items) == 1
    assert os.path.normcase(result.items[0].path) == os.path.normcase(old_dir)


def test_safety_is_caution_not_safe(monkeypatch):
    _fake_windows_old(monkeypatch)
    result = scan_windows_old()
    assert result.items[0].safety == "caution"
