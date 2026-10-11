"""Final review of the Cleanup deletion engine (2026-10-11), the UI side:
finding 1 (the tabs report MEASURED bytes, never the scanned size), finding 2
(a Temp breakdown child keeps its parent's age floor) and finding 5 (a
category group's clean is cancellable)."""
import os
import threading
import time

import pytest
from PyQt6.QtCore import Qt, QThreadPool

from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.cleanup_scanner import ScanItem, ScanResult, scanners_system as ss
from modules.cleanup.cleanup_scanner.deleter import DeleteReport


def _settle(qapp, timeout_ms=30_000):
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def no_system_guards(monkeypatch):
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])


# ── 1. Measured totals ────────────────────────────────────────────────────

def test_scan_tab_emits_the_measured_bytes(qapp):
    from modules.cleanup.tabs._scan_tab import _ScanTab
    tab = _ScanTab({})
    tab._do_scan = lambda: None
    got = []
    tab.freed_bytes.connect(got.append)
    tab._on_clean_done((1, 1, 0))
    assert got == [0]


def test_quick_cleanup_emits_the_measured_bytes_not_the_scanned_total(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    result = ScanResult(items=[ScanItem(path=r"C:\safe_0", size=5_000_000, is_dir=False)])
    result.total_size = 5_000_000
    tab._results = {"temp": result}
    tab._preset_combo.setCurrentIndex(tab._preset_combo.findData("custom"))
    tab.scan = lambda *a, **k: None

    def fake_run_clean_safe(widget, items, **kwargs):
        kwargs["on_done"](0, 1, 0)       # the only file was locked
        return None
    monkeypatch.setattr("modules.cleanup.clean_safe_runner.run_clean_safe", fake_run_clean_safe)
    got = []
    tab.freed_bytes.connect(got.append)
    tab._do_clean_all_safe()
    assert got == [0], "the scanned 5 MB was reported as freed"


def _group_with(qapp, item):
    from modules.cleanup.components.category_group import CategoryGroup
    group = CategoryGroup("Temp", lambda: ScanResult(items=[item], total_size=item.size))
    group.set_result(ScanResult(items=[item], total_size=item.size))
    group._do_scan = lambda: None
    return group


def test_category_group_emits_the_measured_bytes(qapp, tmp_path, no_system_guards):
    held = tmp_path / "held.tmp"
    held.write_bytes(b"x" * 4096)
    group = _group_with(qapp, ScanItem(path=str(held), size=4096, is_dir=False))
    got = []
    group.freed_bytes.connect(got.append)
    fd = os.open(str(held), os.O_RDONLY)
    try:
        group._do_clean_all()
        _settle(qapp)
    finally:
        os.close(fd)
    assert got == [0] and held.exists()


def test_quick_cleanup_forwards_a_category_groups_freed_bytes(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    got = []
    tab.freed_bytes.connect(got.append)
    for group in tab._groups_container:
        group.freed_bytes.emit(123)
    assert got == [123] * len(tab._groups_container) and got


# ── 5. Cancellation reaches the engine (category group) ───────────────────

def test_cancelling_a_category_group_clean_reaches_the_engine(qapp, monkeypatch):
    started, release, seen = threading.Event(), threading.Event(), []

    def fake(items, stop_wuauserv=False, cancelled=None):
        started.set()
        release.wait(10)
        seen.append(cancelled())
        return DeleteReport(cancelled=True), [], []
    monkeypatch.setattr(cs, "delete_items_report", fake)
    group = _group_with(qapp, ScanItem(path=r"C:\x", size=1, is_dir=False))
    group._do_clean_all()
    assert started.wait(5)
    group.cancel()
    release.set()
    _settle(qapp)
    assert seen == [True]
    assert group._cleaning is False, "a cancelled clean left the group refusing to clean again"


# ── 2. The Temp breakdown keeps its floor ─────────────────────────────────

def test_a_breakdown_child_inherits_its_parents_age_floor(qapp, tmp_path, no_system_guards):
    from modules.cleanup.tabs._scan_tab import _ScanTab
    child_dir = tmp_path / "{GUID}"
    child_dir.mkdir()
    fresh = child_dir / "setup.msi"
    fresh.write_bytes(b"x" * 64)
    (tmp_path / "other").mkdir()

    def scan_temp(min_age_days: int = 0) -> ScanResult:
        return ScanResult(items=[ScanItem(path=str(tmp_path), size=2 * 1024**3, is_dir=True,
                                          keep_newer_than_hours=24)])
    tab = _ScanTab({scan_temp: ("Temp", "safe")})
    tab._on_scan_result((scan_temp(), {scan_temp: scan_temp()}))
    parent = tab._tree.topLevelItem(0).child(0)
    tab._fill_breakdown(parent)
    _settle(qapp)
    children = [parent.child(k).data(0, Qt.ItemDataRole.UserRole) for k in range(parent.childCount())]
    child = next(c for c in children if c is not None and c.path == str(child_dir))
    assert child.keep_newer_than_hours == 24 and child.keep_root is False
    child.selected = True
    report, _r, _p = ss.delete_items_report([child])
    assert fresh.exists() and report.kept_recent == 1
