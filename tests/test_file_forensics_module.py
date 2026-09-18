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
