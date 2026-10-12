r"""Opening logs from a whole tree, with a say in which ones.

The flat scan stays the default because `C:\Windows\Logs` has twelve
subfolders. Measured there: **90 logs, 106 MB**, of which 85 are under 1 MB
and one is 84.5 MB. Opening all ninety unasked is the accident the checklist
exists to prevent; so is quietly opening the 84.5 MB one.
"""
import os

import pytest

from modules.log_viewer.log_set import LogSet, PRESELECT_MAX_BYTES, preselected


def _write(path, size=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * size, encoding="utf-8")
    return str(path)


# ---- the scan -----------------------------------------------------------

def test_a_recursive_scan_finds_nested_logs(tmp_path):
    _write(tmp_path / "top.log")
    _write(tmp_path / "sub" / "nested.log")
    _write(tmp_path / "sub" / "deeper" / "deep.log")

    found, capped = LogSet.logs_under(str(tmp_path))

    assert [os.path.basename(p) for p in found] == \
        ["deep.log", "nested.log", "top.log"]
    assert not capped


def test_the_flat_scan_is_still_flat(tmp_path):
    """`logs_in_folder` must not start recursing: the default open would
    then walk twelve subfolders."""
    _write(tmp_path / "top.log")
    _write(tmp_path / "sub" / "nested.log")
    assert len(LogSet.logs_in_folder(str(tmp_path))) == 1


def test_non_logs_are_ignored(tmp_path):
    _write(tmp_path / "a.log")
    _write(tmp_path / "sub" / "notes.txt")
    found, _ = LogSet.logs_under(str(tmp_path))
    assert len(found) == 1


def test_the_scan_is_capped_and_says_so(tmp_path):
    for n in range(12):
        _write(tmp_path / f"log{n:02d}.log")

    found, capped = LogSet.logs_under(str(tmp_path), cap=5)

    assert len(found) == 5
    assert capped is True


def test_a_folder_that_is_not_there_yields_nothing(tmp_path):
    found, capped = LogSet.logs_under(str(tmp_path / "absent"))
    assert found == [] and not capped


# ---- what is ticked by default ------------------------------------------

def test_small_logs_are_preselected(tmp_path):
    small = _write(tmp_path / "small.log", 100)
    assert preselected([small]) == {small}


def test_a_large_log_is_offered_but_not_ticked(tmp_path):
    r"""`CbsPersist_20260831055247.log` is 84.5 MB. Opening it because
    someone pointed at C:\Windows\Logs is the accident this prevents."""
    big = tmp_path / "big.log"
    big.write_bytes(b"x" * (PRESELECT_MAX_BYTES + 1))

    assert preselected([str(big)]) == set()


def test_a_log_exactly_at_the_threshold_is_ticked(tmp_path):
    edge = tmp_path / "edge.log"
    edge.write_bytes(b"x" * PRESELECT_MAX_BYTES)
    assert preselected([str(edge)]) == {str(edge)}


def test_a_file_that_vanished_is_not_preselected(tmp_path):
    """A log can roll away between the scan and the dialog."""
    assert preselected([str(tmp_path / "gone.log")]) == set()


@pytest.mark.skipif(not os.path.isdir(r"C:\Windows\Logs"),
                    reason="no Windows Logs folder")
def test_the_real_windows_logs_tree_is_handled(tmp_path):
    """The folder this feature exists for."""
    found, capped = LogSet.logs_under(r"C:\Windows\Logs")
    assert found, r"found no logs under C:\Windows\Logs"
    ticked = preselected(found)
    # Pin the rule, not the machine: right after a restart every log here can
    # be small, so "something stays unticked" failed on a perfectly working run.
    for path in found:
        try:
            small = os.path.getsize(path) <= PRESELECT_MAX_BYTES
        except OSError:
            continue  # rolled away between the walk and now
        assert (path in ticked) == small, path


# ---- the checklist dialog -----------------------------------------------

from modules.log_viewer.folder_dialog import FolderPickDialog  # noqa: E402


def test_the_dialog_lists_what_was_found(qapp, tmp_path):
    a = _write(tmp_path / "a.log")
    b = _write(tmp_path / "sub" / "b.log")

    dialog = FolderPickDialog(str(tmp_path), [a, b], {a}, False)
    try:
        assert dialog.count() == 2
        assert dialog.chosen() == [a], "only the preselected one is ticked"
    finally:
        dialog.deleteLater()


def test_the_dialog_shows_paths_relative_to_the_folder(qapp, tmp_path):
    b = _write(tmp_path / "sub" / "b.log")
    dialog = FolderPickDialog(str(tmp_path), [b], {b}, False)
    try:
        label = dialog.label_for(0)
        assert str(tmp_path) not in label, "the full path is noise here"
        assert "b.log" in label
    finally:
        dialog.deleteLater()


def test_the_dialog_shows_each_size(qapp, tmp_path):
    a = _write(tmp_path / "a.log", 2048)
    dialog = FolderPickDialog(str(tmp_path), [a], {a}, False)
    try:
        assert "KB" in dialog.label_for(0)
    finally:
        dialog.deleteLater()


def test_the_dialog_says_when_the_scan_was_capped(qapp, tmp_path):
    a = _write(tmp_path / "a.log")
    dialog = FolderPickDialog(str(tmp_path), [a], {a}, True)
    try:
        assert "stopped" in dialog.note().lower()
    finally:
        dialog.deleteLater()


def test_the_dialog_is_quiet_when_it_was_not_capped(qapp, tmp_path):
    a = _write(tmp_path / "a.log")
    dialog = FolderPickDialog(str(tmp_path), [a], {a}, False)
    try:
        assert "stopped" not in dialog.note().lower()
    finally:
        dialog.deleteLater()


def test_select_all_ticks_everything_including_the_big_one(qapp, tmp_path):
    a = _write(tmp_path / "a.log")
    big = tmp_path / "big.log"
    big.write_bytes(b"x" * (PRESELECT_MAX_BYTES + 1))

    dialog = FolderPickDialog(str(tmp_path), [a, str(big)], {a}, False)
    try:
        dialog.select_all(True)
        assert len(dialog.chosen()) == 2
    finally:
        dialog.deleteLater()
