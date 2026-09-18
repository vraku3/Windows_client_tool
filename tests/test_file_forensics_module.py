import pytest
from PyQt6.QtWidgets import QApplication


class _FakeApp:
    config = None
    thread_pool = None


@pytest.fixture
def module(qapp):
    from modules.file_forensics.file_forensics_module import FileForensicsModule
    m = FileForensicsModule()
    m.on_start(_FakeApp())
    m.create_widget()
    return m


def test_module_declares_itself_correctly(module):
    assert module.name == "File Forensics"
    assert module.requires_admin is False


def test_create_widget_builds_without_raising(module):
    assert module._widget is not None


def test_search_with_no_folder_shows_the_empty_state(module, monkeypatch):
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [])
    module._folder_edit.setText(r"C:\some\folder")
    module._on_search_clicked()
    assert module._results_stack.currentIndex() == 1  # empty page


def test_a_result_populates_the_table_and_shows_content_page(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    fake_analysis = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\found.txt", size=10,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="TESTUSER", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [fake_analysis])
    module._folder_edit.setText(r"C:\some\folder")
    module._on_search_clicked()

    assert module._table.rowCount() == 1
    assert module._results_stack.currentIndex() == 0  # content page


def test_a_nonexistent_folder_shows_the_error_banner(module, monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError("no such folder")
    monkeypatch.setattr(module, "_analyze_folder", _raise)
    module._folder_edit.setText(r"C:\does\not\exist")
    module._on_search_clicked()
    assert not module._error_banner.isHidden()


def test_a_directory_listing_refusal_during_walk_is_not_swallowed(
        module, monkeypatch, tmp_path):
    """os.walk()'s default onerror=None would otherwise let a scandir()
    refusal on the folder itself (traversable but not listable -- a real
    Windows ACL configuration) return an empty result set silently,
    indistinguishable from "genuinely nothing here". The module must pass
    an onerror that re-raises so this shows up as a real refusal."""
    import os as os_module

    def _fake_walk(folder, onerror=None, **kwargs):
        if onerror is not None:
            onerror(PermissionError(13, "Access is denied", folder))
        return iter([])

    monkeypatch.setattr(os_module, "walk", _fake_walk)
    module._folder_edit.setText(str(tmp_path))
    module._recurse_cb.setChecked(True)
    module._on_search_clicked()

    assert not module._error_banner.isHidden()
    assert module._table.rowCount() == 0


def test_a_subdirectory_listing_refusal_is_skipped_not_fatal(
        module, monkeypatch, tmp_path):
    """The distinguishing case from the top-level test above: os.walk's
    onerror fires for EVERY directory it can't list anywhere in a
    recursive tree, not just the target folder -- System Volume
    Information, $RECYCLE.BIN, another user's profile subfolder are all
    ordinary occurrences on a real Windows volume. Those must be skipped
    and counted, not treated as a reason to abort a search that already
    found real results elsewhere in the tree."""
    import os as os_module
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    def _fake_walk(folder, onerror=None, **kwargs):
        if onerror is not None:
            # A DIFFERENT directory than the target folder itself failing
            # to list -- the ordinary mid-walk case, not the rare "the
            # user pointed the tool straight at something unlistable"
            # case the previous test covers.
            onerror(PermissionError(
                13, "Access is denied",
                os_module.path.join(folder, "System Volume Information"),
            ))
        yield (folder, [], ["found.txt"])

    def _fake_analyze(path, vt_api_key=""):
        return FileAnalysis(
            metadata=FileMetadata(
                path=path, size=1,
                created=datetime.datetime(2026, 1, 1),
                modified=datetime.datetime(2026, 1, 1),
                accessed=datetime.datetime(2026, 1, 1),
                owner="TESTUSER", read_only=False,
            ),
            locking_processes=[], locking_summary="ok",
            creator_candidates=[], top_creator_signature=None, reputation=None,
        )

    monkeypatch.setattr(os_module, "walk", _fake_walk)
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", _fake_analyze
    )
    module._folder_edit.setText(str(tmp_path))
    module._recurse_cb.setChecked(True)
    module._on_search_clicked()

    assert module._table.rowCount() == 1
    assert module._last_skip_count == 1
    assert not module._error_banner.isHidden()
    assert "1" in module._error_banner.text()
    assert "skipped" in module._error_banner.text().lower()


def test_per_file_analysis_refusals_are_disclosed_not_dropped(
        module, monkeypatch, tmp_path):
    """A file that exists but can't be analyzed (e.g. ACL-denied) must not
    just vanish from the results with no trace -- see CLAUDE.md's
    tweak_engine "(N step(s) could not be checked)" disclosed-uncertainty
    convention. Results and the disclosure can both be true at once: the
    table still shows what WAS analyzed."""
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    (tmp_path / "good.txt").write_text("hello")
    (tmp_path / "bad.txt").write_text("world")

    def _fake_analyze(path, vt_api_key=""):
        import os as os_module
        if os_module.path.basename(path) == "bad.txt":
            raise PermissionError(13, "Access is denied", path)
        return FileAnalysis(
            metadata=FileMetadata(
                path=path, size=5,
                created=datetime.datetime(2026, 1, 1),
                modified=datetime.datetime(2026, 1, 1),
                accessed=datetime.datetime(2026, 1, 1),
                owner="TESTUSER", read_only=False,
            ),
            locking_processes=[], locking_summary="ok",
            creator_candidates=[], top_creator_signature=None, reputation=None,
        )

    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", _fake_analyze
    )
    module._folder_edit.setText(str(tmp_path))
    module._recurse_cb.setChecked(False)
    module._on_search_clicked()

    assert module._table.rowCount() == 1
    assert not module._error_banner.isHidden()
    assert "1" in module._error_banner.text()
    assert "skipped" in module._error_banner.text().lower()


def test_a_locked_file_row_is_highlighted(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="ok", creator_candidates=[], top_creator_signature=None,
        reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()

    from PyQt6.QtGui import QColor
    item = module._table.item(0, 5)
    assert item.background().color() != QColor(0, 0, 0, 0)  # actually colored


def test_selecting_a_row_shows_its_detail(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="1 matches in 250 processes", creator_candidates=[],
        top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()
    module._table.selectRow(0)
    module._on_row_selected()

    assert "notepad" in module._detail_label.text()
    assert "250 processes" in module._detail_label.text()


def test_kill_locking_process_asks_for_confirmation(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="ok", creator_candidates=[], top_creator_signature=None,
        reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()
    module._table.selectRow(0)
    module._on_row_selected()

    from PyQt6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No)
    killed = []
    monkeypatch.setattr("modules.file_forensics.file_forensics_module.end_process",
                        lambda pid: killed.append(pid))

    module._on_kill_locking_clicked()

    assert asked  # confirmation was shown
    assert killed == []  # user said No, nothing killed
