"""Shared facts: evidence and identity are never replaced by guesses."""
from datetime import datetime, timezone
from types import SimpleNamespace
import time

import pytest
from PyQt6.QtCore import Qt, QThreadPool

from core.app_facts import AppFactsIndex, AppKey, last_used_text, tooltip
from modules.app_buster.engine import app_info, model as m
from modules.game_mode.engine.app_usage import Evidence


def record(name='Autodesk Access', **kw):
    return m.AppRecord(key=name, name=name, type=m.DESKTOP, **kw)


def drain(qapp):
    for _ in range(20):
        qapp.processEvents()
        QThreadPool.globalInstance().waitForDone(100)
        qapp.processEvents()
        time.sleep(.005)


def test_matching_precedence():
    records = [record('family', family='pkg_pid'), record('registry', registry_key=r'HKLM\A'),
               record('winget', winget_id='Vendor.App'), record('folder', install_location=r'C:\App'),
               record('exact', publisher='Vendor')]
    index = AppFactsIndex(records)
    key = AppKey('exact', 'Vendor', r'C:\App', family='pkg_pid',
                 registry_key=r'HKLM\A', winget_id='Vendor.App')
    from dataclasses import replace
    for name, field in [('family', 'family'), ('registry', 'registry_key'),
                        ('winget', 'winget_id'), ('folder', 'install_location')]:
        assert index._match(key).name == name
        key = replace(key, **{field: ''})
    assert index._match(key).name == 'exact'


def test_duplicate_name_or_shared_folder_does_not_pick_arbitrary_product():
    index = AppFactsIndex([record('Same', publisher='A', install_location=r'C:\Shared'),
                           record('Same', publisher='B', install_location=r'C:\Shared')])
    assert index._match(AppKey('Same')) is None
    assert index._match(AppKey('Other', install_location=r'C:\Shared')) is None
    assert index._match(AppKey('Same', publisher='B')).publisher == 'B'
    assert index._match(AppKey('Same', publisher='C')) is None


@pytest.mark.parametrize('name', ['OneNote', 'OneDrive', 'Teams', 'Word', 'Excel', 'Outlook',
                                 'Google Chrome', 'Brave', 'Stremio', 'Ollama', 'eSpeak NG',
                                 'Sysmon', 'AMD Software', 'amd3dvcache', 'Bitdefender',
                                 'MSTeams', 'MicrosoftTeams'])
def test_owner_protected_always_keep(name):
    rec = record(name, removable=False)
    facts = AppFactsIndex([rec]).lookup(AppKey(name))
    assert facts.verdict == 'Keep'
    assert 'protected' in facts.breaks


def test_unknown_uses_only_installer_facts():
    facts = AppFactsIndex().lookup(AppKey('Unrecognised 438', publisher='Example',
                                         install_location=r'C:\Example'))
    assert facts.verdict == 'Unknown'
    assert 'not in the catalog' in tooltip(facts)
    assert facts.confidence == 'from the installer only'
    assert 'Example' in facts.what and r'C:\Example' in facts.does


def test_refused_usage_is_not_never_or_unused():
    facts = AppFactsIndex(errors=['BAM access denied']).lookup(AppKey('Unrecognised'))
    assert facts.last_used is None
    assert 'could not read' in last_used_text(facts)
    assert 'never' not in last_used_text(facts)


@pytest.mark.parametrize('enumerator', ['_values', '_subkeys'])
def test_usage_registry_enumeration_refusal_is_not_end_of_list(monkeypatch, enumerator):
    import winreg
    from modules.game_mode.engine.app_usage import RegistryBackend
    api = 'EnumValue' if enumerator == '_values' else 'EnumKey'

    def refused(*args):
        error = PermissionError('access denied')
        error.winerror = 5
        raise error

    monkeypatch.setattr(winreg, api, refused)
    with pytest.raises(PermissionError):
        list(getattr(RegistryBackend, enumerator)(object()))

    def end(*args):
        error = OSError('no more items')
        error.winerror = 259
        raise error

    monkeypatch.setattr(winreg, api, end)
    assert list(getattr(RegistryBackend, enumerator)(object())) == []


def test_security_vendors_are_all_keep():
    from core.security_vendors import SECURITY_VENDORS
    for vendor in SECURITY_VENDORS:
        assert AppFactsIndex().lookup(AppKey('Endpoint agent', publisher=vendor)).verdict == 'Keep'


def test_protected_app_still_gets_usage_and_install_dates():
    when = datetime(2026, 9, 4, tzinfo=timezone.utc)
    rec = record('Chrome', installed=when, icon_path=r'C:\Chrome\chrome.exe')
    evidence = [Evidence(r'path:c:\chrome\chrome.exe', when, 'UserAssist')]
    facts = AppFactsIndex([rec], evidence).lookup(AppKey('Chrome'))
    assert facts.last_used == when and facts.installed == when
    assert facts.last_used_source == 'UserAssist'


def test_build_scans_once_for_simultaneous_screens(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from modules.app_buster.engine import scan
    import core.app_facts as af
    calls = []
    monkeypatch.setattr(AppFactsIndex, '_cached', None)
    monkeypatch.setattr(scan, 'scan', lambda: calls.append('scan') or SimpleNamespace(rows=[], problems=[]))
    monkeypatch.setattr(af, 'read_usage', lambda: ([], []))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: AppFactsIndex.build(), range(4)))
    assert calls == ['scan'] and all(i is results[0] for i in results)


def test_failed_build_is_cached_and_ui_shows_could_not_read(qapp, monkeypatch):
    from modules.app_buster.engine import scan
    from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem
    from ui.app_facts import bind_row
    calls = []

    def refused():
        calls.append(1)
        raise PermissionError('catalog refused')

    monkeypatch.setattr(AppFactsIndex, '_cached', None)
    monkeypatch.setattr(scan, 'scan', refused)
    table = QTableWidget(1, 1)
    table.setHorizontalHeaderLabels(['Name'])
    table.setItem(0, 0, QTableWidgetItem('Example'))
    bind_row(table, 0, AppKey('Example'))
    drain(qapp)
    for col in range(1, 4):
        assert 'could not read' in table.item(0, col).text()
    with pytest.raises(RuntimeError, match='catalog refused'):
        AppFactsIndex.build()
    assert calls == [1]


def test_date_cells_sort_dates_not_relative_age_text(qapp, monkeypatch):
    from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem
    from ui.app_facts import bind_row
    recent = datetime(2026, 10, 22, tzinfo=timezone.utc)
    older = datetime(2026, 9, 4, tzinfo=timezone.utc)
    rows = [record('Old', installed=older, icon_path=r'C:\Old\old.exe'),
            record('Recent', installed=recent, icon_path=r'C:\Recent\recent.exe')]
    evidence = [Evidence(r'path:c:\old\old.exe', older, 'UserAssist'),
                Evidence(r'path:c:\recent\recent.exe', recent, 'BAM')]
    monkeypatch.setattr(AppFactsIndex, '_cached', AppFactsIndex(rows, evidence))
    table = QTableWidget(2, 1)
    table.setHorizontalHeaderLabels(['Name'])
    for row, rec in enumerate(rows):
        table.setItem(row, 0, QTableWidgetItem(rec.name))
        bind_row(table, row, rec)
    drain(qapp)
    table.setSortingEnabled(True)
    for column in (1, 2):
        table.sortItems(column, Qt.SortOrder.DescendingOrder)
        assert table.item(0, 0).text() == 'Recent'


def test_refresh_discards_old_results_and_preserves_identity(qapp, monkeypatch):
    from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem
    from ui.app_facts import bind_row
    table = QTableWidget(1, 1)
    table.setHorizontalHeaderLabels(['Name'])
    table.setItem(0, 0, QTableWidgetItem('Old'))
    bind_row(table, 0, AppKey('Old'))
    qapp.processEvents()  # First Worker is now queued or running.
    table.setItem(0, 0, QTableWidgetItem('New'))
    bind_row(table, 0, AppKey('New'))
    drain(qapp)
    assert 'New' in table.item(0, 0).toolTip()
    assert 'Old' not in table.item(0, 0).toolTip()


def test_autodesk_all_names_have_researched_about_and_verdict():
    from modules.app_buster.engine.autodesk_info import ENTRIES
    names = ['Autodesk AutoCAD 2027 - English', 'AutoCAD 2027 - English',
             'Autodesk Inventor Professional 2027 - English', 'ACAD Private',
             'Autodesk Inventor Private 2027', 'Autodesk Inventor Private 2027 Language Pack - English',
             'Autodesk Inventor Content Center Libraries 2027 (Desktop Content)',
             'Autodesk Inventor Electrical Catalog Browser CA 2027', 'Autodesk Genuine Service',
             'Autodesk Access', 'Autodesk App Manager', 'Autodesk Featured Apps', 'Autodesk CER',
             'Autodesk Interoperability Engine Manager', 'AutoCAD Open in Desktop',
             'Autodesk AutoCAD MCP Server']
    # 15 components (AutoCAD has two installer display names) + 2 product-update patterns.
    assert len(ENTRIES) == 17
    for name in names:
        rec = record(name)
        assert app_info.curated(rec) is not None, name
        facts = AppFactsIndex([rec]).lookup(AppKey(name))
        assert facts.confidence == 'researched' and facts.verdict != 'Unknown', name


def assert_table(table, qapp, name_column=0, safety=True):
    drain(qapp)
    labels = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    for label in ['Installed', 'Last used'] + (['Uninstall safety'] if safety else []):
        assert label in labels
        assert table.item(0, labels.index(label)).text()
    assert 'What it is:' in table.item(0, name_column).toolTip()


def entry():
    from modules.software_inventory.software_reader import SoftwareEntry
    return SoftwareEntry('Example', '1', 'Vendor', '2026-09-04', '', '64-bit', 'registry')


def test_dashboard_and_software_inventory(qapp):
    from modules.dashboard.installed_apps_tab import InstalledAppsTab
    from modules.software_inventory.software_module import _SoftwarePane
    dashboard = InstalledAppsTab()
    dashboard._apply([entry()])
    assert_table(dashboard._table, qapp)
    pane = _SoftwarePane()
    pane.set_entries([entry()])
    assert_table(pane.table, qapp)
    pane.table.selectRow(0)
    assert 'Uninstall safety:' in pane.detail.toPlainText()


def test_app_buster_model_cards_and_details(qapp):
    from modules.app_buster.app_list import AppModel
    from modules.app_buster.engine import views
    model = AppModel()
    model.set_rows([record()])
    drain(qapp)
    headers = [model.headerData(c, Qt.Orientation.Horizontal) for c in range(model.columnCount())]
    assert {'Installed', 'Last used', 'Uninstall safety'} <= set(headers)
    assert 'What it is:' in model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
    assert 'Last used' in views.subline(model.rows[0], model.fields)
    assert 'Uninstall safety' in views.about_html(model.rows[0])


def test_tweaks_installed_lists_keep_checkboxes_and_catalog_separate(qapp):
    from modules.tweaks.tweaks_module import AppManagerTab
    from modules.tweaks.app_catalog import WingetApp
    widget = AppManagerTab(None, None)
    widget.populate_installed({'Example.Package'})
    widget.populate_installed_desktop([WingetApp('Example', 'Vendor.Example', '1', '')])
    drain(qapp)
    for view in (widget._installed_list, widget._desktop_list):
        item = view.item(0)
        assert 'Installed:' in item.text() and 'Last used:' in item.text()
        assert 'Uninstall safety:' in item.text() and 'What it is:' in item.toolTip()
    assert not widget._remove_queue and not widget._remove_winget_queue
    widget._desktop_list.item(0).setCheckState(Qt.CheckState.Checked)
    assert widget._remove_winget_queue == {'Vendor.Example'}


def test_updates_has_dates_and_tooltip_only(qapp):
    from modules.updates.updates_module import _AppUpdatesTab
    from modules.updates.winget_updater import AppUpdate
    widget = _AppUpdatesTab()
    widget._updates = [AppUpdate('Example', 'Vendor.Example', '1', '2', 'winget')]
    widget._render_table()
    assert_table(widget._table, qapp, safety=False)
    labels = [widget._table.horizontalHeaderItem(c).text() for c in range(widget._table.columnCount())]
    assert 'Uninstall safety' not in labels and 'Installed version' in labels


def test_store_apps(qapp, monkeypatch):
    from modules.store_apps.store_apps_module import StoreAppsModule
    from test_debloat_module import _FakeApp
    module = StoreAppsModule()
    module.on_start(_FakeApp())
    module.create_widget()
    monkeypatch.setattr(module, '_start_size_scan', lambda: None)
    module._on_apps_loaded([{'Name': 'Example.Package', 'Version': '1'}], None)
    assert_table(module._table, qapp)


def test_debloat_apps(qapp, monkeypatch):
    from test_debloat_module import _module
    module = _module()
    monkeypatch.setattr(module, '_load_debloat_entries', lambda: {
        'example': {'id': 'example', 'name': 'Example', 'package': 'Example.Package'}})
    monkeypatch.setattr(module, '_install_location_by_package', lambda: {})
    module._populate_apps_table(['Example.Package'], {})
    assert_table(module._apps_table, qapp, name_column=1)


def test_game_mode_usage_has_about_and_safety_tooltip(qapp):
    from modules.game_mode.optimise_tab import OptimiseTab
    from modules.game_mode.engine.app_usage import AppUsage, UsageReport, UNKNOWN
    widget = OptimiseTab(None, optimiser=SimpleNamespace())
    usage = UsageReport(apps=[AppUsage('Example', 'Desktop app', UNKNOWN, None, '', '')])
    widget._fill_extras({'usage': usage})
    drain(qapp)
    tip = widget._usage_tree.topLevelItem(0).toolTip(0)
    assert 'What it does:' in tip and 'Uninstall safety:' in tip


def test_autodesk_update_patches_are_researched_even_outside_the_scan():
    # App Buster's scan hides update patches under their product, so the
    # Software Inventory rows for them reach lookup() with no matching record.
    index = AppFactsIndex([])
    for name in ('Autodesk AutoCAD 2027.1 Update', 'Inventor Professional 2027.1 Update'):
        facts = index.lookup(AppKey(name=name))
        assert facts.confidence == 'researched' and facts.verdict == 'Keep', name
        assert 'rolls' in facts.breaks
