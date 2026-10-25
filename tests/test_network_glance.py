"""Measured network summaries, including unavailable reads and empty results."""
from types import SimpleNamespace

from modules.network_diagnostics.network_health_module import NetworkHealthModule
from modules.network_diagnostics import network_health as health, dns_client
from modules.hosts_editor.hosts_editor_module import HostsEditorModule
from modules.hosts_editor import hosts_file
from modules.remote_tools.remote_module import RemoteToolsModule
from modules.remote_tools.remote_audit import Row
from modules.shared_resources.shares_module import _RefreshTab
from modules.wifi_analyzer.wifi_module import WifiAnalyzerModule
from modules.wifi_analyzer.wifi_scan import ScanResult, ScanProblem
from modules.network_extras.net_extras_module import NetExtrasModule


def test_health_measures_counts_latency_and_dns_and_filters(qapp):
    module = NetworkHealthModule()
    module.create_widget()
    pane = module._health
    pane._findings = [health.Finding('bad', health.ERROR, 'Bad route'),
                      health.Finding('warn', health.WARNING, 'Slow link')]
    pane._probes = {'gw:192.0.2.1': {'ok': True, 'rtt_ms': 7.5},
                    'dns:192.0.2.53': dns_client.DnsReply('192.0.2.53', 'x', 'A', 2, 'NOERROR', ['1.2.3.4'])}
    pane._update_glance()
    assert pane._glance['errors'].value.text() == '1'
    assert pane._glance['warnings'].value.text() == '1'
    assert pane._glance['gateway'].value.text() == '7.5 ms'
    assert pane._glance['dns'].value.text() == '1 / 1'
    pane._glance['errors'].clicked.emit()
    assert pane._table.rowCount() == 1
    assert pane._table.item(0, 1).text() == 'Bad route'
    pane._failed('Access denied')
    assert all(t.value.text() == '\u2014' for t in pane._glance.values())


def test_hosts_empty_read_and_edit_counts(qapp, monkeypatch):
    module = HostsEditorModule()
    module.create_widget()
    monkeypatch.setattr(hosts_file, 'read_hosts_text', lambda _: hosts_file.HostsText('', False))
    module._load()
    assert module._glance['entries'].value.text() == '0'
    assert module._content.currentWidget() is module._empty
    module._add_entry()
    assert module._glance['entries'].value.text() == '1'
    assert module._glance['blocked'].value.text() == '1'
    assert module._content.currentWidget() is module._table
    module._read = hosts_file.HostsText('', False, error='Access denied')
    module._update_glance()
    assert module._glance['entries'].value.text() == '\u2014'
    assert module._empty.variant == 'error'


def test_remote_counts_do_not_turn_unknown_into_off(qapp):
    module = RemoteToolsModule()
    module.create_widget()
    module._on_exposure([Row('RDP', 'On', 'Listening', 'high', ['NLA off']),
                         Row('SSH', 'Off', 'Not running', 'ok'),
                         Row('WinRM', 'Unknown', 'Access denied', 'info')])
    assert [t.value.text() for t in module._glance.values()] == ['1', '1', '1', '1']
    assert len(module._feature_cards) == 3
    module._exp_details.setChecked(True)
    assert not module._exp_table.isHidden() and module._feature_area.isHidden()
    module._exp_details.setChecked(False)
    assert module._exp_table.isHidden() and not module._feature_area.isHidden()
    module._on_exposure_error('Access denied')
    assert all(t.value.text() == '\u2014' for t in module._glance.values())


def test_shares_empty_and_failed_are_distinct(qapp):
    tab = _RefreshTab(lambda: [], ['Client', 'User', 'Opens'], SimpleNamespace(start=lambda _: None))
    tab._show_rows([])
    assert tab._glance['items'].value.text() == '0'
    assert tab._empty.variant == 'empty'
    tab._show_rows([{'Client': 'pc', 'User': 'alice', 'Opens': '3'}])
    assert tab._glance['items'].value.text() == '1'
    assert tab._glance['clients'].value.text() == '1'
    assert tab._glance['users'].value.text() == '1'
    tab._show_error('Access denied')
    assert all(t.value.text() == '\u2014' for t in tab._glance.values())
    assert tab._empty.variant == 'error'


def test_wifi_connection_signal_and_same_band_congestion(qapp):
    module = WifiAnalyzerModule()
    module.create_widget()
    iface = {'State': 'connected', 'SSID': 'Home', 'BSSID': 'aa', 'Signal': '82%', 'Band': '5 GHz', 'Channel': '36'}
    scan = ScanResult([{'SSID': 'Home', 'BSSID': 'aa', 'Band': '5 GHz', 'Channel': 36},
                       {'SSID': 'Other', 'BSSID': 'bb', 'Band': '5 GHz', 'Channel': 36},
                       {'SSID': 'Six', 'BSSID': 'cc', 'Band': '6 GHz', 'Channel': 36}], [iface], None, None)
    module._show_connection(scan)
    assert module._glance['ssid'].value.text() == 'Home'
    assert module._glance['signal'].value.text() == '82%'
    assert module._signal_bar.value() == 82
    assert module._glance['congestion'].value.text() == '1'
    problem = ScanProblem('refused', 'Access denied', 'denied')
    module._show_connection(ScanResult([], [], problem, problem))
    assert all(t.value.text() == '\u2014' for t in module._glance.values())
    assert module._signal_bar.isHidden()


def test_extras_unknown_and_measured_adapter_proxy_summary(qapp):
    module = NetExtrasModule()
    module.create_widget()
    module._show_overview({'adapters': 2, 'up': 1, 'proxy': False, 'server': ''})
    assert [t.value.text() for t in module._glance.values()] == ['1 / 2', 'Off', 'Direct']
    module._show_overview({'adapters': None, 'up': None, 'proxy': None, 'server': None,
                           'adapter_error': 'Access denied', 'proxy_error': 'Access denied'})
    assert all(t.value.text() == '\u2014' for t in module._glance.values())


def test_shared_exposure_classifies_refusals_as_unknown(qapp, monkeypatch):
    from modules.shared_resources import shares_module, share_audit
    data = share_audit.ShareData(fatal='Access denied')
    monkeypatch.setattr(share_audit, 'collect_cached', lambda **_: data)
    rows = shares_module.get_exposure()
    tab = _RefreshTab(lambda: rows, ['Severity', 'Finding', 'Detail'], SimpleNamespace(start=lambda _: None))
    tab._show_rows(rows)
    assert tab._glance['unknown'].value.text() == '1'
    assert tab._glance['attention'].value.text() == '0'


def test_shared_read_cancel_drops_late_result_and_allows_retry(qapp):
    pending = []
    tab = _RefreshTab(lambda: [], ['Client', 'User', 'Opens'], SimpleNamespace(start=pending.append))
    tab.refresh()
    old = pending[-1]
    tab.cancel_all()
    old.signals.result.emit([{'Client': 'stale'}])
    assert not tab._loaded and not tab._scanning
    assert tab._table.rowCount() == 0
    assert tab._refresh_btn.isEnabled()
    tab.refresh()
    assert len(pending) == 2
    pending[-1].signals.result.emit([{'Client': 'new', 'User': 'alice'}])
    assert tab._glance['items'].value.text() == '1'
    for worker in pending:
        worker.signals.finished.emit()


def test_wifi_disconnected_and_missing_signal_do_not_show_zero(qapp):
    module = WifiAnalyzerModule()
    module.create_widget()
    module._show_connection(ScanResult([], [{'State': 'disconnected'}], None, None))
    assert module._glance['ssid'].value.text() == 'Disconnected'
    assert module._glance['signal'].value.text() == '\u2014'
    module._show_connection(ScanResult([], [{'State': 'connected', 'SSID': 'Home'}], None, None))
    assert module._glance['ssid'].value.text() == 'Home'
    assert module._glance['signal'].value.text() == '\u2014'
    assert module._glance['congestion'].value.text() == '\u2014'


def test_health_missing_probes_are_not_zero_latency_or_dns_success(qapp):
    module = NetworkHealthModule()
    module.create_widget()
    module._health._update_glance()
    assert module._health._glance['gateway'].value.text() == '\u2014'
    assert module._health._glance['dns'].value.text() == '\u2014'


def test_extras_refused_registry_read_preserves_unknown(qapp, monkeypatch):
    from modules.network_extras import net_extras_module as extras
    monkeypatch.setattr(extras.psutil, 'net_if_stats', lambda: {'nic': SimpleNamespace(isup=True)})
    def refused(*args):
        raise PermissionError('Access denied')
    monkeypatch.setattr(extras.winreg, 'OpenKey', refused)
    data = extras._read_overview()
    assert data['proxy'] is None and data['server'] is None
    assert data['adapters'] == 1 and data['up'] == 1
    module = NetExtrasModule()
    module.create_widget()
    module._show_overview(data)
    assert module._glance['proxy'].value.text() == '\u2014'


def test_wifi_replaced_scan_cannot_overwrite_connection_summary(qapp, monkeypatch):
    pending = []
    module = WifiAnalyzerModule()
    module.create_widget()
    monkeypatch.setattr(module, '_show_history', lambda _: None)
    module.app = SimpleNamespace(thread_pool=SimpleNamespace(start=pending.append))
    module._do_scan()
    old = pending[-1]
    module._do_scan()
    old.signals.result.emit({'scan': ScanResult([], [{'State': 'connected', 'SSID': 'Stale'}], None, None),
                             'history': None})
    assert module._glance['ssid'].value.text() == '\u2014'
    assert not module._scan_btn.isEnabled()
    old.signals.finished.emit()
    assert not module._scan_btn.isEnabled()
    module.on_stop()
    pending[-1].signals.finished.emit()
