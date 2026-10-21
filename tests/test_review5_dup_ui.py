from types import SimpleNamespace

from modules.cleanup.tabs import _duplicates_tab as ui
from modules.cleanup.duplicates.models import FileEntry


def test_freed_signal_preserves_large_totals(qapp):
    tab = ui._DuplicatesTab()
    received = []
    tab.freed_bytes.connect(received.append)
    tab.freed_bytes.emit(3 * 1024 ** 3)
    assert received == [3 * 1024 ** 3]
    tab.deleteLater()


def test_open_workers_kept_until_finished_and_retired_on_cancel(qapp, tmp_path, monkeypatch):
    path = tmp_path / "file"
    path.write_bytes(b"data")
    tab = ui._DuplicatesTab()
    monkeypatch.setattr(tab, "_current_entry", lambda: FileEntry.from_path(str(path)))
    pending = []
    monkeypatch.setattr(ui, "get_long_op_pool", lambda: SimpleNamespace(start=pending.append))
    for _ in range(10):
        tab._open(False)
    try:
        assert tab._open_workers == pending
        pending[0].signals.finished.emit()
        qapp.processEvents()
        assert tab._open_workers == pending[1:]
        tab._cancel_all()
        assert all(worker.is_cancelled for worker in pending[1:])
        assert not tab._open_workers
    finally:
        for worker in pending:
            worker.signals.finished.emit()
        qapp.processEvents()
        tab.deleteLater()
