from types import SimpleNamespace
import pytest
from modules.file_forensics.engine.search import search_batches
from modules.file_forensics.engine.bulk_locks import attribute_handles


def test_streams_before_finishing_and_caps(tmp_path):
    for i in range(8): (tmp_path / f'{i}.txt').write_text('x')
    batches = list(search_batches(str(tmp_path), '', True, limit=3))
    assert len(batches[0].rows) == 1
    assert sum(len(b.rows) for b in batches) == 3
    assert batches[-1].outcome == 'truncated'
    assert batches[-1].matches == 3


def test_cancel_inside_directory_keeps_partial_results(tmp_path):
    for i in range(20): (tmp_path / f'{i}.txt').write_text('x')
    stopped = [False]
    stream = search_batches(str(tmp_path), '', True, cancelled=lambda: stopped[0])
    first = next(stream)
    stopped[0] = True
    rest = list(stream)
    assert len(first.rows) == 1
    assert rest[-1].outcome == 'stopped'
    assert rest[-1].files == 1


def test_name_filter_and_nonrecursive(tmp_path):
    (tmp_path / 'a.txt').write_text('x')
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'a.txt').write_text('x')
    assert sum(len(b.rows) for b in search_batches(str(tmp_path), 'A.TXT', False)) == 1


def test_missing_target_is_error(tmp_path):
    with pytest.raises(OSError): list(search_batches(str(tmp_path / 'gone'), '', True))


def test_bulk_exact_paths_and_streams_only():
    targets = {'a': r'\Device\Vol\a', 'b': r'\Device\Vol\b'}
    def handle(name, kind='File'):
        return SimpleNamespace(detail=name, type_name=kind, pid=1, process='app')
    found = attribute_handles(targets, [handle(r'\device\vol\a'), handle(r'\Device\Vol\a:s'),
                                      handle(r'\Device\Vol\a.old'), handle(r'\Device\Vol\b','Key')])
    assert len(found['a']) == 1
    assert found['b'] == []


def test_bulk_sweep_called_once_and_reuses_snapshot(monkeypatch):
    from modules.file_forensics.engine import bulk_locks as bulk
    calls = []
    snapshot = [SimpleNamespace(pid=10, name='app')]
    monkeypatch.setattr(bulk, 'to_device_path', lambda path: '\\Device\\Vol\\' + path)
    def source(text, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(matches=[], note='Partial snapshot', summary=lambda: 'Some processes refused')
    monkeypatch.setattr(bulk, 'find', source)
    locks, summaries = bulk.bulk_locks(['a', 'b', 'c'], snapshot)
    assert len(calls) == 1
    assert calls[0]['process_snapshot'] is snapshot
    assert calls[0]['modules'] is False
    assert len(locks) == 3
    assert 'Partial snapshot' in summaries['a']


def test_selected_cloud_file_is_never_opened(tmp_path, monkeypatch):
    from modules.file_forensics.engine import selected_details as details
    from modules.file_forensics.engine.search import fast_analysis
    path = tmp_path / 'cloud.exe'; path.write_text('test')
    row = fast_analysis(str(path), path.stat())
    monkeypatch.setattr(details, 'is_cloud_placeholder', lambda p: True)
    def forbidden(*a, **k):
        raise AssertionError('A cloud placeholder must never be opened')
    monkeypatch.setattr(details, 'check_own_signature', forbidden)
    monkeypatch.setattr(details, 'compute_sha256', forbidden)
    _, _, signature_notes = details.inspect_selected(row, 'signatures')
    _, _, reputation_notes = details.inspect_selected(row, 'reputation', 'key')
    assert any('not checked: cloud' in note for note in signature_notes)
    assert any('not checked: cloud' in note for note in reputation_notes)


def test_findref_does_not_take_second_process_snapshot(monkeypatch):
    from core.procengine import findref
    def forbidden():
        raise AssertionError('process snapshot was repeated')
    monkeypatch.setattr(findref, 'system_processes', forbidden)
    sweeps = []
    monkeypatch.setattr(findref, '_find_handles', lambda needle, names, report, stop, progress: sweeps.append(names))
    snapshot = [SimpleNamespace(pid=10, name='app')]
    findref.find('\\', handles=True, modules=False, process_snapshot=snapshot)
    assert sweeps == [{10: 'app'}]
