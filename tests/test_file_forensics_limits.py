"""Result budgets, large-model batching and incremental search summaries."""
import time
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QTableView
from core.config_manager import ConfigManager
from modules.file_forensics.file_forensics_module import FileForensicsModule
from modules.file_forensics.engine.search import search_batches, fast_analysis, SearchBatch
from modules.file_forensics.engine.analysis import LockInspection
from modules.file_forensics.engine.export_csv import export_csv
from modules.file_forensics.limit_controls import LIMIT_KEY, LIMIT_STEPS
from modules.file_forensics.results_model import ResultsModel, ResultsFilter, SORT_ROLE


@pytest.fixture
def pane(qapp, tmp_path, monkeypatch):
    from modules.file_forensics import history_log
    monkeypatch.setattr(history_log, '_history_path', lambda: str(tmp_path / 'history.json'))
    config = ConfigManager(str(tmp_path / 'config'), {})
    m = FileForensicsModule()
    m.on_start(SimpleNamespace(config=config))
    m.create_widget()
    yield m
    m.on_stop()
    m._widget.deleteLater()
    qapp.processEvents()


def test_limit_steps_exact_value_and_unlimited(pane):
    assert pane._result_limit == 5000
    for index, value in enumerate(LIMIT_STEPS):
        pane._limit_slider.setValue(index)
        assert pane._result_limit == value
        assert pane.app.config.get(LIMIT_KEY) == value
        assert pane._limit_state.isHidden() == (value is not None)
    pane._limit_slider.setValue(1)
    pane._limit_spin.setValue(12345)
    assert pane._result_limit == 12345
    assert pane._limit_slider.value() == 2
    assert pane._limit_spin.value() == 12345
    assert all('Stop' in w.toolTip() for w in (pane._limit_slider, pane._limit_spin, pane._limit_state))


@pytest.mark.parametrize('value', [None, 0, 12345, 10000000])
def test_persistence_round_trip(pane, tmp_path, value):
    pane._set_result_limit(None if value == 0 else value)
    if value == 0:
        pane.app.config.set(LIMIT_KEY, 0)
    pane.app.config.save()
    config = ConfigManager(str(tmp_path / 'config'), {})
    config.load()
    other = FileForensicsModule()
    other.on_start(SimpleNamespace(config=config))
    other.create_widget()
    assert other._result_limit == (None if value == 0 else value)
    other.on_stop()
    other._widget.deleteLater()


@pytest.mark.parametrize('value', ['5000', True, False, -1, 99, 10000001, 1.5, {}, []])
def test_invalid_config_falls_back(pane, value):
    pane.app.config.set(LIMIT_KEY, value)
    other = FileForensicsModule()
    other.on_start(pane.app)
    other.create_widget()
    assert other._result_limit == 5000
    other.on_stop()
    other._widget.deleteLater()


@pytest.mark.parametrize('limit,expected,outcome', [(3, 3, 'truncated'), (20, 8, 'completed'), (None, 8, 'completed')])
def test_engine_budgets(tmp_path, limit, expected, outcome):
    for i in range(8):
        (tmp_path / str(i)).write_text('x')
    batches = list(search_batches(str(tmp_path), limit=limit))
    assert sum(len(b.rows) for b in batches) == expected
    assert batches[-1].outcome == outcome


def test_unlimited_stop_flushes_rows(tmp_path):
    for i in range(8):
        (tmp_path / str(i)).write_text('x')
    stop = False
    stream = search_batches(str(tmp_path), limit=None, cancelled=lambda: stop)
    assert len(next(stream).rows) == 1
    stop = True
    tail = list(stream)
    assert tail[-1].outcome == 'stopped'
    assert tail[-1].matches == 1


@pytest.mark.parametrize('limit,folder,name,extension,recurse,confirm', [
    (None, 'C:\\', '', '', True, True), (500000, 'C:\\', '', '', True, True),
    (None, 'C:\\', 'report', '', True, False), (None, 'C:\\', '', 'txt', True, False),
    (None, 'C:\\Windows', '', '', True, False), (None, 'C:\\', '', '', False, False),
    (None, os.path.expanduser('~'), '', '', True, True),
    (None, 'C:\\', '', ', . ,', True, True),
])
def test_large_search_confirmation(pane, monkeypatch, limit, folder, name, extension, recurse, confirm):
    pane._set_result_limit(limit)
    pane._extensions_edit.setText(extension)
    pane._recurse_cb.setChecked(recurse)
    seen = []
    def reject(box):
        seen.append(box.text())
        assert box.defaultButton().text() == 'Cancel'
        box.reject()
    monkeypatch.setattr(QMessageBox, 'exec', reject)
    assert pane._confirm_broad_search(folder, name) == (not confirm)
    assert bool(seen) == confirm
    if confirm:
        assert 'over a million files' in seen[0] and 'several GB' in seen[0]
        assert 'once at the end' in seen[0]


def test_chosen_limit_reaches_engine_history_and_csv(pane, tmp_path, monkeypatch):
    from modules.file_forensics import history_log
    seen = []
    pane._folder_edit.setText(str(tmp_path))
    pane._limit_spin.setValue(12345)
    pool = SimpleNamespace(start=lambda worker: seen.append(worker))
    monkeypatch.setattr(FileForensicsModule, 'thread_pool', property(lambda self: pool))
    pane._on_search_clicked()
    assert seen[0].args[-1] == 12345
    assert pane._search_limit == 12345
    pane._set_result_limit(None)  # Next-search changes must not alter the active budget.
    pane._last_batch = SearchBatch((), 1, 12345, 12345, 0, 'truncated')
    pane._finish_search('truncated')
    assert 'Truncated at 12,345' in pane._progress_label.text()
    assert 'first 12,345' in pane._info_banner.text()
    assert history_log.recent()[0]['result limit'] == '12,345'
    assert '12,345' in pane._history_table.item(0, 3).text()
    path = tmp_path / 'results.csv'
    export_csv(path, [], pane._search_context)
    assert '# result limit: 12,345' in path.read_text()
    assert isinstance(pane._table, QTableView)


def test_batches_count_only_new_rows_and_memory_is_sampled(pane, tmp_path, monkeypatch):
    path = tmp_path / 'one.exe'
    path.write_text('x')
    row = fast_analysis(str(path), path.stat())
    pane._populate_table([])
    worker = object()
    pane._search_worker = worker
    pane._search_limit = None
    pane._on_search_event(worker, ('batch', SearchBatch((row,), 1, 1, 1, 0)))
    import modules.file_forensics.file_forensics_module as module
    original = module.matches_filter
    calls = []
    monkeypatch.setattr(module, 'matches_filter', lambda r, k: calls.append(r) or original(r, k))
    second = replace(row, metadata=replace(row.metadata, path=str(tmp_path / 'two.txt')))
    pane._on_search_event(worker, ('batch', SearchBatch((second,), 1, 2, 2, 0)))
    assert calls and all(r is second for r in calls)
    assert pane._count_values['all'] == 2 and pane._count_values['size'] == 2
    assert 'measured sample' in pane._progress_label.text()
    assert 'approximate' in pane._progress_label.text()
    enriched = replace(row, creator_ready=True, lock_inspection=LockInspection())
    pane._on_search_event(worker, ('enriched', (enriched,)))
    assert pane._count_values['all'] == 2 and pane._count_values['pending'] == 1
    assert pane._count_values['ready'] == 1
    pane._search_worker = None


def test_200000_rows_batch_insert_and_numeric_sort(qapp):
    stat = SimpleNamespace(st_size=0, st_ctime=time.time(), st_mtime=time.time(), st_atime=time.time())
    model = ResultsModel()
    proxy = ResultsFilter()
    proxy.setSourceModel(model)
    inserted = []
    model.rowsInserted.connect(lambda parent, first, last: inserted.append(last - first + 1))
    started = time.monotonic()
    for start in range(0, 200000, 1000):
        rows = []
        for i in range(start, start + 1000):
            stat.st_size = 200000 - i
            rows.append(fast_analysis(f'C:/fake/{i}.txt', stat))
        model.append(rows)
    insert_time = time.monotonic() - started
    assert model.rowCount() == 200000 and inserted == [1000] * 200
    proxy.sort(2, Qt.SortOrder.AscendingOrder)
    assert proxy.index(0, 2).data(SORT_ROLE) == 1
    assert proxy.index(199999, 2).data(SORT_ROLE) == 200000
    assert insert_time < 15
    assert time.monotonic() - started < 30

def test_creator_snapshot_index_preserves_candidates_and_avoids_repeated_conversion(monkeypatch):
    from datetime import datetime, timedelta
    from modules.file_forensics.engine import creator_heuristic as ch
    created = datetime(2026, 1, 1, 12)
    processes = [SimpleNamespace(pid=i, name=str(i), create_time=i) for i in range(500)]
    calls = []
    monkeypatch.setattr(ch, '_filetime_to_datetime', lambda value: calls.append(value) or created + timedelta(seconds=value - 250))
    monkeypatch.setattr(ch, 'resolve', lambda pid: SimpleNamespace(path=None, user=None, path_error='refused', user_error='refused'))
    expected = ch.find_creator_candidates(created, processes=processes)
    index = ch.ProcessStarts(processes)
    calls.clear()
    actual = ch.find_creator_candidates(created, processes=index)
    assert actual == expected
    assert calls == []
    assert ch.find_creator_candidates(datetime.min, processes=index) == []
    assert ch.find_creator_candidates(datetime.max, processes=index) == []
    started = time.monotonic()
    for _ in range(100000):
        assert ch.find_creator_candidates(created - timedelta(days=1), processes=index) == []
    assert time.monotonic() - started < 3

def test_bulk_preparation_can_stop_and_reports_progress(monkeypatch):
    from modules.file_forensics.engine import bulk_locks as bulk
    stopped = False
    messages = []
    monkeypatch.setattr(bulk, 'to_device_path', lambda path: '\\Device\\Vol\\')
    def progress(text):
        nonlocal stopped
        messages.append(text)
        stopped = True
    def forbidden(*args, **kwargs):
        pytest.fail('A stopped preparation must not start the handle sweep')
    monkeypatch.setattr(bulk, 'find', forbidden)
    assert bulk.bulk_locks(['C:/a', 'C:/b'], [], lambda: stopped, progress) == ({}, {}, {})
    assert 'Preparing one handle check' in messages[0]


def test_bulk_one_drive_translation_and_one_sweep_with_progress(monkeypatch):
    from modules.file_forensics.engine import bulk_locks as bulk
    from core.procengine.findref import FindReport
    translated, sweeps, messages = [], [], []
    monkeypatch.setattr(bulk, 'to_device_path', lambda path: translated.append(path) or '\\Device\\Vol\\')
    def find(text, **kwargs):
        sweeps.append(kwargs)
        kwargs['progress'](10, 20)
        return FindReport()
    monkeypatch.setattr(bulk, 'find', find)
    paths = [f'C:\\fake\\{i}' for i in range(10000)]
    locks, _, inspections = bulk.bulk_locks(paths, [], progress=messages.append)
    assert len(translated) == len(sweeps) == 1
    assert len(locks) == len(inspections) == 10000
    assert any('10 / 20 processes' in message for message in messages)


def test_active_limit_is_passed_to_engine(pane, monkeypatch):
    import modules.file_forensics.file_forensics_module as module
    limits = []
    def batches(*args, **kwargs):
        limits.append(kwargs['limit'])
        return []
    monkeypatch.setattr(module, 'search_batches', batches)
    for limit in (12345, None):
        pane._run_search(SimpleNamespace(is_cancelled=False), 'unused', '', True, limit=limit)
    assert limits == [12345, None]
