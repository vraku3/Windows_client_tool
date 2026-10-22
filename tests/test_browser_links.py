"""No real registry, DPAPI, process inspection, or shell launches."""
import json
import struct
from types import SimpleNamespace

import pytest

from modules.quick_fix import browser_links as links


class Key:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def machine(monkeypatch, tmp_path):
    reg = links.winreg
    data = {}
    writes = []
    denied = set()
    backup = tmp_path / 'office_links_backup.json'

    def open_key(hive, path, *args):
        if (hive, path) in denied:
            raise PermissionError('refused')
        if (hive, path) not in data:
            raise FileNotFoundError(path)
        return Key((hive, path))

    def create(hive, path, *args):
        data.setdefault((hive, path), {})
        return Key((hive, path))

    def query(key, name):
        try:
            return data[key.path][name]
        except KeyError:
            raise FileNotFoundError(name)

    def set_value(key, name, reserved, kind, value):
        assert key.path == (reg.HKEY_CURRENT_USER, links.OFFICE)
        assert backup.exists(), 'journal must precede EVERY registry write'
        writes.append((name, value, kind))
        data[key.path][name] = (value, kind)

    def delete(key, name):
        assert key.path == (reg.HKEY_CURRENT_USER, links.OFFICE)
        assert backup.exists()
        writes.append((name, None, None))
        if name not in data[key.path]:
            raise FileNotFoundError(name)
        del data[key.path][name]

    def enum_key(key, index):
        prefix = key.path[1] + '\\'
        names = sorted({path[len(prefix):].split('\\')[0] for hive, path in data
                        if hive == key.path[0] and path.startswith(prefix)})
        if index >= len(names):
            raise OSError(259, 'No more data')
        return names[index]

    def enum_value(key, index):
        values = list(data[key.path].items())
        if index >= len(values):
            raise OSError(259, 'No more data')
        name, (value, kind) = values[index]
        return name, value, kind

    monkeypatch.setattr(reg, 'OpenKey', open_key)
    monkeypatch.setattr(reg, 'CreateKeyEx', create)
    monkeypatch.setattr(reg, 'QueryValueEx', query)
    monkeypatch.setattr(reg, 'SetValueEx', set_value)
    monkeypatch.setattr(reg, 'DeleteValue', delete)
    monkeypatch.setattr(reg, 'EnumKey', enum_key)
    monkeypatch.setattr(reg, 'EnumValue', enum_value)
    monkeypatch.setattr(links, 'backup_path', lambda: backup)
    monkeypatch.setattr(links, 'running_apps', lambda: 'outlook.exe: running; ms-teams.exe: not running')
    monkeypatch.setattr(links.win32crypt, 'CryptProtectData',
                        lambda value, description, *args: b'dpapi:' + value)
    monkeypatch.setattr(links.win32crypt, 'CryptUnprotectData',
                        lambda value, *args: (links.DESCRIPTION, value[6:]))
    monkeypatch.setattr(links.shell_launch, 'launch', lambda *args, **kwargs: None)

    def put(path, name, value, kind=None, hive=reg.HKEY_CURRENT_USER):
        data.setdefault((hive, path), {})[name] = (value, kind or reg.REG_SZ)

    def browser(name='Google Chrome', progid='ChromeHTML', hive=reg.HKEY_CURRENT_USER):
        cap = links.CLIENTS + '\\' + name + '\\Capabilities'
        data.setdefault((hive, links.CLIENTS), {})
        put(links.CLIENTS + '\\' + name, '', name, hive=hive)
        put(cap, 'ApplicationName', name, hive=hive)
        put(cap + '\\URLAssociations', 'http', progid, hive=hive)
        put(cap + '\\URLAssociations', 'https', progid, hive=hive)
        put(links.REGISTERED, name, cap, hive=hive)

    browser()
    put(links.OFFICE, 'BrowserChoice', b'dpapi:' + struct.pack('<I', 1), reg.REG_BINARY)
    return SimpleNamespace(data=data, writes=writes, denied=denied, put=put,
                           browser=browser, backup=backup, reg=reg)


def test_detection_and_missing_firefox(machine):
    machine.browser('Brave', 'BraveHTML', machine.reg.HKEY_LOCAL_MACHINE)
    browsers, errors = links.detect_browsers()
    assert not errors
    assert browsers['Chrome'].http == 'ChromeHTML'
    assert browsers['Brave'].registered_machine == 'Brave'
    assert 'Firefox' not in browsers


@pytest.mark.parametrize('raw,expected', [(0, 'system default'), (1, 'Microsoft Edge'),
                                         (42, 'unknown value 42')])
def test_decrypt_mapping(machine, raw, expected):
    machine.put(links.OFFICE, 'BrowserChoice', b'dpapi:' + struct.pack('<I', raw), machine.reg.REG_BINARY)
    assert expected in links.read_state().office_text


def test_absent_and_undecryptable_are_distinct(machine, monkeypatch):
    del machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)]['BrowserChoice']
    assert 'absent' in links.read_state().office_text
    machine.put(links.OFFICE, 'BrowserChoice', b'bad', machine.reg.REG_BINARY)
    assert 'undecryptable' in links.read_state().office_text


def test_journal_before_write_and_undo_exact(machine):
    machine.put(links.OFFICE, 'DecisionComplete', 9, machine.reg.REG_DWORD)
    original = dict(machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)])
    links.set_office_default()
    assert links.read_state().choice == 0
    assert len(machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)]['BrowserChoiceTime'][0]) == 8
    assert machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)]['DecisionComplete'][0] == 9
    assert json.loads(machine.backup.read_text())['version'] == 1
    links.undo_office()
    assert machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)] == original


def test_readback_mismatch_restores(machine, monkeypatch):
    original = dict(machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)])
    protect = links.win32crypt.CryptProtectData
    monkeypatch.setattr(links.win32crypt, 'CryptProtectData',
                        lambda value, *args: protect(struct.pack('<I', 1), *args))
    with pytest.raises(RuntimeError, match='restored'):
        links.set_office_default()
    assert machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)] == original


def test_policy_prevents_office_writes(machine):
    machine.put(links.POLICY, 'BrowserChoice', 1, machine.reg.REG_DWORD)
    state = links.read_state()
    assert 'override' in state.summary()
    assert 'policy' in links.set_office_default().lower()
    assert machine.writes == []
    assert not machine.backup.exists()


def test_refusal_is_not_absence(machine):
    machine.denied.add((machine.reg.HKEY_CURRENT_USER, links.POLICY))
    assert 'refused' in links.read_state().summary()
    with pytest.raises(RuntimeError, match='refused'):
        links.set_office_default()
    assert not machine.writes


def test_backup_failure_prevents_write(machine, monkeypatch):
    monkeypatch.setattr(links, '_save_journal', lambda *args: (_ for _ in ()).throw(PermissionError('disk refused')))
    with pytest.raises(PermissionError):
        links.set_office_default()
    assert not machine.writes


def test_poll_timeout_and_settings_fallback(machine, monkeypatch):
    calls = []
    monkeypatch.setattr(links.shell_launch, 'launch', lambda uri: calls.append(uri) or ('refused' if len(calls) < 3 else None))
    browser = links.read_state().browsers['Chrome']
    browser.registered_machine = 'Google Chrome'
    links.open_default_settings(browser)
    assert 'registeredAppUser=Google%20Chrome' in calls[0]
    assert 'registeredAppMachine=Google%20Chrome' in calls[1]
    assert calls[2] == 'ms-settings:defaultapps'
    clock = iter([0, 121])
    assert 'not changed yet' in links.poll_defaults(browser, SimpleNamespace(is_cancelled=False),
                                                   clock=lambda: next(clock), sleep=lambda _: None)


def test_poll_success_requires_both_protocols(machine):
    for proto in ('http', 'https'):
        machine.put(links.URLS + '\\' + proto + '\\UserChoice', 'ProgId', 'ChromeHTML')
    assert links.poll_defaults(links.read_state().browsers['Chrome'], SimpleNamespace(is_cancelled=False)) == 'Windows now uses Chrome'


def test_undo_removes_values_originally_absent(machine):
    machine.data.pop((machine.reg.HKEY_CURRENT_USER, links.OFFICE))
    links.set_office_default()
    links.undo_office()
    assert machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)] == {}


def test_existing_backup_is_preserved(machine):
    links.set_office_default()
    original_backup = machine.backup.read_bytes()
    links.set_office_default()
    assert machine.backup.read_bytes() == original_backup


def test_each_actual_change_journals_current_raw_values(machine):
    links.set_office_default()
    machine.put(links.OFFICE, 'BrowserChoice', b'dpapi:' + struct.pack('<I', 1), machine.reg.REG_BINARY)
    machine.put(links.OFFICE, 'DecisionComplete', 15, machine.reg.REG_DWORD)
    before = dict(machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)])
    links.set_office_default()
    links.undo_office()
    assert machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)] == before


def test_registered_applications_without_start_menu_entry(machine):
    for key in list(machine.data):
        if key[1] == links.CLIENTS or key[1].endswith('StartMenuInternet\\Google Chrome'):
            del machine.data[key]
    assert links.detect_browsers()[0]['Chrome'].registered_user == 'Google Chrome'


def test_start_menu_without_registered_application_uses_plain_settings(machine, monkeypatch):
    del machine.data[(machine.reg.HKEY_CURRENT_USER, links.REGISTERED)]
    browser = links.detect_browsers()[0]['Chrome']
    calls = []
    monkeypatch.setattr(links.shell_launch, 'launch', lambda uri: calls.append(uri))
    links.open_default_settings(browser)
    assert calls == ['ms-settings:defaultapps']


def test_machine_registration_attempts_user_then_machine_page(machine, monkeypatch):
    browser = links.Browser('Brave', 'BraveHTML', 'BraveHTML', registered_machine='Brave')
    calls = []
    monkeypatch.setattr(links.shell_launch, 'launch', lambda uri: calls.append(uri) or
                        ('not found' if len(calls) == 1 else None))
    links.open_default_settings(browser)
    assert calls == ['ms-settings:defaultapps?registeredAppUser=Brave',
                     'ms-settings:defaultapps?registeredAppMachine=Brave']


def test_default_file_extensions_are_read_independently(machine):
    machine.put(links.EXTENSIONS + r'\.html\UserChoice', 'ProgId', 'ChromeHTML')
    machine.put(links.EXTENSIONS + r'\.htm\UserChoice', 'ProgId', 'OtherHTML')
    assert links.read_state().defaults['.html'].data == 'ChromeHTML'
    assert links.read_state().defaults['.htm'].data == 'OtherHTML'


def test_dpapi_description_and_current_user_flags(machine, monkeypatch):
    calls = []

    def protect(*args):
        calls.append(args)
        return b'dpapi:' + args[0]

    monkeypatch.setattr(links.win32crypt, 'CryptProtectData', protect)
    links.set_office_default()
    assert calls == [(b'\x00\x00\x00\x00', links.DESCRIPTION, None, None, None, 0)]
    assert machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)]['DecisionComplete'] == (3, machine.reg.REG_DWORD)


def test_partial_write_failure_rolls_back(machine, monkeypatch):
    original = dict(machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)])
    setter = links.winreg.SetValueEx
    failed = False

    def fail_once(key, name, *args):
        nonlocal failed
        if name == 'BrowserChoiceTime' and not failed:
            failed = True
            raise PermissionError('timestamp write refused')
        setter(key, name, *args)

    monkeypatch.setattr(links.winreg, 'SetValueEx', fail_once)
    with pytest.raises(RuntimeError, match='restored'):
        links.set_office_default()
    assert machine.data[(machine.reg.HKEY_CURRENT_USER, links.OFFICE)] == original


def test_stale_confirmation_cannot_overwrite_office_change(machine):
    state = links.read_state()
    machine.put(links.OFFICE, 'DecisionComplete', 7, machine.reg.REG_DWORD)
    with pytest.raises(RuntimeError, match='since confirmation'):
        links.set_office_default(expected=state.office)
    assert not machine.writes


def test_already_default_no_office_writes(machine):
    machine.put(links.OFFICE, 'BrowserChoice', b'dpapi:' + struct.pack('<I', 0), machine.reg.REG_BINARY)
    assert 'already' in links.set_office_default()
    assert not machine.writes


def test_detection_refused_not_reported_as_absence(machine):
    machine.denied.add((machine.reg.HKEY_CURRENT_USER, links.REGISTERED))
    assert any('refused' in reason for reason in links.detect_browsers()[1])


def test_poll_two_second_interval_and_cancellation(machine):
    ticks = iter([0, 0, 120])
    sleeps = []
    browser = links.detect_browsers()[0]['Chrome']
    result = links.poll_defaults(browser, SimpleNamespace(is_cancelled=False), clock=lambda: next(ticks), sleep=sleeps.append)
    assert 'not changed yet' in result
    assert sum(sleeps) == pytest.approx(2)
    assert 'cancelled' in links.poll_defaults(browser, SimpleNamespace(is_cancelled=True))


def test_broken_existing_backup_blocks_writes(machine):
    machine.backup.write_text('{broken')
    with pytest.raises(ValueError):
        links.set_office_default()
    assert not machine.writes
