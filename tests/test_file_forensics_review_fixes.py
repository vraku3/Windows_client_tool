"""Regressions for the nine verified File Forensics review findings."""
from dataclasses import replace
from types import SimpleNamespace
import logging
import os

import pytest
from PyQt6.QtWidgets import QMessageBox, QDialog
from core.worker import WorkerSignals
from modules.file_forensics.file_forensics_module import FileForensicsModule
from modules.file_forensics.engine.search import fast_analysis, search_batches, SearchBatch


class FakeWorker:
    def __init__(self, fn, *args):
        self.fn, self.args = fn, args
        self.signals = WorkerSignals()
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True


@pytest.fixture
def pane(qapp, tmp_path, monkeypatch):
    from modules.file_forensics import history_log
    monkeypatch.setattr(history_log, '_history_path', lambda: str(tmp_path / 'history.json'))
    monkeypatch.setattr('modules.file_forensics.file_forensics_module.Worker', FakeWorker)
    m = FileForensicsModule()
    m.on_start(SimpleNamespace(config=None))
    m.create_widget()
    m.on_activate()
    started = []
    monkeypatch.setattr(FileForensicsModule, 'thread_pool', property(lambda self: SimpleNamespace(start=started.append)))
    m.started = started
    p = tmp_path / 'file.exe'
    p.write_text('data')
    m._populate_table([fast_analysis(str(p), p.stat())])
    m._table.selectRow(0)
    yield m
    m.on_stop()


@pytest.mark.parametrize('shutdown', ['on_deactivate', 'on_stop'])
def test_detail_callbacks_after_leaving_are_inert(pane, shutdown):
    worker = pane.started[0]
    row = pane._current_results[0]
    getattr(pane, shutdown)()
    worker.signals.cancelled.emit()
    worker.signals.result.emit((replace(row, metadata=replace(row.metadata, owner='late')), 'metadata', ()))
    worker.signals.error.emit('late refusal')
    worker.signals.finished.emit()
    assert len(pane.started) == 1
    assert not pane._detail_cache
    assert pane._current_results[0].metadata.owner == 'Not checked yet'
    pane.on_activate()
    worker.signals.result.emit((replace(row, metadata=replace(row.metadata, owner='stale')), 'metadata', ()))
    worker.signals.error.emit('stale refusal')
    worker.signals.cancelled.emit()
    worker.signals.finished.emit()
    assert not pane._detail_cache
    assert pane._current_results[0].metadata.owner == 'Not checked yet'
    assert len(pane.started) == 2  # only activation may retry


def test_cancelled_metadata_is_terminal(pane):
    worker = pane.started[0]
    worker.signals.cancelled.emit()
    worker.signals.finished.emit()
    assert len(pane.started) == 1


def test_enrichment_keeps_resolved_metadata(pane):
    row = pane._current_results[0]
    pane._details_ready((replace(row, metadata=replace(row.metadata, owner='resolved')), 'metadata', ()))
    pane._search_worker = object()
    pane._on_search_event(pane._search_worker, ('enriched', (row,)))
    assert pane._current_results[0].metadata.owner == 'resolved'
    assert 'metadata' in pane._detail_cache[row.metadata.path]


def test_late_metadata_keeps_reputation(pane):
    row = pane._current_results[0]
    reputation = object()
    pane._details_ready((replace(row, reputation=reputation), 'reputation', ()))
    pane._details_ready((row, 'metadata', ()))
    assert pane._current_results[0].reputation is reputation


def test_enrichment_retries_requested_signatures_and_rejects_old_candidate(pane):
    row = pane._current_results[0]
    pane._check_signatures()
    worker = pane.started[-1]
    worker.signals.result.emit((row, 'signatures', ()))
    worker.signals.finished.emit()
    candidate = SimpleNamespace(path='creator.exe', name='creator', pid=1, user='', user_error='',
                                path_error='', delta_seconds=0)
    enriched = replace(row, creator_candidates=[candidate])
    pane._search_worker = object()
    pane._on_search_event(pane._search_worker, ('enriched', (enriched,)))
    assert len(pane.started) == 3
    assert 'signatures' not in pane._detail_cache[row.metadata.path]
    pane._details_ready((replace(row, top_creator_signature=object()), 'signatures', ()))
    assert pane._current_results[0].top_creator_signature is None
    assert 'signatures' not in pane._detail_cache[row.metadata.path]


def test_watch_checks_locks_and_creator_visibility(pane, monkeypatch):
    from modules.file_forensics.engine.analysis import LockInspection
    path = pane._current_results[0].metadata.path
    proc = SimpleNamespace(pid=1, process='holder')
    candidate = SimpleNamespace(name='creator')
    calls = []
    monkeypatch.setattr('modules.file_forensics.file_forensics_module.system_processes', lambda: [])
    monkeypatch.setattr('modules.file_forensics.file_forensics_module.bulk_locks',
        lambda paths, *args: (calls.append(paths) or {path: [proc]}, {path: 'partial'},
                             {path: LockInspection('partial', 'refused handles')}))
    monkeypatch.setattr('modules.file_forensics.file_forensics_module.find_creator_candidates', lambda *a, **k: [candidate])
    row = pane._analyze_watch(path)
    assert calls == [[path]] and row.locking_processes == [proc]
    pane._model.replace([row])
    assert pane._model.index(0, 6).data() == 'creator'
    assert row.lock_inspection.state == 'partial'


@pytest.mark.parametrize('report_kwargs, expected', [({}, 'complete'),
    ({'refused_handles': 1}, 'partial'), ({'stopped_early': True, 'note': 'budget exhausted'}, 'partial'),
    ({'blocked_handles': 1}, 'partial'), ({'unsearched': 2}, 'partial')])
def test_bulk_structured_inspection(monkeypatch, report_kwargs, expected):
    from modules.file_forensics.engine import bulk_locks as bulk
    from core.procengine.findref import FindReport
    monkeypatch.setattr(bulk, 'to_device_path', lambda p: None if p == 'unc' else r'\Device\Vol\file')
    monkeypatch.setattr(bulk, 'find', lambda *a, **k: FindReport(**report_kwargs))
    _, _, inspection = bulk.bulk_locks(['local', 'unc'], [])
    assert inspection['local'].state == expected
    assert inspection['unc'].state == 'could_not_check'
    assert inspection['unc'].reason


def test_locked_tile_discloses_incomplete_inspection(pane):
    from modules.file_forensics.engine.analysis import LockInspection
    row = replace(pane._current_results[0], locking_summary='no matches',
                  lock_inspection=LockInspection('partial', 'budget exhausted'))
    pane._populate_table([row])
    tile = pane._tiles['locked'][0]
    assert '0 observed' in tile.value.text()
    assert 'inspection incomplete: budget exhausted' in tile.sub.text().lower()


def test_skipped_tile_opens_list_without_filtering(pane, monkeypatch):
    seen = []
    pane._last_batch = SearchBatch((), 1, 1, 1, 2, unreadable=1, skipped_links=1,
                                  unreadable_items=(('secret.txt', 'Access denied'),))
    monkeypatch.setattr(QDialog, 'exec', lambda dialog: seen.append(dialog))
    pane._tiles['skipped'][0].clicked.emit()
    assert pane._proxy.kind == 'all'
    assert pane._proxy.rowCount() == 1
    from PyQt6.QtWidgets import QTableWidget
    table = seen[0].findChild(QTableWidget)
    assert table.item(0, 0).text() == 'secret.txt'
    assert table.item(0, 1).text() == 'Access denied'


@pytest.mark.parametrize('root_kind, recurse, name_filter, asks', [
    ('drive', True, '', True), ('profile', True, '', True),
    ('drive', False, '', False), ('profile', False, '', False), ('drive', True, 'log', False)])
def test_broad_confirmation_scope(pane, monkeypatch, root_kind, recurse, name_filter, asks):
    folder = os.path.splitdrive(os.path.abspath('.'))[0] + '\\' if root_kind == 'drive' else os.path.expanduser('~')
    pane._recurse_cb.setChecked(recurse)
    seen = []
    def reject(box):
        seen.append((box.text(), box.defaultButton().text()))
        box.reject()
    monkeypatch.setattr(QMessageBox, 'exec', reject)
    pane._confirm_broad_search(folder, name_filter)
    assert bool(seen) == asks
    if asks:
        assert folder in seen[0][0] and 'subfolders' in seen[0][0] and '5,000' in seen[0][0]
        assert seen[0][1] == 'Cancel'


def test_unreadable_items_and_link_counts_are_separate_bounded_and_logged(tmp_path, monkeypatch, caplog):
    from modules.file_forensics.engine import search
    class Entry:
        def __init__(self, n):
            self.path = str(tmp_path / str(n))
            self.name = str(n)
            self.n = n
        def is_dir(self, **kwargs):
            if self.n:
                raise PermissionError('refused')
            return False
        def is_symlink(self):
            return True
    class Entries:
        def __enter__(self):
            return iter(Entry(i) for i in range(251))
        def __exit__(self, *args):
            return False
    monkeypatch.setattr(search.os, 'scandir', lambda path: Entries())
    with caplog.at_level(logging.WARNING):
        final = list(search_batches(str(tmp_path)))[-1]
    assert final.skipped_links == 1 and final.unreadable == 250 and final.skipped == 251
    assert len(final.unreadable_items) == 200
    warnings = [r.message for r in caplog.records if r.name == search.__name__]
    assert len(warnings) == 51
    assert str(tmp_path / '1') in warnings[0] and 'refused' in warnings[0]
    assert '200' in warnings[-1]


@pytest.mark.parametrize('late_signal', ['result', 'error'])
def test_inflight_signatures_retry_new_creator_even_if_selection_changed(pane, late_signal):
    from core.procengine.signatures import SignatureFacts, VALID
    row = pane._current_results[0]
    pane._check_signatures()
    worker = pane.started[-1]
    candidate = SimpleNamespace(path='new.exe', name='new creator', pid=1, user='', user_error='',
                                path_error='', delta_seconds=0)
    enriched = replace(row, creator_candidates=[candidate])
    pane._search_worker = object()
    pane._on_search_event(pane._search_worker, ('enriched', (enriched,)))
    assert len(pane.started) == 2  # one metadata, one signature worker still in flight
    pane._table.clearSelection()
    pane._table.setCurrentIndex(pane._proxy.index(-1, -1))
    if late_signal == 'result':
        worker.signals.result.emit((row, 'signatures', ('old evidence',)))
    else:
        worker.signals.error.emit('old candidate vanished')
    worker.signals.finished.emit()
    assert len(pane.started) == 3
    assert 'signatures' not in pane._detail_cache.get(row.metadata.path, {})
    facts = SignatureFacts(path='new.exe', status=VALID)
    retry = pane.started[-1]
    retry.signals.result.emit((replace(enriched, top_creator_signature=facts), 'signatures', ('new evidence',)))
    retry.signals.finished.emit()
    assert pane._current_results[0].top_creator_signature == facts
    assert pane._detail_cache[row.metadata.path]['signatures'] == ('new evidence',)
    assert len(pane.started) == 3


def test_model_shows_known_creator_when_lock_check_is_unavailable(pane):
    row = replace(pane._current_results[0], creator_candidates=[SimpleNamespace(name='known creator')],
                  creator_ready=True)
    pane._model.update_row(row)
    assert pane._model.index(0, 5).data() == '...'
    assert pane._model.index(0, 6).data() == 'known creator'


def test_requested_signatures_finish_for_enriched_creator(pane):
    from core.procengine.signatures import SignatureFacts, VALID
    row = pane._current_results[0]
    pane._check_signatures()
    old = pane.started[-1]
    old.signals.result.emit((row, 'signatures', ()))
    old.signals.finished.emit()
    candidate = SimpleNamespace(path='creator.exe', name='creator', pid=1, user='', user_error='',
                                path_error='', delta_seconds=0)
    enriched = replace(row, creator_candidates=[candidate])
    pane._search_worker = object()
    pane._on_search_event(pane._search_worker, ('enriched', (enriched,)))
    retry = pane.started[-1]
    facts = SignatureFacts(path='creator.exe', status=VALID)
    retry.signals.result.emit((replace(enriched, top_creator_signature=facts), 'signatures', ()))
    retry.signals.finished.emit()
    assert pane._current_results[0].top_creator_signature == facts
    assert 'signatures' in pane._detail_cache[row.metadata.path]


def test_unreadable_subfolder_is_recorded_and_logged(tmp_path, monkeypatch, caplog):
    from modules.file_forensics.engine import search
    (tmp_path / 'denied').mkdir()
    original = search.os.scandir
    def scandir(path):
        if path == str(tmp_path / 'denied'):
            raise PermissionError('folder denied')
        return original(path)
    monkeypatch.setattr(search.os, 'scandir', scandir)
    with caplog.at_level(logging.WARNING):
        batch = list(search_batches(str(tmp_path)))[-1]
    assert batch.unreadable == 1 and batch.skipped_links == 0
    assert batch.unreadable_items == ((str(tmp_path / 'denied'), 'folder denied'),)
    assert 'folder denied' in caplog.text and str(tmp_path / 'denied') in caplog.text
