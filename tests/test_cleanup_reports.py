import csv
import math
import re

from modules.cleanup import cleanup_reports as reports
from modules.cleanup.cleanup_scanner.scan_snapshot import ScanSnapshot, SnapshotRow


def snapshot():
    return ScanSnapshot(rows=(SnapshotRow('test', '<label>', 'System', 'system', 'safe', 'caution',
                                          r'C:\<img onerror>\cache', 100, True),), preset='<preset>')


def test_offline_scan_html_and_csv(tmp_path):
    page = reports.scan_html(snapshot(), machine='<machine>', recommendations=[{'title': '<reco>'}],
                             not_offered=[{'label': '<held>', 'reason': '<reason>'}])
    assert '<script' not in page
    assert '<img onerror>' not in page
    for escaped in ('&lt;label&gt;', '&lt;img onerror&gt;', '&lt;preset&gt;', '&lt;machine&gt;', '&lt;reco&gt;', '&lt;reason&gt;'):
        assert escaped in page
    assert '<svg' in page
    assert not re.search(r'(?:src|href)=["\']https?://', page)
    html_path, csv_path = reports.export_scan(snapshot(), directory=tmp_path)
    assert html_path.exists() and csv_path.exists()
    with csv_path.open(encoding='utf-8', newline='') as stream:
        assert list(csv.reader(stream))[1][6] == r'C:\<img onerror>\cache'


def test_csv_formula_escaping(tmp_path):
    row = SnapshotRow('test', '=cmd', 'System', 'system', 'safe', 'safe', '@evil', 1, True)
    _, path = reports.export_scan(ScanSnapshot(rows=(row,)), directory=tmp_path)
    assert "'=cmd" in path.read_text(encoding='utf-8')
    assert "'@evil" in path.read_text(encoding='utf-8')


def test_angles_and_history_legacy():
    angles = reports.slice_angles([2, 3, 4])
    assert math.isclose(sum(angles), 360)
    assert reports.slice_angles([0]) == []
    page = reports.history_html([{'at': '<date>', 'bytes': 100, 'count': 1,
                                 'largest': [['<path>', 100]], 'refused': ['<old-refusal>']},
                                {'at': 'now', 'bytes': 2, 'owners': {'<process>': 1},
                                 'locked': ['<locked>'], 'denied': ['<denied>'], 'cancelled': True}])
    for value in ('date', 'path', 'old-refusal', 'process', 'locked', 'denied'):
        assert f'&lt;{value}&gt;' in page
    assert 'Cancelled' in page
    assert 'In use: Not recorded' in page


def test_reports_describe_files_and_summary_history_without_invented_folder_behavior():
    row = SnapshotRow('unknown', 'A file', 'System', 'system', 'safe', 'safe', 'file.log', 10,
                      True, is_dir=False, keep_root=False, keep_newer_than_hours=48)
    page = reports.scan_html(ScanSnapshot(rows=(row,)))
    assert 'This file is deleted.' in page
    assert 'root kept' not in page
    history = reports.history_html([], summaries=[{'at': '2026-10-01', 'total_size': 10},
                                                 {'at': '2026-10-03', 'total_size': 20}])
    assert '<svg' in history


def test_retention_exact_names_only(tmp_path):
    for i in range(35):
        (tmp_path / f'cleanup-scan-20261021-{i:06d}.html').write_text('test', encoding='utf-8')
    for name in ('other.log', 'cleanup-scan-notes.html', 'cleanup-history-20261021-123456.html.bak'):
        (tmp_path / name).write_text('keep', encoding='utf-8')
    foreign = tmp_path / 'cleanup-scan-20261021-235959.html'
    foreign.mkdir()
    reports.retain_reports(tmp_path)
    assert len(list(tmp_path.glob('cleanup-scan-*.html'))) == 32  # 30 reports, notes and directory
    assert (tmp_path / 'other.log').exists()
    assert (tmp_path / 'cleanup-scan-notes.html').exists()
    assert foreign.is_dir()
