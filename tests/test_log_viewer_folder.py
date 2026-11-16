"""Opening a folder, and the merged view it produces.

A Windows servicing failure is told across CBS, DISM and setupact at once, so
the pane has to be able to show them as one timeline -- and then let you
narrow back down to one file, which is what the Source column and combo are
for.
"""

import pytest

from modules.log_viewer.log_model import MESSAGE, SOURCE
from modules.log_viewer.log_viewer_module import LogViewerWidget


def _service_line(stamp, component, message):
    return f"{stamp}, Info                  {component}    {message}\n"


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "cbs.log").write_text(
        _service_line("2026-08-27 10:00:00", "CBS", "cbs first")
        + _service_line("2026-08-27 10:00:30", "CBS", "cbs second"),
        encoding="utf-8")
    (tmp_path / "dism.log").write_text(
        _service_line("2026-08-27 10:00:15", "DISM", "dism middle"),
        encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a log\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def viewer(qapp, folder):
    widget = LogViewerWidget()
    widget.open_folder(str(folder))
    yield widget
    widget.stop()


def _messages(widget):
    model = widget.model
    return [model.data(model.index(row, MESSAGE))
            for row in range(model.rowCount())]


def _sources(widget):
    model = widget.model
    return [model.data(model.index(row, SOURCE))
            for row in range(model.rowCount())]


# ---- opening a folder ---------------------------------------------------

def test_opening_a_folder_interleaves_every_log_in_it(viewer):
    assert _messages(viewer) == ["cbs first", "dism middle", "cbs second"]


def test_a_non_log_file_in_the_folder_is_ignored(viewer):
    assert "not a log" not in _messages(viewer)


def test_each_row_says_which_file_it_came_from(viewer):
    assert _sources(viewer) == ["cbs.log", "dism.log", "cbs.log"]


def test_an_empty_folder_says_so_rather_than_looking_broken(qapp, tmp_path):
    widget = LogViewerWidget()
    try:
        widget.open_folder(str(tmp_path))
        assert "No logs" in widget.status.text()
    finally:
        widget.stop()


# ---- the Source column appears only when it means something -------------

def test_the_source_column_shows_when_several_logs_are_open(viewer):
    assert not viewer.table.isColumnHidden(SOURCE)


def test_the_source_column_hides_for_a_single_log(qapp, folder):
    widget = LogViewerWidget()
    try:
        widget.open(str(folder / "cbs.log"))
        assert widget.table.isColumnHidden(SOURCE)
    finally:
        widget.stop()


# ---- the Source filter --------------------------------------------------

def test_the_source_combo_lists_the_open_logs(viewer):
    items = [viewer.source.itemText(i) for i in range(viewer.source.count())]
    assert items == ["All", "cbs.log", "dism.log"]


def test_choosing_a_source_narrows_to_that_log(viewer):
    viewer.source.setCurrentIndex(viewer.source.findText("dism.log"))
    assert _messages(viewer) == ["dism middle"]


def test_clearing_the_source_brings_the_others_back(viewer):
    viewer.source.setCurrentIndex(viewer.source.findText("dism.log"))
    viewer.source.setCurrentIndex(0)
    assert len(_messages(viewer)) == 3


# ---- crossing two interactions ------------------------------------------

def test_opening_one_log_does_not_keep_the_folders_source_filter(viewer,
                                                                 folder):
    """The exact defect shape the real-log pass found: a stale filter
    surviving into a newly-opened log while its combo reads "All"."""
    viewer.source.setCurrentIndex(viewer.source.findText("dism.log"))

    viewer.open(str(folder / "cbs.log"))

    assert viewer.source.currentText() == "All"
    assert viewer.model._log == "", \
        "the combo says All but the model kept the old source"
    assert _messages(viewer) == ["cbs first", "cbs second"]


def test_a_severity_filter_survives_opening_a_folder(viewer, folder):
    """Filter first, then open a folder: the new records must obey it."""
    viewer._level_boxes["Info"].setChecked(False)
    assert _messages(viewer) == []

    viewer.open_folder(str(folder))

    assert _messages(viewer) == [], "the filter was dropped by the reopen"


def test_following_picks_up_appends_from_every_log(viewer, folder):
    viewer.follow.setChecked(True)
    with open(folder / "dism.log", "a", encoding="utf-8", newline="") as handle:
        handle.write(_service_line("2026-08-27 10:01:00", "DISM", "dism later"))
    with open(folder / "cbs.log", "a", encoding="utf-8", newline="") as handle:
        handle.write(_service_line("2026-08-27 10:00:45", "CBS", "cbs later"))

    viewer._poll()

    assert _messages(viewer)[-2:] == ["cbs later", "dism later"]


# ---- the status line ----------------------------------------------------

def test_the_status_names_how_many_logs_are_open(viewer):
    assert "2 logs" in viewer.status.text()


def test_the_status_names_the_file_when_only_one_is_open(qapp, folder):
    widget = LogViewerWidget()
    try:
        widget.open(str(folder / "cbs.log"))
        assert "cbs.log" in widget.status.text()
        assert "1 logs" not in widget.status.text()
    finally:
        widget.stop()


# ---- the Open menu ------------------------------------------------------

def test_the_open_menu_offers_a_folder(qapp):
    widget = LogViewerWidget()
    try:
        labels = [a.text() for a in widget.open_menu.actions()]
        assert any("folder" in label.lower() for label in labels)
    finally:
        widget.stop()


def test_the_open_menu_offers_the_largest_archive(qapp, monkeypatch):
    """The existing entry offers the NEWEST archive, which is routinely the
    smallest -- 15 MB here against the 363 MB one that holds the history."""
    import modules.log_viewer.log_viewer_module as module
    monkeypatch.setattr(module, "largest_cbs_archive",
                        lambda: r"C:\Windows\Logs\CBS\CbsPersist_big.log")
    widget = LogViewerWidget()
    try:
        labels = [a.text() for a in widget.open_menu.actions()]
        assert any("largest" in label.lower() for label in labels)
    finally:
        widget.stop()


# ---- the column has to fit real log names -------------------------------

def test_the_source_column_is_wide_enough_for_a_real_log_name(qapp, tmp_path):
    r"""Found by rendering the real C:\Windows\Logs\CBS folder.

    A Qt column's default width is a guess until it has met the real dump.
    Source got the default and rendered "CbsPersist_20…" -- and CBS archives
    differ only in the TIMESTAMP at the end of the name, so every archive
    elided to the same eight characters and became impossible to tell apart.
    """
    from PyQt6.QtWidgets import QHeaderView

    long_name = "CbsPersist_20260831055247.log"
    (tmp_path / long_name).write_text(
        _service_line("2026-08-27 10:00:00", "CBS", "x"), encoding="utf-8")
    (tmp_path / "CbsPersist_20260827190818.log").write_text(
        _service_line("2026-08-27 10:00:01", "CBS", "y"), encoding="utf-8")

    widget = LogViewerWidget()
    try:
        widget.open_folder(str(tmp_path))
        header = widget.table.horizontalHeader()
        assert header.sectionResizeMode(SOURCE) == \
            QHeaderView.ResizeMode.Interactive, \
            "Source is interactive so the user can drag and resize it"

        needed = widget.table.fontMetrics().horizontalAdvance(long_name)
        assert header.sectionSize(SOURCE) >= needed, (
            f"Source is {header.sectionSize(SOURCE)}px for a name needing "
            f"{needed}px")
    finally:
        widget.stop()
