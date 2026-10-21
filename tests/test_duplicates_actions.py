import os

import pytest

from modules.cleanup.duplicates.actions import perform_actions, verify_pair
from modules.cleanup.duplicates.enumerate import ScanOptions
from modules.cleanup.duplicates.finder import find_duplicates


@pytest.fixture(autouse=True)
def ordinary_temp_folder(monkeypatch):
    # Pytest's temp lives below AppData; exercise ordinary-root action policy here.
    monkeypatch.setattr("modules.cleanup.duplicates.actions.system_folders", lambda: [])


def groups_at(root, suffix="bin", method="exact"):
    for name in ("a", "b"):
        (root / f"{name}.{suffix}").write_bytes(b"same content")
    return find_duplicates(ScanOptions(roots=[str(root)], min_size=1,
                           skip_system_folders=False, use_cache=False, method=method)).groups


def test_changed_file_refused(tmp_path):
    group = groups_at(tmp_path)[0]
    keeper, target = group.files
    with open(target.path, "wb") as stream:
        stream.write(b"changed")
    reason = verify_pair(keeper, target)
    assert reason and "changed" in reason.lower()


def test_protected_file_refused(tmp_path):
    group = groups_at(tmp_path, "pst")[0]
    assert "protected" in verify_pair(*group.files).lower()


def test_all_selected_refused(tmp_path):
    groups = groups_at(tmp_path)
    with pytest.raises(ValueError, match="Keep at least"):
        perform_actions(groups, {f.path for f in groups[0].files}, action=lambda p: None)


def test_injected_recycle_counts_no_freed_bytes(tmp_path):
    groups = groups_at(tmp_path)
    target = groups[0].files[1]
    result = perform_actions(groups, {target.path}, action=os.remove)
    assert result.outcomes[0].status == "done"
    assert result.freed == 0
    assert os.path.exists(groups[0].files[0].path)


def test_injected_action_must_remove_source(tmp_path):
    groups = groups_at(tmp_path)
    target = groups[0].files[1]
    result = perform_actions(groups, {target.path}, action=lambda p: None)
    assert result.outcomes[0].status == "failed"
    assert not result.freed


def test_probable_upgraded_before_action(tmp_path):
    groups = groups_at(tmp_path, method="probable")
    called = []
    target = groups[0].files[1]
    # Simulate different data while preserving the snapshot size/mtime.
    with open(target.path, "wb") as stream:
        stream.write(b"evil content")
    os.utime(target.path, ns=(target.mtime_ns, target.mtime_ns))
    result = perform_actions(groups, {target.path}, action=called.append)
    assert result.outcomes[0].status == "skipped"
    assert not called


def test_keeper_changed_refused(tmp_path):
    group = groups_at(tmp_path)[0]
    with open(group.files[0].path, "ab") as stream:
        stream.write(b"new")
    assert "changed" in verify_pair(*group.files).lower()


def test_permanent_delete_uses_measured_outcome(tmp_path):
    groups = groups_at(tmp_path)
    target = groups[0].files[1]
    result = perform_actions(groups, {target.path}, mode="delete", record=False)
    assert result.freed == target.size
    assert not os.path.exists(target.path)


def test_move_same_volume_keeps_both_contents_and_frees_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    groups = groups_at(tmp_path)
    destination = tmp_path / "moved"
    destination.mkdir()
    target = groups[0].files[1]
    result = perform_actions(groups, {target.path}, mode="move", destination=str(destination), record=False)
    assert result.outcomes[0].status == "done"
    assert result.freed == 0
    assert next(destination.iterdir()).read_bytes() == b"same content"


def test_system_root_must_be_explicit_and_confirmed(tmp_path, monkeypatch):
    groups = groups_at(tmp_path)
    group = groups[0]
    monkeypatch.setattr("modules.cleanup.duplicates.actions.system_folders", lambda: [str(tmp_path)])
    assert "explicit root" in verify_pair(*group.files)
    assert verify_pair(*group.files, scanned_roots=[str(tmp_path)], confirm_system=True) is None
    assert "explicit root" in verify_pair(*group.files, scanned_roots=[str(tmp_path.parent)], confirm_system=True)


def test_cancelled_batch_reports_unattempted_files(tmp_path):
    groups = groups_at(tmp_path)
    target = groups[0].files[1]
    result = perform_actions(groups, {target.path}, cancelled=lambda: True, record=False)
    assert result.cancelled
    assert result.outcomes[0].status == "skipped"
    assert "cancel" in result.outcomes[0].reason.lower()


def test_signin_store_ancestor_refused(tmp_path):
    folder = tmp_path / "IndexedDB"
    folder.mkdir()
    group = groups_at(folder)[0]
    assert "Protected" in verify_pair(*group.files)


def test_resized_images_can_be_manually_removed_after_fresh_similarity_check(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    from modules.cleanup.duplicates.models import FileEntry, DuplicateGroup
    image = Image.new("RGB", (32, 32))
    image.putdata([(x * 8, y * 8, 0) for y in range(32) for x in range(32)])
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    image.save(a)
    image.resize((64, 64)).save(b)
    entries = [FileEntry.from_path(str(p)) for p in (a, b)]
    group = DuplicateGroup(0, entries, "images", "similar")
    result = perform_actions([group], {str(b)}, action=os.remove, record=False, image_threshold=3)
    assert result.outcomes[0].status == "done"


def test_recycler_forces_recoverable_shell_operation(tmp_path, monkeypatch):
    from modules.cleanup.duplicates.actions import recycle_file
    from win32com.shell import shell
    import pythoncom
    seen = {}
    class Operation:
        def SetOperationFlags(self, flags):
            seen["flags"] = flags
        def DeleteItem(self, item, sink):
            seen["item"] = item
        def PerformOperations(self):
            seen["performed"] = True
        def GetAnyOperationsAborted(self):
            return False
    monkeypatch.setattr(pythoncom, "CoCreateInstance", lambda *a: Operation())
    monkeypatch.setattr(shell, "SHCreateItemFromParsingName", lambda *a: "fake shell item")
    recycle_file(str(tmp_path / "never-created"))
    assert seen["flags"] & 0x00080000  # FOFX_RECYCLEONDELETE
    assert seen["performed"]
