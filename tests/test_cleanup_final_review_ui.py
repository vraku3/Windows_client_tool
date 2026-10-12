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


def test_a_row_the_scanner_left_unselected_shows_unticked_and_is_not_deleted(qapp, tmp_path):
    """The tree ticked EVERY row while the engine deletes only selected items, so
    an orphaned VM disk (selected=False by design) showed ticked and was skipped."""
    from modules.cleanup.components.category_group import CategoryGroup
    keep = ScanItem(path=str(tmp_path / "vm.vhdx"), size=10, is_dir=False, selected=False)
    junk = ScanItem(path=str(tmp_path / "junk.tmp"), size=5, is_dir=False)
    group = CategoryGroup("Mixed", lambda: ScanResult(items=[keep, junk], total_size=15))
    group.set_result(ScanResult(items=[keep, junk], total_size=15))
    root = group._tree.topLevelItem(0)
    states = {root.child(i).data(0, Qt.ItemDataRole.UserRole).path: root.child(i).checkState(0)
              for i in range(root.childCount())}
    assert states[keep.path] == Qt.CheckState.Unchecked
    assert states[junk.path] == Qt.CheckState.Checked
    assert [i.path for i in group._items_to_clean()] == [junk.path]


def test_a_row_the_person_ticks_by_hand_is_deleted_even_if_unselected(qapp, tmp_path):
    from modules.cleanup.components.category_group import CategoryGroup
    keep = ScanItem(path=str(tmp_path / "vm.vhdx"), size=10, is_dir=False, selected=False)
    group = CategoryGroup("Mixed", lambda: ScanResult(items=[keep], total_size=10))
    group.set_result(ScanResult(items=[keep], total_size=10))
    group._tree.topLevelItem(0).child(0).setCheckState(0, Qt.CheckState.Checked)
    chosen = group._items_to_clean()
    assert [i.path for i in chosen] == [keep.path] and chosen[0].selected is True
    assert keep.selected is False      # the scan result itself is not mutated


def test_the_group_root_shows_partial_when_some_rows_are_unselected(qapp, tmp_path):
    from modules.cleanup.components.category_group import CategoryGroup
    keep = ScanItem(path=str(tmp_path / "vm.vhdx"), size=10, is_dir=False, selected=False)
    junk = ScanItem(path=str(tmp_path / "junk.tmp"), size=5, is_dir=False)
    group = CategoryGroup("Mixed", lambda: ScanResult(items=[keep, junk], total_size=15))
    group.set_result(ScanResult(items=[keep, junk], total_size=15))
    root = group._tree.topLevelItem(0)
    assert root.checkState(0) == Qt.CheckState.Checked        # every scanner-selected row is ticked
    assert [i.path for i in group._items_to_clean()] == [junk.path]


def test_the_group_root_never_ticks_a_row_the_scanner_left_unselected(qapp, tmp_path):
    # Codex review: an auto-tristate root ticked EVERY child, so one root click
    # plus Clean All deleted an orphaned VM disk nobody chose.
    from modules.cleanup.components.category_group import CategoryGroup
    keep = ScanItem(path=str(tmp_path / "vm.vhdx"), size=10, is_dir=False, selected=False)
    junk = ScanItem(path=str(tmp_path / "junk.tmp"), size=5, is_dir=False)
    group = CategoryGroup("Mixed", lambda: ScanResult(items=[keep, junk], total_size=15))
    group.set_result(ScanResult(items=[keep, junk], total_size=15))
    root = group._tree.topLevelItem(0)
    root.setCheckState(0, Qt.CheckState.Unchecked)
    assert group._items_to_clean() == []
    root.setCheckState(0, Qt.CheckState.Checked)
    assert [i.path for i in group._items_to_clean()] == [junk.path]


def test_a_hand_ticked_row_in_an_all_unselected_group_ticks_the_root(qapp, tmp_path):
    from modules.cleanup.components.category_group import CategoryGroup
    keep = ScanItem(path=str(tmp_path / "vm.vhdx"), size=10, is_dir=False, selected=False)
    group = CategoryGroup("VMs", lambda: ScanResult(items=[keep], total_size=10))
    group.set_result(ScanResult(items=[keep], total_size=10))
    root = group._tree.topLevelItem(0)
    assert root.checkState(0) == Qt.CheckState.Unchecked
    root.child(0).setCheckState(0, Qt.CheckState.Checked)
    assert root.checkState(0) == Qt.CheckState.Checked
