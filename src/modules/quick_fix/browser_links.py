"""Web-link settings engine. Only Office's per-user preference is writable.

Call all public I/O functions from a Worker. Windows UserChoice, policies,
and protocol registrations are deliberately read-only here.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote
import winreg
import win32crypt

from core import shell_launch

logger = logging.getLogger(__name__)
OFFICE = r'Software\Microsoft\Office\16.0\Common\Links'
POLICY = r'Software\Policies\Microsoft\office\16.0\common\links'
CLIENTS = r'SOFTWARE\Clients\StartMenuInternet'
REGISTERED = r'SOFTWARE\RegisteredApplications'
URLS = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations'
EXTENSIONS = r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts'
DESCRIPTION = 'V2 Microsoft 365 Browser User Choice'
NAMES = ('BrowserChoice', 'BrowserChoiceTime', 'DecisionComplete')
EEA_NOTE = "In the EEA, Windows' own Search/Widgets web links follow the default browser."
_lock = threading.Lock()


@dataclass(eq=True)
class Value:
    data: object = None
    kind: int | None = None
    error: str = ''

    @property
    def present(self):
        return self.kind is not None

    def text(self):
        if self.error:
            return 'could not read: ' + self.error
        return str(self.data) if self.present else 'absent'


@dataclass
class Browser:
    name: str
    http: str
    https: str
    registered_user: str = ''
    registered_machine: str = ''

    def is_default(self, defaults):
        return all(not defaults[p].error and defaults[p].present
                   and str(defaults[p].data).casefold() == expected.casefold()
                   for p, expected in (('http', self.http), ('https', self.https)))


@dataclass
class State:
    defaults: dict[str, Value]
    office: dict[str, Value]
    policy: Value
    choice: int | None
    office_text: str
    browsers: dict[str, Browser] = field(default_factory=dict)
    detection_errors: list[str] = field(default_factory=list)
    processes: str = ''
    undo_available: bool = False

    def summary(self):
        windows = ', '.join(f'{key}={value.text()}' for key, value in self.defaults.items())
        policy = ('could not check: ' + self.policy.error if self.policy.error else
                  'override present (' + self.policy.text() + ')' if self.policy.present else 'absent')
        return f'Windows default: {windows}. Outlook/Teams: {self.office_text}. Policy: {policy}. {EEA_NOTE}'


def _read(hive, path, name) -> Value:
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            data, kind = winreg.QueryValueEx(key, name)
        return Value(data, kind)
    except FileNotFoundError:
        return Value()
    except OSError as error:
        logger.warning('Browser links: read %s\\%s refused: %s', path, name, error)
        return Value(error=str(error))


def _enumerate(hive, path, values=False):
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    yield winreg.EnumValue(key, index) if values else winreg.EnumKey(key, index)
                except OSError as error:
                    if getattr(error, 'winerror', None) == 259 or error.errno == 259:
                        break
                    raise
                index += 1
    except FileNotFoundError:
        return


def detect_browsers():
    browsers = {}
    errors = []
    for hive, scope in ((winreg.HKEY_CURRENT_USER, 'user'), (winreg.HKEY_LOCAL_MACHINE, 'machine')):
        candidates = []
        try:
            for name, path, kind in _enumerate(hive, REGISTERED, values=True):
                if kind == winreg.REG_SZ and isinstance(path, str):
                    candidates.append((name, path, name))
        except OSError as error:
            errors.append(f'{scope} RegisteredApplications: {error}')
        try:
            for client in _enumerate(hive, CLIENTS):
                path = CLIENTS + '\\' + client
                display = _read(hive, path, '')
                if display.error:
                    errors.append(f'{scope} {client}: {display.error}')
                candidates.append((client, path + r'\Capabilities', ''))
        except OSError as error:
            errors.append(f'{scope} StartMenuInternet: {error}')
        for name, cap, registered in candidates:
            app_name = _read(hive, cap, 'ApplicationName')
            if app_name.error:
                errors.append(f'{scope} {cap}: {app_name.error}')
            label = (name + ' ' + str(app_name.data or '')).casefold()
            target = next((name for name in ('Chrome', 'Brave', 'Firefox') if name.casefold() in label), None)
            if target is None:
                continue
            http = _read(hive, cap + r'\URLAssociations', 'http')
            https = _read(hive, cap + r'\URLAssociations', 'https')
            if http.error or https.error:
                errors.append(f'{scope} {target}: {http.error or https.error}')
                continue
            if not (isinstance(http.data, str) and http.data and isinstance(https.data, str) and https.data):
                continue
            existing = browsers.get(target)
            if existing is None:
                existing = browsers[target] = Browser(target, http.data, https.data)
            if registered:
                setattr(existing, 'registered_' + scope, registered)
    for error in errors:
        logger.warning('Browser detection: %s', error)
    return browsers, errors


def read_defaults():
    return {name: _read(winreg.HKEY_CURRENT_USER,
                        (URLS if name in ('http', 'https') else EXTENSIONS) + '\\' + name + r'\UserChoice',
                        'ProgId') for name in ('http', 'https', '.html', '.htm')}


def _office_values():
    return {name: _read(winreg.HKEY_CURRENT_USER, OFFICE, name) for name in NAMES}


def _choice(value):
    if value.error:
        return None, 'could not read: ' + value.error
    if not value.present:
        return None, 'BrowserChoice absent'
    try:
        if value.kind != winreg.REG_BINARY or not isinstance(value.data, bytes):
            raise ValueError('BrowserChoice is not REG_BINARY')
        description, plaintext = win32crypt.CryptUnprotectData(value.data, None, None, None, 0)
        if description != DESCRIPTION:
            raise ValueError('unexpected DPAPI description')
        choice, = struct.unpack('<I', plaintext)
        return choice, {0: 'system default (0)', 1: 'Microsoft Edge (1)'}.get(choice, f'unknown value {choice}')
    except Exception as error:
        logger.warning('Browser links: could not decrypt Office preference: %s', error)
        return None, 'undecryptable: ' + str(error)


def running_apps():
    import psutil
    names = set()
    refused = False
    try:
        for process in psutil.process_iter(['name']):
            try:
                name = process.info.get('name')
                if name:
                    names.add(name.casefold())
                else:
                    refused = True
            except psutil.Error as error:
                refused = True
                logger.warning('Browser links: process read failed: %s', error)
    except psutil.Error as error:
        refused = True
        logger.warning('Browser links: process enumeration failed: %s', error)
    return '; '.join(f'{name}: ' + ('running' if name in names else
                                  'could not determine (process read refused)' if refused else 'not running')
                     for name in ('outlook.exe', 'ms-teams.exe'))


def backup_path():
    appdata = os.environ.get('APPDATA')
    if not appdata:
        raise RuntimeError('APPDATA is unavailable; cannot safely journal Office values')
    return Path(appdata) / 'WindowsTweaker' / 'quick_fix' / 'office_links_backup.json'


def read_state():
    office = _office_values()
    choice, text = _choice(office['BrowserChoice'])
    errors = [f'{name}: {value.error}' for name, value in office.items() if value.error]
    if errors:
        text += '; ' + '; '.join(errors)
    browsers, detection_errors = detect_browsers()
    state = State(read_defaults(), office, _read(winreg.HKEY_CURRENT_USER, POLICY, 'BrowserChoice'),
                  choice, text, browsers, detection_errors, running_apps())
    try:
        state.undo_available = backup_path().is_file()
    except OSError as error:
        state.detection_errors.append('Could not inspect Undo backup: ' + str(error))
    return state


def _require_readable(values):
    for name, value in values.items():
        if value.error:
            raise RuntimeError(f'{name}: read refused: {value.error}')


def _save_journal(values):
    path = backup_path()
    if path.exists():
        _load_journal()  # Refuse to overwrite an unreadable recovery journal.
    document = {'version': 1, 'values': {
        name: {'kind': value.kind, 'data': base64.b64encode(value.data).decode('ascii')
               if isinstance(value.data, bytes) else value.data,
               'binary': isinstance(value.data, bytes)} for name, value in values.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='office_links_', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        logger.info('Browser links: wrote Office Undo journal %s', path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _load_journal():
    with backup_path().open(encoding='utf-8') as handle:
        document = json.load(handle)
    if document.get('version') != 1 or set(document.get('values', {})) != set(NAMES):
        raise ValueError('Invalid Office Undo backup')
    result = {}
    for name, raw in document['values'].items():
        kind, data = raw['kind'], raw['data']
        if raw['binary']:
            data = base64.b64decode(data, validate=True)
        if kind is not None and not isinstance(kind, int):
            raise ValueError('Invalid registry type in Undo backup')
        if kind is None and data is not None:
            raise ValueError('Invalid absent value in Undo backup')
        result[name] = Value(data, kind)
    return result


def _write_values(values):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, OFFICE, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        for name in NAMES:
            value = values[name]
            if value.present:
                logger.info('Browser links: writing HKCU\\%s\\%s (type %s)', OFFICE, name, value.kind)
                winreg.SetValueEx(key, name, 0, value.kind, value.data)
            else:
                logger.info('Browser links: deleting HKCU\\%s\\%s (originally absent)', OFFICE, name)
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    logger.debug('Browser links: %s already absent', name)


def _restore(values):
    _write_values(values)
    actual = _office_values()
    if actual != values:
        raise RuntimeError('Office restore read-back did not match; Undo backup retained')


def set_office_default(expected=None):
    with _lock:
        policy = _read(winreg.HKEY_CURRENT_USER, POLICY, 'BrowserChoice')
        _require_readable({'policy': policy})
        if policy.present:
            return 'Office policy override present; user preference left unchanged'
        original = _office_values()
        _require_readable(original)
        if expected is not None and original != expected:
            raise RuntimeError('Office settings changed since confirmation; review and try again')
        choice, _ = _choice(original['BrowserChoice'])
        if choice == 0:
            return 'Outlook/Teams already uses the system default'
        blob = win32crypt.CryptProtectData(struct.pack('<I', 0), DESCRIPTION, None, None, None, 0)
        desired = dict(original)
        desired['BrowserChoice'] = Value(blob, winreg.REG_BINARY)
        desired['BrowserChoiceTime'] = Value(struct.pack('<Q', time.time_ns() // 100 + 116444736000000000),
                                            winreg.REG_BINARY)
        if not original['DecisionComplete'].present:
            desired['DecisionComplete'] = Value(3, winreg.REG_DWORD)
        _save_journal(original)
        try:
            _write_values(desired)
            actual = _office_values()
            if _choice(actual['BrowserChoice'])[0] != 0 or actual != desired:
                raise RuntimeError('Office write read-back mismatch')
        except Exception as error:
            logger.error('Browser links: Office write failed; restoring original values: %s', error)
            try:
                _restore(original)
            except Exception as restore_error:
                logger.error('Browser links: rollback failed: %s', restore_error)
                raise RuntimeError(f'{error}; restore FAILED: {restore_error}. Undo backup retained') from error
            raise RuntimeError(f'{error}; original Office values restored') from error
        return 'Outlook/Teams now uses the system default. Restart Outlook/Teams to pick it up'


def undo_office():
    with _lock:
        original = _load_journal()
        _restore(original)
        backup_path().unlink()
        logger.info('Browser links: removed successfully restored Undo journal')
    return 'Original Outlook/Teams values restored and verified. Restart Outlook/Teams to pick it up'


def confirmation(state, browser):
    lines = [state.summary(), state.processes, '', 'Changes:']
    if state.policy.error:
        lines.append('Blocked: Office policy could not be read: ' + state.policy.error)
    elif state.policy.present:
        lines.append('Office: policy override present; no Office values will be changed.')
    elif any(value.error for value in state.office.values()):
        lines.append('Blocked: Office values could not be read; no values will be changed.')
    elif state.choice == 0:
        lines.append('Office: already system default; no Office values will be changed.')
    else:
        lines.extend(['Save the current raw Office values to ' + str(backup_path()) +
                      ' before writing (replace the previous Undo backup for this new change).',
                      'Office BrowserChoice: ' + state.office_text + ' -> system default (0), protected with DPAPI.',
                      'Office BrowserChoiceTime: ' + state.office['BrowserChoiceTime'].text() + ' -> current UTC FILETIME.',
                      'Office DecisionComplete: ' + (state.office['DecisionComplete'].text() + ' (keep exactly).'
                       if state.office['DecisionComplete'].present else 'absent -> DWORD 3.'),
                      'Restart Outlook/Teams afterwards; neither app will be closed.'])
    if browser.is_default(state.defaults):
        lines.append(f'Windows: http and https already use {browser.name}; no change needed.')
    else:
        lines.append(f'Open Windows Default apps for {browser.name}. Click "Set default"; '
                     'Windows handles the associations. Watch http/https for up to 120 seconds.')
    lines.append('The app will not change .html/.htm directly. A www address uses http/https when opened as a web link.')
    return '\n'.join(lines)


def open_default_settings(browser):
    candidates = []
    # Try the per-user Settings route first even for a machine registration.
    for scope, name in (('User', browser.registered_user or browser.registered_machine),
                        ('Machine', browser.registered_machine or browser.registered_user)):
        if name:
            candidates.append('ms-settings:defaultapps?registeredApp' + scope + '=' + quote(name, safe=''))
    candidates.append('ms-settings:defaultapps')
    failures = []
    for uri in candidates:
        reason = shell_launch.launch(uri)
        if reason is None:
            return 'Windows Settings opened. Click "Set default"'
        logger.warning('Browser links: Settings launch failed (%s): %s', uri, reason)
        failures.append(reason)
    raise RuntimeError('Could not open Windows Default apps: ' + '; '.join(failures))


def poll_defaults(browser, worker, timeout=120, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while not worker.is_cancelled:
        defaults = read_defaults()
        if browser.is_default(defaults):
            return f'Windows now uses {browser.name}'
        if clock() >= deadline:
            errors = '; '.join(v.error for v in defaults.values() if v.error)
            return 'Windows not changed yet' + ('; read refused: ' + errors if errors else '')
        # Short waits allow shutdown cancellation without a two-second stall.
        for _ in range(20):
            if worker.is_cancelled:
                return 'Windows default check cancelled'
            sleep(0.1)
    return 'Windows default check cancelled'
