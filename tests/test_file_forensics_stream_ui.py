"""Behavioral regressions for bounded, streamed searches and virtual sorting."""
import time
from threading import Event
from modules.file_forensics.engine.analysis import LockInspection
from types import SimpleNamespace
import pytest
from PyQt6.QtCore import Qt, QThreadPool, QTimer
from PyQt6.QtWidgets import QMessageBox
from modules.file_forensics.file_forensics_module import FileForensicsModule
from modules.file_forensics.engine.search import SearchBatch, fast_analysis


@pytest.fixture
def pane(qapp, tmp_path, monkeypatch):
    from modules.file_forensics import history_log
    monkeypatch.setattr(history_log, '_history_path', lambda: str(tmp_path / 'history.json'))
    m = FileForensicsModule()
    m.on_start(SimpleNamespace(config=None))
    m.create_widget()
    m.on_activate()
    m._folder_edit.setText(str(tmp_path))
    yield m
    m.on_stop()
    QThreadPool.globalInstance().waitForDone(10000)
    qapp.processEvents()


def wait_until(qapp, predicate):
    deadline = time.monotonic() + 10
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    assert predicate()


@pytest.mark.parametrize('field', ['_folder_edit', '_filter_edit'])
def test_enter_starts_search(pane, field, monkeypatch, qapp):
    calls = []
    monkeypatch.setattr(pane, '_analyze_folder', lambda *a, **k: calls.append(a) or [])
    getattr(pane, field).returnPressed.emit()
    wait_until(qapp, lambda: pane._search_worker is None)
    assert len(calls) == 1
    assert pane._search_btn.text() == 'Search'


def test_streamed_row_is_visible_before_phase_two_and_stop_resets(pane, tmp_path, monkeypatch, qapp):
    (tmp_path / 'one.txt').write_text('x')
    entered = Event()
    def locks(paths, processes, cancelled):
        entered.set()
        while not cancelled():
            time.sleep(.005)
        return {p: [] for p in paths}, {p: 'Stopped handle sweep' for p in paths}, {p: LockInspection('partial', 'Stopped') for p in paths}
    monkeypatch.setattr('modules.file_forensics.file_forensics_module.system_processes', lambda: [])
    monkeypatch.setattr('modules.file_forensics.file_forensics_module.bulk_locks', locks)
    pane._on_search_clicked()
    wait_until(qapp, lambda: pane._proxy.rowCount() == 1 and entered.is_set())
    assert pane._search_btn.text() == 'Stop'
    assert pane._search_btn.isEnabled()
    pane._on_search_clicked()
    wait_until(qapp, lambda: pane._search_worker is None)
    assert pane._search_btn.text() == 'Search'
    assert 'Stopped' in pane._progress_label.text()
    assert pane._current_results
    from modules.file_forensics import history_log
    assert history_log.recent()[0]['outcome'] == 'stopped'


def test_drive_root_confirmation_defaults_to_cancel(pane, monkeypatch):
    pane._folder_edit.setText('C:\\')
    seen = []
    def reject(box):
        seen.append((box.text(), box.defaultButton().text()))
        box.reject()
        return 0
    monkeypatch.setattr(QMessageBox, 'exec', reject)
    pane._on_search_clicked()
    assert pane._search_worker is None
    assert seen and seen[0][1] == 'Cancel'
    assert 'Add a name filter?' in seen[0][0]


def test_truncation_disclosure_and_history(pane, tmp_path):
    p = tmp_path / 'one.txt'; p.write_text('x')
    row = fast_analysis(str(p), p.stat())
    pane._search_folder = str(tmp_path)
    pane._populate_table([row])
    pane._last_batch = SearchBatch((), 1, 5000, 5000, 0, 'truncated')
    pane._finish_search('truncated')
    assert not pane._info_banner.isHidden()
    assert 'first 5,000' in pane._info_banner.text()
    assert 'Truncated' in pane._progress_label.text()
    from modules.file_forensics import history_log
    assert history_log.recent()[0]['outcome'] == 'truncated'


def test_one_snapshot_and_one_handle_sweep_for_many_files(pane, tmp_path, monkeypatch, qapp):
    for i in range(10): (tmp_path / f'{i}.txt').write_text('x')
    calls = []
    monkeypatch.setattr('modules.file_forensics.file_forensics_module.system_processes', lambda: calls.append('snapshot') or [])
    def locks(paths, processes, cancelled):
        calls.append('handles')
        return {p: [] for p in paths}, {p: 'No readable locks' for p in paths}, {p: LockInspection() for p in paths}
    monkeypatch.setattr('modules.file_forensics.file_forensics_module.bulk_locks', locks)
    pane._on_search_clicked()
    wait_until(qapp, lambda: pane._search_worker is None)
    assert calls == ['snapshot', 'handles']
    assert len(pane._current_results) == 10
    assert all(r.own_signature is None and r.reputation is None for r in pane._current_results)


def test_numeric_size_sort_keeps_selected_identity(pane, tmp_path):
    rows = []
    for name, size in [('large', 100), ('small', 9)]:
        p = tmp_path / name; p.write_bytes(b'x' * size)
        rows.append(fast_analysis(str(p), p.stat()))
    pane._populate_table(rows)
    pane._table.sortByColumn(2, Qt.SortOrder.AscendingOrder)
    pane._table.selectRow(0)
    assert pane._selected_analysis().metadata.size == 9
    assert pane._proxy.index(0, 0).data() == 'small'


def test_stale_detail_result_cannot_replace_new_search(pane, tmp_path, monkeypatch, qapp):
    p = tmp_path / 'one.txt'; p.write_text('x')
    row = fast_analysis(str(p), p.stat())
    pane._populate_table([row])
    gate = Event()
    def details(*a):
        gate.wait(5)
        return row, 'metadata', ('old result',)
    monkeypatch.setattr('modules.file_forensics.file_forensics_module.inspect_selected', details)
    pane._table.selectRow(0)
    pane._search_generation += 1
    gate.set()
    QThreadPool.globalInstance().waitForDone(10000)
    qapp.processEvents()
    assert str(p) not in pane._detail_cache
