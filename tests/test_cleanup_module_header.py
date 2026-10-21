"""WP12 integration: no live scans or deletion during header tests."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt, QCoreApplication, QEvent
from PyQt6.QtWidgets import QLabel

from modules.cleanup.cleanup_module import CleanupModule
from modules.cleanup.cleanup_scanner import ScanItem, ScanResult
from modules.cleanup.components.detail_pane import DetailPane
from modules.cleanup.components.item_rows import RowFacts
from modules.cleanup.components.drive_summary_bar import DriveSummaryBar


@pytest.fixture
def module(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from modules.cleanup.tabs._scan_tab import _ScanTab
    from modules.cleanup.tabs._browser_tab import _BrowserCleanupTab
    from modules.cleanup.tabs._discover_tab import _DiscoverTab
    from modules.cleanup.tabs._history_tab import _HistoryTab
    for cls in (QuickCleanupTab, _ScanTab, _BrowserCleanupTab):
        monkeypatch.setattr(cls, 'auto_scan', lambda self: None)
    monkeypatch.setattr(_HistoryTab, 'refresh', lambda self: None)
    monkeypatch.setattr(QuickCleanupTab, '_start_facts_job', lambda self: None)
    monkeypatch.setattr(QuickCleanupTab, '_refresh_trend', lambda self: None)
    m = CleanupModule()
    m.on_start(SimpleNamespace(config=object()))
    widget = m.create_widget()
    yield m
    m.on_stop()
    widget.deleteLater()
    # processEvents alone does not drain DeferredDelete without an event loop.
    # Delete our complete tree before another test reapplies the global QSS.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_header_preset_and_widget_navigation(module):
    m = module
    assert m._clean_now_btn.text() == 'Clean safe items (Light)'
    m._quick._preset_combo.setCurrentIndex(m._quick._preset_combo.findData('thorough'))
    assert 'Thorough' in m._clean_now_btn.text()
    assert m._tabs.tabText(m._tabs.indexOf(m._app_tab)) == 'App && Game Caches'
    m._tabs.setTabText(m._tabs.indexOf(m._app_tab), 'A different caption')
    m._on_category_clicked('app')
    assert m._tabs.currentWidget() is m._app_tab
    assert m._recommendations._config is m.app.config
    assert m._export_btn.toolTip()


def test_snapshot_feeds_recommendations_summary_and_report(module, monkeypatch, tmp_path, qapp):
    from modules.cleanup.cleanup_scanner.scan_snapshot import ScanSnapshot
    from modules.cleanup import cleanup_reports
    calls = []
    monkeypatch.setattr(module._recommendations, 'refresh', calls.append)
    monkeypatch.setattr(ScanSnapshot, 'append_summary', lambda self: True)
    snapshot = ScanSnapshot(preset='light')
    module._quick.snapshot_ready.emit(snapshot)
    module._quick._snapshot = snapshot
    module._quick._reco_pending = True
    qapp.processEvents()
    assert calls == [snapshot]
    target = tmp_path / 'report.html'
    def export(snap, **kwargs):
        assert snap.rows == snapshot.rows and snap.preset == snapshot.preset
        assert kwargs['recommendations'] == module._recommendations.recommendations()
        return target, target.with_suffix('.csv')
    monkeypatch.setattr(cleanup_reports, 'export_scan', export)
    monkeypatch.setattr('os.startfile', lambda path: calls.append(str(path)))
    module._export_report()
    assert calls[-1] == str(target)


def test_recommendation_selects_only_named_ids_and_sets_age(module):
    def scan_temp_files(age=0):
        return ScanResult()
    def scan_other(age=0):
        return ScanResult()
    tab = module._sys_tab
    tab._scanners = {scan_temp_files: ('Temp', 'safe'), scan_other: ('Other', 'safe')}
    a = ScanResult(items=[ScanItem(r'C:\Temp\one', 5, False)])
    b = ScanResult(items=[ScanItem(r'C:\Temp\two', 6, False)])
    tab.populate(ScanResult(items=a.items + b.items), {scan_temp_files: a, scan_other: b})
    module._navigate_recommendation('system', ['temp_files'], 0)
    assert [i.path for i in tab._get_selected_items() if i.selected] == [r'C:\Temp\one']
    module._navigate_recommendation('system', ['temp_files'], 7)
    assert tab._age_spin.value() == 7


def test_long_path_does_not_set_detail_minimum(qapp):
    pane = DetailPane()
    path = 'C:\\' + 'long_folder_' * 100
    pane.set_row(RowFacts(label='Cache', path=path))
    pane.resize(420, 500)
    pane.show()
    qapp.processEvents()
    labels = [label for label in pane.findChildren(QLabel) if label.toolTip() == path]
    assert len(labels) == 1
    assert labels[0].minimumSizeHint().width() < 100
    assert pane.minimumSizeHint().width() < 500
    assert not labels[0].wordWrap()
    pane.close()


def test_drive_severity_and_last_line(qapp, monkeypatch):
    from modules.cleanup import cleanup_history
    bar = DriveSummaryBar()
    gb = 1024 ** 3
    bar._show_drives([('C:\\', 4 * gb, 100 * gb), ('D:\\', 12 * gb, 100 * gb),
                      ('E:\\', 50 * gb, 100 * gb)])
    assert [b.property('severity') for b in bar.bars] == ['error', 'warning', 'success']
    assert all(b.toolTip() and b.width() <= 160 for b in bar.bars)
    monkeypatch.setattr(cleanup_history, 'recent_deleted', lambda **kw: [{'at': '2026-10-21'}])
    monkeypatch.setattr(cleanup_history, 'summarise', lambda entry: 'locked ' * 100)
    bar._refresh_last()
    bar.resize(500, 90)
    bar.show()
    qapp.processEvents()
    assert not bar.last.wordWrap()
    assert 'locked ' * 100 in bar.last.toolTip()
    bar.close()


def test_module_fits_standard_window_and_queued_refresh_cannot_restart_after_cancel(module, qapp, monkeypatch):
    from modules.cleanup.cleanup_scanner.scan_snapshot import ScanSnapshot
    calls = []
    monkeypatch.setattr(ScanSnapshot, 'append_summary', lambda self: True)
    monkeypatch.setattr(module._quick, '_refresh_recommendations', lambda: calls.append(1))
    window = module._tabs.window()
    window.resize(1280, 900)
    window.show()
    module._on_snapshot_ready(ScanSnapshot())
    module.on_deactivate()
    qapp.processEvents()
    assert window.size().width() == 1280 and window.size().height() == 900
    assert calls == []
    window.close()


def test_curated_scanners_never_relax_catalog_safety():
    from modules.cleanup.cleanup_module import _short_labels
    def scan_test(age=0): pass
    scan_test.spec = SimpleNamespace(short_label='Researched cache', safety='caution')
    assert _short_labels({scan_test: ('Old label', 'safe')})[scan_test] == ('Researched cache', 'caution')
    scan_test.spec.safety = 'safe'
    assert _short_labels({scan_test: ('Old label', 'danger')})[scan_test][1] == 'danger'


def test_cancellation_reaches_every_owner(module, monkeypatch):
    calls = []
    for name in ('_recommendations', '_history', '_discover', '_duplicates', '_browser'):
        owner = getattr(module, name)
        method = '_cancel_all' if hasattr(owner, '_cancel_all') else 'cancel'
        monkeypatch.setattr(owner, method, lambda n=name: calls.append(n), raising=False)
    module.on_deactivate()
    assert set(calls) == {'_recommendations', '_history', '_discover', '_duplicates', '_browser'}


def test_badge_height_is_natural_on_two_line_rows(qapp):
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QFontMetrics
    from PyQt6.QtWidgets import QStyleOptionViewItem
    from modules.cleanup.components.safety_delegate import ItemRowModel, SafetyDelegate
    class Painter:
        def __getattr__(self, name):
            return lambda *args: None
        def drawRoundedRect(self, rect, *args):
            self.pill = rect
    painter = Painter()
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 140, 70)
    option.fontMetrics = QFontMetrics(qapp.font())
    model = ItemRowModel([RowFacts(label='Cache', subtitle='A second line')])
    SafetyDelegate().paint(painter, option, model.index(0, 3))
    assert painter.pill.height() == option.fontMetrics.height() + 8
    assert abs(painter.pill.center().y() - option.rect.center().y()) <= 1


def test_frozen_manifest_and_check_missing_stale_content(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    import sys
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from pyinstaller_common import HIDDEN_IMPORTS, get_datas
    spec = importlib.util.spec_from_file_location('wp12_frozen_check', root / 'tools/check_frozen_cleanup.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    expected, knowledge = checker.expected_content()
    new = {name for name in expected if name.endswith(('knowledge', 'scan_snapshot', 'recommend',
        'item_rows', 'detail_pane', 'safety_delegate', 'charts', 'kpi_tiles', 'recommendations_panel',
        'cleanup_reports', 'optimise', 'space', 'app_usage', 'optimise_tab')) or '.duplicates' in name}
    assert new <= set(HIDDEN_IMPORTS)
    assert knowledge and any('definitions/knowledge' in target for _, target in get_datas(str(root)))
    source = tmp_path / 'facts.json'
    source.write_text('{"a":1}', encoding='utf-8')
    monkeypatch.setattr(checker, 'expected_content', lambda: ({'required'}, {'facts.json': source}))
    class Archive:
        toc = {'PYZ.pyz': None, 'facts.json': None}
        modules = {'required': None}
        payload = b'{"a":1}'
        def __init__(self, path): pass
        def open_embedded_archive(self, name): return SimpleNamespace(toc=self.modules)
        def extract(self, name): return self.payload
    from PyInstaller.archive import readers
    monkeypatch.setattr(readers, 'CArchiveReader', Archive)
    assert checker.check('fake.exe') == 0
    Archive.modules = {}
    assert checker.check('fake.exe') == 1
    Archive.modules = {'required': None}
    Archive.payload = b'{"a":2}'
    assert checker.check('fake.exe') == 1
    Archive.toc = {'PYZ.pyz': None}
    assert checker.check('fake.exe') == 1
