"""Batch A regressions: all machine I/O is faked."""
import ast
import inspect
import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from modules.security_dashboard import security_reader as sr, incident_triage as tri
from modules.security_dashboard import snapshots, asr_rules
from modules.security_dashboard.catalog import load_catalog
from modules.security_dashboard.catalog import accounts
from modules.security_dashboard.staging import ChangeSet, PendingChange, NO_PROBE_NOTE


def stage(cid, target, current=None):
    cs = ChangeSet()
    cs.add(load_catalog()[cid], target, from_value=current)
    return cs.changes[0]


@pytest.mark.parametrize('cid,field,old', [
    ('firewall_enabled', 'Enabled', [0, 1, 1]),
    ('firewall_inbound_blocked', 'DefaultInboundAction', [0, 2, 4]),
    ('firewall_outbound_blocked', 'DefaultOutboundAction', [4, 0, 2]),
])
@pytest.mark.parametrize('target', [True, False])
def test_firewall_undo_keeps_each_profile(monkeypatch, cid, field, old, target):
    names = ['Domain', 'Private', 'Public']
    rows = [dict(Name=n, **{field: v}) for n, v in zip(names, old)]
    monkeypatch.setattr(sr, '_ps', lambda *a, **k: (0, json.dumps(rows), ''))
    change = stage(cid, target, not target)
    cmd = change.resolved_steps()[0]['revert_command']
    assert cmd and '-All' not in cmd
    values = ({0: 'False', 1: 'True'} if field == 'Enabled' else
              {0: 'NotConfigured', 2: 'Allow', 4: 'Block'})
    for name, value in zip(names, old):
        assert f'-Profile {name} -{field} {values[value]}' in cmd
    # A stateful shell fake applies the forward action, then the captured undo.
    state = dict(zip(names, (values[v] for v in old)))
    before = state.copy()
    forward = change.resolved_steps()[0]['command'].split()[-1]
    state.update(dict.fromkeys(names, forward))
    for name, value in re.findall(r'-Profile (Domain|Private|Public) -' + field
                                 + r' (True|False|NotConfigured|Allow|Block)', cmd):
        state[name] = value
    assert state == before
    for suffix in ('; whoami', '\n', '\nwhoami'):
        bad = PendingChange(cid, change.control, not target, target,
                            change.revert_token + suffix)
        assert not bad.resolved_steps()[0].get('revert_command')


@pytest.mark.parametrize('payload', ['[]', '[{"Name":"Domain","Enabled":1}]',
    '[{"Name":"Domain","Enabled":1},{"Name":"Private","Enabled":1},{"Name":"Public","Enabled":"1;whoami"}]',
    'Access denied'])
def test_firewall_unreadable_state_has_no_guessed_undo(monkeypatch, payload):
    monkeypatch.setattr(sr, '_ps', lambda *a, **k: (0, payload, ''))
    change = stage('firewall_enabled', True, False)
    assert not change.resolved_steps()[0].get('revert_command')
    assert change.revert_note == NO_PROBE_NOTE


@pytest.mark.parametrize('cid', ['firewall_enabled', 'firewall_inbound_blocked',
                                'firewall_outbound_blocked', 'asr_wmi_persistence'])
def test_probe_token_must_match_in_full(cid):
    control = load_catalog()[cid]
    assert control.revert_probe is not None
    change = PendingChange(cid, control, False, True, 'True; whoami')
    assert not change.resolved_steps()[0].get('revert_command')


@pytest.mark.parametrize('build,value', [(19045, 1), (22000, 1), (22621, 2), (26100, 2)])
def test_lsa_writer_uses_no_lock_on_supported_builds(monkeypatch, build, value):
    monkeypatch.setattr(sr.sys, 'getwindowsversion', lambda: SimpleNamespace(build=build))
    control = load_catalog()['lsass_protection']
    assert control.steps_for(True)[0]['data'] == value
    assert 'firmware lock' in control.description
    assert 'registry revert' in control.description


@pytest.mark.parametrize('value,wording', [(1, 'with UEFI'), (2, 'without UEFI')])
def test_lsa_labels(monkeypatch, value, wording):
    monkeypatch.setattr(sr, '_reg_read', lambda *a: value)
    result = sr.check_lsass_protection()
    assert result['enabled'] is True
    assert wording in str(result['details'])


def test_hide_username_uses_policy_key_for_read_and_writes(monkeypatch):
    keys = []
    monkeypatch.setattr(sr, '_reg_read', lambda key, *a: keys.append(key) or 1)
    monkeypatch.setattr(sr, '_reg_write', lambda key, *a: keys.append(key) or True)
    assert sr.check_last_username_hidden()['enabled'] is True
    sr.set_last_username_hidden(True)
    c = load_catalog()['last_username_hidden']
    keys += [s['key'] for s in c.on_steps + c.off_steps]
    assert all(k == accounts._POLICIES_SYSTEM for k in keys)


@pytest.mark.parametrize('value', [0, 4, 10, 50])
def test_cached_logons_writes_exact_integer_as_reg_sz(value):
    step = stage('cached_logons', value).resolved_steps()[0]
    assert (step['data'], step['kind']) == (str(value), 'SZ')


@pytest.mark.parametrize('value', [-1, 51, True, False, 4.0, '4', '4;whoami'])
def test_cached_logons_rejects_invalid_targets_before_staging(value):
    with pytest.raises(ValueError, match='0..50'):
        stage('cached_logons', value)


@pytest.mark.parametrize('cid', [c.id for c in load_catalog().values()
    if c.writable and type(c.desired) is int and c.id != 'cached_logons'])
def test_other_numeric_controls_refuse_unimplemented_targets(cid):
    c = load_catalog()[cid]
    with pytest.raises(ValueError, match='target'):
        stage(cid, 123456)
    assert stage(cid, c.desired).resolved_steps() == PendingChange(
        cid, c, None, c.desired).resolved_steps()


def test_unknown_string_target_is_refused():
    with pytest.raises(ValueError, match='target'):
        stage('ps_execution_policy', 'Bypass')


def test_netbios_label_describes_name_resolution_only(monkeypatch):
    monkeypatch.setattr(sr, '_reg_read', lambda *a: 2)
    result = sr.check_netbios_tcpip()
    assert result['status'] == 'P-node: no broadcast name resolution'
    assert 'WINS' in str(result['details'])
    assert 'broadcast' in load_catalog()['netbios_over_tcpip'].title.lower()


@pytest.mark.parametrize('action', [0, 1, 2, 5, 6, None])
def test_asr_undo_preserves_action_or_absence(monkeypatch, action):
    rule = asr_rules.RULES[0]
    prefs = {'AttackSurfaceReductionRules_Ids': [] if action is None else [rule.guid],
             'AttackSurfaceReductionRules_Actions': [] if action is None else [action]}
    monkeypatch.setattr(snapshots, 'mp_preference', lambda: prefs)
    monkeypatch.setattr(snapshots, 'unavailable', lambda *a: None)
    change = stage('asr_' + rule.slug, action != 1, action == 1)
    cmd = change.resolved_steps()[0]['revert_command']
    assert cmd and rule.guid in cmd
    if action is None:
        assert 'Remove-MpPreference' in cmd
        assert 'Add-MpPreference' not in cmd
    else:
        assert f'-AttackSurfaceReductionRules_Actions {action}' in cmd


@pytest.mark.parametrize('prefs,reason', [({}, 'denied'),
    ({'AttackSurfaceReductionRules_Ids': ['x'], 'AttackSurfaceReductionRules_Actions': []}, None),
    ({'AttackSurfaceReductionRules_Ids': [asr_rules.RULES[0].guid],
      'AttackSurfaceReductionRules_Actions': ['bad']}, None)])
def test_asr_failed_probe_has_no_undo(monkeypatch, prefs, reason):
    monkeypatch.setattr(snapshots, 'mp_preference', lambda: prefs)
    monkeypatch.setattr(snapshots, 'unavailable', lambda *a: reason)
    change = stage('asr_' + asr_rules.RULES[0].slug, True, False)
    assert change.revert_note == NO_PROBE_NOTE
    assert not change.resolved_steps()[0].get('revert_command')


@pytest.mark.parametrize('where', ['OpenKey', 'QueryValueEx'])
@pytest.mark.parametrize('error', [FileNotFoundError, PermissionError, OSError])
def test_registry_absence_is_not_refusal(monkeypatch, where, error):
    monkeypatch.setattr(sr.winreg, 'OpenKey', MagicMock())
    monkeypatch.setattr(sr.winreg, where, MagicMock(side_effect=error('refused')))
    result = sr.check_autologon()
    if error is FileNotFoundError:
        assert result['enabled'] is False and result['available'] is True
    else:
        assert result['available'] is False
        assert load_catalog()['autologon'].read() is None


_REG_CHECKS = [n.name for n in ast.parse(inspect.getsource(sr)).body
               if isinstance(n, ast.FunctionDef) and n.name.startswith('check_')
               and any(isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                       and x.func.id == '_reg_read' for x in ast.walk(n))]


@pytest.mark.parametrize('name', _REG_CHECKS)
def test_registry_dependent_checks_report_refusal(monkeypatch, name):
    monkeypatch.setattr(sr.winreg, 'OpenKey', MagicMock(side_effect=PermissionError('denied')))
    monkeypatch.setattr(sr, '_windows_build', lambda: 18362)
    monkeypatch.setattr(sr, '_ps', lambda *a, **k: (1, '', 'denied'))
    monkeypatch.setattr(sr, '_cmd_run', lambda *a, **k: (1, '', 'denied'))
    assert getattr(sr, name)().get('available') is False


@pytest.mark.parametrize('configured,running,status', [
    ([1, 2], [1, 2], 'Running'), ([1], [2], 'Configured (needs reboot)'),
    ([2], [2], 'Not Configured'), (1, 1, 'Running'), (3, 3, 'Not Configured')])
def test_credential_guard_service_ids_are_not_bitmasks(monkeypatch, configured, running, status):
    monkeypatch.setattr(sr, '_ps', lambda *a, **k: (0, json.dumps({
        'SecurityServicesConfigured': configured, 'SecurityServicesRunning': running}), ''))
    assert sr.check_credential_guard()['status'] == status


def test_task_query_failure_is_unknown_coverage(monkeypatch):
    monkeypatch.setattr(tri, 'read', lambda channel, *a: tri.Query(
        channel, [], refused='access denied' if channel == tri.TASKS else None))
    monkeypatch.setattr(tri.log_readiness, 'read_log', lambda *a: SimpleNamespace(enabled=True))
    findings = tri.triage().findings
    assert any(f.source == tri.TASKS and f.severity == tri.UNKNOWN
               and 'access denied' in f.detail for f in findings)


def test_importing_unchanged_nondefault_numeric_value_stages_nothing():
    cs = ChangeSet()
    cs.add(load_catalog()['ntlm_level'], 3, from_value=3)
    assert not cs.changes
