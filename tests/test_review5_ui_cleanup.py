"""Fix brief B: Cleanup UI and offline export regressions."""
import csv
from dataclasses import replace

import pytest
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QMessageBox, QScrollArea

from modules.cleanup.cleanup_scanner import ScanItem, ScanResult


def group_with(item):
    from modules.cleanup.components.category_group import CategoryGroup
    group = CategoryGroup('Cache', lambda: None)
    group.set_result(ScanResult(items=[item], total_size=item.size))
    return group


@pytest.mark.parametrize('outcome', ['result', 'error', 'cancelled'])
def test_scan_during_clean_defers_and_resumes(qapp, monkeypatch, outcome):
    from modules.cleanup.components import category_group as cg
    pending = []
    monkeypatch.setattr(cg.QThreadPool, 'globalInstance', lambda: type(
        'Pool', (), {'start': lambda self, worker: pending.append(worker)})())
    group = group_with(ScanItem(path=r'C:\cache', size=1, is_dir=True))
    scans = []
    group._do_scan = lambda: scans.append(True)
    group._do_clean_all()
    generation = group._generation
    group.scan()
    assert not scans and group._generation == generation
    assert group._rescan_after_clean
    signal = getattr(pending[0].signals, outcome)
    signal.emit(*{'result': [(1, 0, 3 * 1024**3)], 'error': ['refused'], 'cancelled': []}[outcome])
    assert not group._cleaning and not group._rescan_after_clean
    assert scans == [True]
    pending[0].signals.finished.emit()


@pytest.mark.parametrize('module,name', [
    ('components.category_group', 'CategoryGroup'),
    ('components.quick_cleanup_tab', 'QuickCleanupTab'),
    ('tabs._browser_tab', '_BrowserCleanupTab'),
    ('tabs._discover_tab', '_DiscoverTab'),
    ('tabs._large_items_tab', '_LargeItemsTab'),
    ('tabs._scan_tab', '_ScanTab'),
])
def test_byte_signals_preserve_three_gib(qapp, module, name):
    from importlib import import_module
    cls = getattr(import_module('modules.cleanup.' + module), name)
    # Bind the production signal descriptors without starting scans or timers.
    attrs = {'freed_bytes': cls.__dict__['freed_bytes']}
    if 'scan_done' in cls.__dict__:
        attrs['scan_done'] = cls.__dict__['scan_done']
    emitter = type('Emitter', (QObject,), attrs)()
    got = []
    emitter.freed_bytes.connect(got.append)
    emitter.freed_bytes.emit(3 * 1024**3)
    assert got == [3 * 1024**3]
    if hasattr(emitter, 'scan_done'):
        totals = []
        emitter.scan_done.connect(lambda count, size: totals.append((count, size)))
        emitter.scan_done.emit(1, 3 * 1024**3)
        assert totals == [(1, 3 * 1024**3)]


def test_retention_removes_only_matching_csv_companion(tmp_path):
    from modules.cleanup.cleanup_reports import retain_reports
    old = tmp_path / 'cleanup-scan-20260101-010101.html'
    new = tmp_path / 'cleanup-scan-20260102-010101.html'
    for path in [old, old.with_suffix('.csv'), new, new.with_suffix('.csv'),
                 tmp_path / 'cleanup-scan-20260101-010101-extra.csv']:
        path.write_text('report')
    retain_reports(tmp_path, limit=1)
    assert not old.exists() and not old.with_suffix('.csv').exists()
    assert new.exists() and new.with_suffix('.csv').exists()
    assert (tmp_path / 'cleanup-scan-20260101-010101-extra.csv').exists()


def test_retention_keeps_symlink_companion(tmp_path):
    from modules.cleanup.cleanup_reports import retain_reports
    html = tmp_path / 'cleanup-scan-20260101-010101.html'
    html.write_text('report')
    target = tmp_path / 'keep.csv'
    target.write_text('keep')
    try:
        html.with_suffix('.csv').symlink_to(target)
    except OSError:
        pytest.skip('Creating symlinks requires Windows developer mode')
    retain_reports(tmp_path, limit=0)
    assert html.with_suffix('.csv').is_symlink() and target.read_text() == 'keep'


def quick_tab():
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    tab = QuickCleanupTab()
    tab.build(categories=[('temp', 'Temp Files', '#4caf50')], advanced_categories=[])
    return tab


def test_snapshot_uses_checkbox_selection_without_mutating_scan(qapp):
    tab = quick_tab()
    item = ScanItem(path=r'C:\cache', size=1, is_dir=True, safety='caution')
    result = ScanResult(items=[item], total_size=1)
    tab._results = {'temp': result}
    tab._render_results()
    tab._publish_snapshot()
    assert tab.snapshot().rows[0].selected is False
    group = tab._group_by_cid['temp']
    group._tree.topLevelItem(0).child(0).setCheckState(0, Qt.CheckState.Checked)
    tab._publish_snapshot()
    assert tab.snapshot().rows[0].selected is True
    assert item.selected is True


def test_snapshot_and_reports_explain_failed_and_pending_scans(qapp, tmp_path):
    from modules.cleanup.cleanup_reports import export_scan
    tab = quick_tab()
    tab._scan_errors = {'temp': '<access denied>'}
    tab._publish_snapshot()
    assert any(row['label'] == 'Temp Files' and '<access denied>' in row['reason']
               for row in tab.snapshot().not_scanned)
    html, csv_path = export_scan(tab.snapshot(), directory=tmp_path)
    assert 'Not scanned' in html.read_text(encoding='utf-8')
    assert '&lt;access denied&gt;' in html.read_text(encoding='utf-8')
    rows = list(csv.reader(csv_path.open(encoding='utf-8', newline='')))
    assert any('Not scanned' in row and '<access denied>' in row for row in rows)
    tab._scan_errors.clear()
    tab._timed_out_cids = {'temp'}
    tab._publish_snapshot()
    assert any('still scanning' in row['reason'].lower() for row in tab.snapshot().not_scanned)


def test_detail_paths_scroll_with_actions_accessible(qapp):
    from modules.cleanup.components.detail_pane import DetailPane
    from modules.cleanup.components.item_rows import RowFacts
    pane = DetailPane()
    pane.set_row(RowFacts('Many paths', children=tuple(
        RowFacts(str(i), path=rf'C:\cache\{i}') for i in range(40))))
    scroll = pane.findChild(QScrollArea)
    assert scroll is not None and scroll.maximumHeight() <= 300
    pane.resize(800, 600)
    pane.show()
    qapp.processEvents()
    assert scroll.verticalScrollBar().maximum() > 0
    assert len(pane.path_buttons) == 120
    assert all(button.toolTip() and button.accessibleName() for button in pane.path_buttons)
    received = []
    pane.copy_path.connect(received.append)
    pane.path_buttons[-2].click()
    assert received == [r'C:\cache\39']
    pane.close()


@pytest.mark.parametrize('safety', ['caution', 'danger'])
def test_small_risky_clean_requires_confirmation_with_paths_and_risk(qapp, monkeypatch, safety):
    from modules.cleanup.tabs import _scan_tab as st
    from modules.cleanup.cleanup_scanner.catalog import scanner_for
    fn = scanner_for('vscode_crashpad')
    tab = st._ScanTab({fn: ('Crash reports', 'safe')})
    item = ScanItem(path=r'C:\cache\pending-report', size=1, is_dir=False, safety=safety)
    result = ScanResult(items=[item], total_size=1)
    tab.populate(result, {fn: result})
    tab._select_all()  # Review rows deliberately need an explicit tick.
    for i in range(tab._tree.topLevelItemCount()):
        root = tab._tree.topLevelItem(i)
        for row, _holder in tab._location_rows(root):
            row.setCheckState(0, Qt.CheckState.Checked)
    dialogs = []
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: dialogs.append(
        box.text() + box.informativeText() + box.detailedText()) or QMessageBox.StandardButton.Cancel)
    assert st._confirm_large(tab, 1) is False
    assert len(dialogs) == 1 and 'permanently delete' in dialogs[0]
    assert item.path in dialogs[0]
    from modules.cleanup.cleanup_scanner.knowledge import entry_for
    assert entry_for('vscode_crashpad').risk in dialogs[0]


def test_small_safe_clean_keeps_size_gate(qapp, monkeypatch):
    from modules.cleanup.tabs import _scan_tab as st
    tab = st._ScanTab({})
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: pytest.fail('Unexpected confirmation'))
    assert st._confirm_large(tab, 1)
