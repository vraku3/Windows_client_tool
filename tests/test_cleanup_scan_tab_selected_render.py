"""C2 (final whole-branch review): a ScanItem built with selected=False
must actually RENDER unchecked in _ScanTab's tree, and an unchecked item
must be excluded from what "Clean Selected" gathers to delete.

Before this fix, `_on_scan_result` unconditionally called
`child.setCheckState(0, Qt.CheckState.Checked)` for every row regardless
of `item.selected` -- so a scanner's own selected=False (e.g. an orphaned
user profile, or a virtual disk image) was silently overridden and the
row arrived pre-checked in the UI. The scanner-level claim was never
actually true end-to-end through the real rendering path.
"""
import time

from PyQt6.QtCore import Qt, QThreadPool

from modules.cleanup.cleanup_scanner import ScanItem, ScanResult


def _settle(qapp, timeout_ms: int = 10_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def scan_one_unselected_danger_item(min_age_days: int = 0) -> ScanResult:
    result = ScanResult()
    item = ScanItem(
        path=r"C:\Users\ghost", size=1024, is_dir=True,
        selected=False, safety="danger")
    result.items.append(item)
    result.total_size = item.size
    return result


def scan_one_selected_safe_item(min_age_days: int = 0) -> ScanResult:
    result = ScanResult()
    item = ScanItem(
        path=r"C:\Temp\junk.tmp", size=512, is_dir=False,
        selected=True, safety="safe")
    result.items.append(item)
    result.total_size = item.size
    return result


def _find_child_by_path(tree, path: str):
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        for j in range(group.childCount()):
            child = group.child(j)
            si = child.data(0, Qt.ItemDataRole.UserRole)
            if si is not None and si.path == path:
                return child
    return None


def test_a_scan_item_with_selected_false_renders_unchecked(qapp):
    from modules.cleanup.tabs._scan_tab import _ScanTab

    tab = _ScanTab({scan_one_unselected_danger_item: ("Orphaned Profiles", "danger")})
    tab._do_scan()
    _settle(qapp)

    child = _find_child_by_path(tab._tree, r"C:\Users\ghost")
    assert child is not None, "the scanned item never made it into the tree"
    assert child.checkState(0) == Qt.CheckState.Unchecked, (
        "a selected=False ScanItem must render Unchecked, not force-checked")


def test_a_scan_item_with_selected_true_still_renders_checked(qapp):
    """The fix must not flip the DEFAULT behaviour -- an ordinary
    selected=True item (the common case: safe temp files, etc.) still
    arrives pre-checked."""
    from modules.cleanup.tabs._scan_tab import _ScanTab

    tab = _ScanTab({scan_one_selected_safe_item: ("Temp Files", "safe")})
    tab._do_scan()
    _settle(qapp)

    child = _find_child_by_path(tab._tree, r"C:\Temp\junk.tmp")
    assert child is not None
    assert child.checkState(0) == Qt.CheckState.Checked


def test_an_unchecked_item_is_excluded_from_clean_selected(qapp):
    """_get_selected_items() reads .selected back from the checkbox
    state -- an item rendered Unchecked must come back with
    selected=False and be excluded from what "Clean Selected" deletes."""
    from modules.cleanup.tabs._scan_tab import _ScanTab

    tab = _ScanTab({scan_one_unselected_danger_item: ("Orphaned Profiles", "danger")})
    tab._do_scan()
    _settle(qapp)

    selected_items = tab._get_selected_items()
    to_delete = [i for i in selected_items if i.selected]
    assert not any(i.path == r"C:\Users\ghost" for i in to_delete), (
        "an unchecked (selected=False) item must not be included in "
        "what Clean Selected gathers for deletion")


def test_a_mixed_group_parent_checkbox_is_partially_checked(qapp):
    """A group can hold a mix of selected and unselected children. The
    parent's initial render must reflect that (PartiallyChecked), not
    force Checked regardless of the children underneath it."""
    from modules.cleanup.tabs._scan_tab import _ScanTab

    def scan_mixed(min_age_days: int = 0) -> ScanResult:
        result = ScanResult()
        a = ScanItem(path=r"C:\A", size=100, is_dir=False, selected=True, safety="caution")
        b = ScanItem(path=r"C:\B", size=100, is_dir=False, selected=False, safety="caution")
        result.items.extend([a, b])
        result.total_size = 200
        return result

    tab = _ScanTab({scan_mixed: ("Mixed Group", "caution")})
    tab._do_scan()
    _settle(qapp)

    assert tab._tree.topLevelItemCount() == 1
    parent = tab._tree.topLevelItem(0)
    assert parent.checkState(0) == Qt.CheckState.PartiallyChecked
