import json
from types import SimpleNamespace

from modules.cleanup.cleanup_scanner._common import ScanItem, ScanResult
from modules.cleanup.cleanup_scanner.scan_snapshot import ScanSnapshot, from_results, append_summary, read_summaries


def results():
    def scan_temp():
        return None
    scan_temp.spec = SimpleNamespace(id='temp_files', label='Temp', category='system', safety='safe')
    return {'System': {scan_temp: ScanResult([ScanItem(r'C:\Temp', 100, True)])},
            'Apps': {scan_temp: ScanResult([ScanItem('c:/temp/', 120, True, safety='caution')])}}


def test_dedupe_is_windows_aware_and_conservative():
    snapshot = from_results(results())
    assert isinstance(snapshot, ScanSnapshot)
    assert len(snapshot.rows) == 2  # both tab memberships remain visible
    assert len(snapshot.unique_rows) == 1
    assert snapshot.total_size == 120
    assert snapshot.unique_rows[0].effective_safety == 'caution'
    assert snapshot.safe_size == 0
    assert snapshot.review_size == 120
    assert snapshot.tabs_for(r'C:\TEMP') == ('System', 'Apps')


def test_summary_cap_recovery_and_no_paths(tmp_path, caplog):
    path = tmp_path / 'scan_summaries.json'
    path.write_text('{broken', encoding='utf-8')
    snapshot = from_results(results())
    assert append_summary(snapshot, path=path)
    assert path.with_suffix('.json.corrupt').exists()
    assert caplog.records
    for _ in range(202):
        assert append_summary(snapshot, path=path)
    rows = read_summaries(path)
    assert len(rows) == 200
    assert rows[-1]['total_size'] == 120
    assert 'C:' not in path.read_text(encoding='utf-8')
    assert json.loads(path.read_text(encoding='utf-8')) == rows


def test_summary_write_failure_never_raises(tmp_path):
    assert not append_summary(ScanSnapshot(), path=tmp_path)  # a directory


def test_snapshot_preserves_deletion_facts_without_mutating_items():
    item = ScanItem('file', 12, False, selected=False, keep_root=False, keep_newer_than_hours=48)
    snapshot = from_results({'System': {'unresearched': ScanResult([item])}})
    row = snapshot.rows[0]
    assert (row.is_dir, row.keep_root, row.keep_newer_than_hours) == (False, False, 48)
    assert not item.selected


def test_snapshot_preserves_catalog_description_for_unresearched_entries():
    def scan_unresearched():
        return None
    scan_unresearched.spec = SimpleNamespace(id='unresearched', label='Test', category='system',
                                            safety='safe', description='Catalog fallback')
    row = from_results({'System': {scan_unresearched: ScanResult([ScanItem('path', 1, True)])}}).rows[0]
    assert row.description == 'Catalog fallback'
