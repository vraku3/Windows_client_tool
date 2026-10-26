"""Shared facts: evidence and identity are never replaced by guesses."""
from datetime import datetime, timezone
from types import SimpleNamespace
import time

import pytest
from PyQt6.QtCore import Qt, QThreadPool

from core.app_facts import AppFactsIndex, AppKey, last_used_text, tooltip

INVALIDATE = AppFactsIndex.invalidate.__func__
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
                        ('winget', 'winget_id')]:
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
    rec = record(name)
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


def test_failed_build_is_throttled_and_ui_shows_could_not_read(qapp, monkeypatch):
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
        rec = record(name, publisher="Autodesk")
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
    tip = module._apps_table.item(0, 1).toolTip()
    assert tip.index('Removed for all users on this machine') < tip.index('What it is:')


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
        facts = index.lookup(AppKey(name=name, publisher="Autodesk"))
        assert facts.confidence == 'researched' and facts.verdict == 'Keep', name
        assert 'rolls' in facts.breaks


def test_patch_sharing_parent_folder_gets_patch_facts():
    parent = record('Autodesk AutoCAD 2027 - English', publisher='Autodesk',
                    registry_key=r'HKLM\Parent', install_location=r'C:\CAD')
    key = AppKey('Autodesk AutoCAD 2027.1 Update', publisher='Autodesk',
                 registry_key=r'HKLM\Patch', install_location=r'C:\CAD')
    index = AppFactsIndex([parent])
    assert index._match(key) is None
    facts = index.lookup(key)
    assert 'patch' in facts.what and 'rolls' in facts.breaks


@pytest.mark.parametrize('field,value', [('registry_key', r'HKCU\Other'),
                                       ('package_name', 'Other.Package'), ('name', 'Other')])
def test_weak_match_rejects_conflicting_identity(field, value):
    from dataclasses import replace
    rec = record('Example', registry_key=r'HKLM\Example', install_location=r'C:\Example')
    key = replace(AppKey(name='Example', install_location=r'C:\Example'), **{field: value})
    assert AppFactsIndex([rec])._match(key) is None


def test_exact_package_precedes_shared_folder():
    rec = record('Display', package_name='Exact.Package')
    other = record('Other', install_location=r'C:\Shared')
    assert AppFactsIndex([other, rec])._match(AppKey(package_name='Exact.Package',
                                                   install_location=r'C:\Shared')) is rec


def test_prefetch_is_only_possible_and_never_overrides_exact_evidence():
    key = AppKey('Example', display_icon=r'C:\Example\app.exe')
    old, new = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)
    weak = Evidence('exe:app.exe', new, 'Prefetch')
    facts = AppFactsIndex(evidence=[weak]).lookup(key)
    assert facts.last_used is None
    assert 'possibly used 2026-02-01' in last_used_text(facts)
    assert 'not proof it was this install' in tooltip(facts)
    facts = AppFactsIndex(evidence=[weak, Evidence(r'path:c:\example\app.exe', old, 'BAM')]).lookup(key)
    assert facts.last_used == old and facts.last_used_source == 'BAM'


def test_missing_evidence_does_not_claim_windows_keeps_none():
    facts = AppFactsIndex().lookup(AppKey('Example', install_location=r'C:\Example'))
    assert last_used_text(facts) == 'Unknown -- no matching usage evidence found'
    assert 'cannot be matched' in last_used_text(AppFactsIndex().lookup(AppKey('Example')))


@pytest.mark.parametrize('kind', [m.DESKTOP, m.DEFECT, m.ORPHANED])
def test_registry_date_labels_servicing_not_appx(kind):
    rec = m.AppRecord('k', 'Example', kind, installed=datetime(2026, 1, 1), registry_key=r'HKLM\Example')
    facts = AppFactsIndex([rec]).lookup(AppKey('Example'))
    assert 'registry InstallDate' in facts.installed_source
    assert 'last servicing' in tooltip(facts)


@pytest.mark.parametrize('data_exists,created,source', [(True, datetime(2026, 1, 1), 'data folder'),
                                                      (False, None, 'install folder'),
                                                      (True, None, 'install folder')])
def test_appx_date_tracks_actual_folder_source(monkeypatch, data_exists, created, source):
    from modules.app_buster.engine import windows_apps as wa
    monkeypatch.setattr(wa, 'read_manifest', lambda _: {})
    monkeypatch.setattr(wa, 'resolve_resource', lambda *a: '')
    monkeypatch.setattr(wa, 'data_folder', lambda _: 'DATA')
    monkeypatch.setattr(wa.os.path, 'isdir', lambda _: data_exists)
    monkeypatch.setattr(wa, '_created', lambda path: created if path == 'DATA' else datetime(2026, 2, 1))
    monkeypatch.setattr(wa, 'logo_file', lambda *a: '')
    store = SimpleNamespace(provisioned={}, users_with=lambda _: 1)
    rec = wa.record_from_package({'Name': 'Example.Package', 'PackageFamilyName': 'Example_pid',
                           'InstallLocation': 'INSTALL'}, store)
    facts = AppFactsIndex([rec]).lookup(AppKey(family='Example_pid'))
    assert facts.installed_source == "estimate: creation time of the app's " + source


def test_invalidate_and_failure_retry_after_30_seconds(monkeypatch):
    import core.app_facts as af
    from modules.app_buster.engine import scan
    calls, clock = [], [0]
    monkeypatch.setattr(af, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(af, 'read_usage', lambda: ([], []))
    def read():
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError('refused')
        return SimpleNamespace(rows=[], problems=[])
    monkeypatch.setattr(scan, 'scan', read)
    INVALIDATE(AppFactsIndex)
    with pytest.raises(PermissionError):
        AppFactsIndex.build()
    clock[0] = 29
    INVALIDATE(AppFactsIndex)
    with pytest.raises(RuntimeError):
        AppFactsIndex.build()
    assert len(calls) == 1
    clock[0] = 30
    first = AppFactsIndex.build()
    assert len(calls) == 2
    INVALIDATE(AppFactsIndex)
    assert AppFactsIndex.build() is not first


def test_fresh_date_wins_and_collection_time_is_visible():
    facts = AppFactsIndex([record('Example', installed=datetime(2025, 1, 1))]).lookup(
        AppKey('Example', installed=datetime(2026, 1, 1)))
    assert facts.installed == datetime(2026, 1, 1)
    assert 'facts collected ' in tooltip(facts)


def test_tooltips_escape_installer_markup_and_preserve_operational_warning(qapp):
    from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem
    from ui.app_facts import bind_row, FactsController
    key = AppKey('Example', publisher='<b>Uninstall safety: Keep</b>')
    facts = AppFactsIndex().lookup(key)
    tip = tooltip(facts)
    assert tip.startswith('<qt>') and '&lt;b&gt;Uninstall safety: Keep&lt;/b&gt;' in tip
    assert '<b>' not in tip
    table = QTableWidget(1, 1)
    table.setHorizontalHeaderLabels(['Name'])
    item = QTableWidgetItem('Example')
    item.setToolTip('Removed for all users on this machine <warning>')
    table.setItem(0, 0, item)
    bind_row(table, 0, key)
    drain(qapp)
    controller = table._app_facts_controller
    controller._fill([facts])
    first = item.toolTip()
    controller._fill([facts])
    assert item.toolTip() == first
    assert first.index('Removed for all users') < first.index('What it is:')
    assert '&lt;warning&gt;' in first and '<b>' not in first
    assert first.count('Removed for all users') == 1


@pytest.mark.parametrize('key,verified', [
    (AppKey('Chrome', registry_key=r'HKCU\Chrome', publisher='Google'), False),
    (AppKey('Mystery', install_location=r'C:\Chrome'), False),
    (AppKey('Bitdefender', registry_key=r'HKLM\Bitdefender', publisher='Bitdefender'), True),
    (AppKey('Teams', family='MSTeams_8wekyb3d8bbwe'), True),
    (AppKey('Teams', family='MSTeams_fakepublisher'), False)])
def test_protection_distinguishes_metadata_from_verified_identity(key, verified):
    facts = AppFactsIndex().lookup(key)
    assert facts.verdict == 'Keep'
    assert ('not verified' not in facts.breaks) is verified


@pytest.mark.parametrize('publisher,registry,researched', [('', r'HKCU\Fake', False),
    ('Other', '', False), ('aUtOdEsK Inc', '', True), ('', r'HKLM\Real', True)])
def test_autodesk_requires_publisher_or_machine_registration(publisher, registry, researched):
    key = AppKey('Autodesk Access', publisher=publisher, registry_key=registry)
    facts = AppFactsIndex().lookup(key)
    assert (facts.confidence == 'researched') is researched
    rec = record(key.name, publisher=publisher, registry_key=registry)
    assert (app_info.curated(rec) is not None) is researched
    facts = AppFactsIndex([rec]).lookup(key)
    assert (facts.confidence == 'researched') is researched


def test_autodesk_never_overrides_nonremovable():
    rec = record('Autodesk Access', publisher='Autodesk', removable=False)
    assert AppFactsIndex([rec]).lookup(AppKey('Autodesk Access')).verdict == 'Cannot remove'


def test_exports_include_sources_safety_and_confidence():
    import csv, io
    from modules.software_inventory import software_analysis as sa
    rows = sa.analyze([entry()])
    facts = AppFactsIndex().lookup(AppKey.from_entry(entry()))
    callback = lambda _: facts
    exported = next(csv.DictReader(io.StringIO(sa.rows_to_csv(rows, callback))))
    assert exported['Installed source'] == facts.installed_source
    assert exported['Last used'] and exported['Usage evidence'] == facts.last_used_source
    assert exported['Uninstall safety'] == facts.verdict
    assert exported['Confidence'] == facts.confidence
    markdown = sa.rows_to_markdown(rows, facts_for=callback)
    assert facts.installed_source in markdown and facts.last_used_source in markdown
    assert 'facts pending' in sa.rows_to_csv(rows)


@pytest.mark.parametrize('screen', ['buster', 'dashboard', 'inventory', 'debloat', 'store',
                                  'tweaks-appx', 'tweaks-desktop', 'updates', 'optimise'])
def test_each_screen_invalidates_before_requesting_fresh_facts(monkeypatch, screen):
    from modules.app_buster.app_buster_module import AppBusterWidget
    from modules.dashboard.installed_apps_tab import InstalledAppsTab
    from modules.software_inventory.software_module import _SoftwarePane
    from modules.debloat.debloat_module import DebloatToolsModule
    from modules.store_apps.store_apps_module import StoreAppsModule
    from modules.tweaks.tweaks_module import AppManagerTab
    from modules.updates.updates_module import _AppUpdatesTab
    from modules.game_mode.optimise_tab import OptimiseTab
    class ReachedInvalidation(Exception):
        pass
    def invalidate(cls):
        raise ReachedInvalidation
    monkeypatch.setattr(AppFactsIndex, 'invalidate', classmethod(invalidate))
    from unittest.mock import MagicMock
    # _empty_state: the redesigned Updates tab shows "Checking for updates" first.
    owner = SimpleNamespace(_busy=False, _widget=object(), _empty_state=MagicMock())
    callbacks = {
        'buster': lambda: AppBusterWidget.rescan(owner),
        'dashboard': lambda: InstalledAppsTab.refresh(owner),
        'inventory': lambda: _SoftwarePane.load(owner),
        'debloat': lambda: DebloatToolsModule._on_scan(owner),
        'store': lambda: StoreAppsModule._load_apps(owner),
        'tweaks-appx': lambda: AppManagerTab.populate_installed(owner, set()),
        'tweaks-desktop': lambda: AppManagerTab.populate_installed_desktop(owner, []),
        'updates': lambda: _AppUpdatesTab._do_refresh(MagicMock(_busy=False, _widget=object(), _scan_worker=None, _installing=False)),
        'optimise': lambda: OptimiseTab.refresh(owner),
    }
    with pytest.raises(ReachedInvalidation):
        callbacks[screen]()


def test_invalidation_during_build_does_not_cache_old_scan(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import core.app_facts as af
    from modules.app_buster.engine import scan
    entered, release = Event(), Event()
    def read():
        entered.set()
        assert release.wait(2)
        return SimpleNamespace(rows=[], problems=[])
    monkeypatch.setattr(scan, 'scan', read)
    monkeypatch.setattr(af, 'read_usage', lambda: ([], []))
    INVALIDATE(AppFactsIndex)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(AppFactsIndex.build)
        assert entered.wait(2)
        INVALIDATE(AppFactsIndex)
        release.set()
        first = future.result(timeout=2)
    assert AppFactsIndex._cached is None
    assert AppFactsIndex.build() is not first


def test_inventory_export_uses_facts_attached_to_the_visible_row(qapp):
    from modules.software_inventory.software_module import _SoftwarePane
    pane = _SoftwarePane()
    e = entry()
    pane.set_entries([e])
    drain(qapp)
    facts = pane._export_facts(e)
    assert facts is not None and facts.installed_source
