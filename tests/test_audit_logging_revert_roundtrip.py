"""Audit & Logging writes go stage -> apply -> revert and land EXACTLY where
they started (final-review C1, C2, I1).

The machine is a fake that keeps state -- each log's maxSize and enabled flag,
each audit subcategory's success/failure pair -- behind `subprocess.run`, so
every layer between the catalog and the shell is the real one: ChangeSet.add
(the revert probe), the applier, TweakEngine's `script` step, BackupService's
record and its revert, and reverting.revert_control's read-back. Nothing here
touches this machine's audit policy or log sizes.

process_cmdline_auditing is a registry step, and BackupService restores a
registry before-value itself; that is checked for real against a throwaway
HKCU key (the control's HKLM key needs elevation and is not touched).
"""
import dataclasses
import shlex
import types
import winreg

import pytest

from core.backup_service import BackupService
from modules.security_dashboard import audit_policy
from modules.security_dashboard.applier import apply_batch
from modules.security_dashboard.catalog import load_catalog
from modules.security_dashboard.catalog.model import ControlState
from modules.security_dashboard.reverting import revert_control
from modules.security_dashboard.staging import NO_PROBE_NOTE, ChangeSet
from modules.tweaks.tweak_engine import TweakEngine

MB = 1024 * 1024
_TASK = "Microsoft-Windows-TaskScheduler/Operational"
_PROC = audit_policy.SUBCATEGORIES[0]
_HEADER = ("Machine Name,Policy Target,Subcategory,Subcategory GUID,"
           "Inclusion Setting,Exclusion Setting\n")
_WORDS = {(False, False): "No Auditing", (True, False): "Success",
          (False, True): "Failure", (True, True): "Success and Failure"}


class FakeMachine:
    """wevtutil and auditpol, answering from (and writing to) plain state."""

    def __init__(self):
        self.logs = {"Security": {"maxSize": 64 * MB, "enabled": True},
                     _TASK: {"maxSize": 10 * MB, "enabled": False}}
        self.audit = {s.guid: (False, False) for s in audit_policy.SUBCATEGORIES}
        self.audit_refused = False
        self.commands = []

    def run(self, args, **_kw):
        argv = list(args) if isinstance(args, (list, tuple)) else shlex.split(args, posix=False)
        self.commands.append(" ".join(argv))
        tool = argv[0].lower()
        handler = {"wevtutil": self._wevtutil, "auditpol": self._auditpol}.get(tool)
        assert handler, f"unexpected command: {argv}"
        rc, out, err = handler(argv[1:])
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    def _wevtutil(self, rest):
        verb, name = rest[0], rest[1]
        log = self.logs.get(name)
        if log is None:
            return 15007, "", "The specified channel could not be found."
        if verb == "gl":
            return 0, (f"name: {name}\nenabled: {str(log['enabled']).lower()}\n"
                       f"type: Operational\nlogging:\n  logFileName: x.evtx\n"
                       f"  retention: false\n  autoBackup: false\n"
                       f"  maxSize: {log['maxSize']}\n"), ""
        if verb == "qe":
            return 0, "", ""
        if verb == "sl":
            for arg in rest[2:]:
                flag, _, value = arg.partition(":")
                if flag == "/ms":
                    log["maxSize"] = int(value)
                elif flag == "/e":
                    assert value in ("true", "false"), value
                    log["enabled"] = value == "true"
                else:
                    raise AssertionError(f"unknown wevtutil switch {arg}")
            return 0, "", ""
        raise AssertionError(f"unexpected wevtutil verb {verb}")

    def _auditpol(self, rest):
        guid = rest[1].split(":", 1)[1]
        if rest[0] == "/get":
            if self.audit_refused:
                return 1314, "Error 0x00000522 occurred:\nA required privilege is not held.\n", ""
            words = _WORDS[self.audit[guid]]
            return 0, _HEADER + f"PC,System,X,{guid},{words},\n", ""
        assert rest[0] == "/set", rest
        success, failure = self.audit[guid]
        for arg in rest[2:]:
            flag, _, value = arg.partition(":")
            assert value in ("enable", "disable"), arg
            if flag == "/success":
                success = value == "enable"
            elif flag == "/failure":
                failure = value == "enable"
            else:
                raise AssertionError(f"unknown auditpol switch {arg}")
        self.audit[guid] = (success, failure)
        return 0, "The command was successfully executed.\n", ""


@pytest.fixture
def machine(monkeypatch):
    fake = FakeMachine()
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


@pytest.fixture
def pipeline(tmp_path):
    backup = BackupService(str(tmp_path))
    yield backup, TweakEngine(backup)
    backup.close()


def _noop():
    return None


def _round_trip(control, backup, engine):
    """Stage (the probe runs here), apply, revert. Returns the two results."""
    changes = ChangeSet()
    changes.add(control, True)
    (change,) = changes.changes
    applied = apply_batch(changes, engine, backup, invalidate_reads=_noop)
    reverted = revert_control(control.id, backup, {control.id: control},
                              expected=change.from_value, invalidate_reads=_noop)
    return change, applied, reverted


def test_security_log_from_a_non_default_64mb_to_192mb_and_back(machine, pipeline):
    control = load_catalog()["security_log_size"]
    change, applied, reverted = _round_trip(control, *pipeline)
    assert change.revert_token == str(64 * MB) and change.revert_note is None
    assert applied.results[0].state is ControlState.APPLIED_VERIFIED
    assert f"wevtutil sl Security /ms:{192 * MB}" in machine.commands
    assert machine.logs["Security"]["maxSize"] == 64 * MB   # not the 20 MB default
    assert reverted.state is ControlState.APPLIED_VERIFIED


def test_task_scheduler_disabled_enabled_disabled(machine, pipeline):
    control = load_catalog()["taskscheduler_log_enabled"]
    change, applied, reverted = _round_trip(control, *pipeline)
    assert change.revert_token == "false"
    assert applied.results[0].state is ControlState.APPLIED_VERIFIED
    assert machine.commands.count(f"wevtutil sl {_TASK} /e:true") == 1
    assert machine.logs[_TASK]["enabled"] is False
    assert reverted.state is ControlState.APPLIED_VERIFIED


def test_process_creation_failure_only_keeps_failure_and_reverts_exactly(machine, pipeline):
    machine.audit[_PROC.guid] = (False, True)               # Failure only
    control = load_catalog()["audit_process_creation"]
    changes = ChangeSet()
    changes.add(control, True)
    (change,) = changes.changes
    assert change.revert_token == "/success:disable /failure:enable"
    backup, engine = pipeline
    applied = apply_batch(changes, engine, backup, invalidate_reads=_noop)
    assert applied.results[0].state is ControlState.APPLIED_VERIFIED
    assert machine.audit[_PROC.guid] == (True, True)        # Failure kept, Success added
    assert not any("/set" in c and ":disable" in c for c in machine.commands)
    reverted = revert_control(control.id, backup, {control.id: control},
                              expected=change.from_value, invalidate_reads=_noop)
    assert machine.audit[_PROC.guid] == (False, True)
    assert machine.commands[-2:-1] == [
        f"auditpol /set /subcategory:{_PROC.guid} /success:disable /failure:enable"]
    assert reverted.state is ControlState.APPLIED_VERIFIED


def test_unreadable_probe_stages_with_the_note_and_runs_no_revert(machine, pipeline):
    machine.audit_refused = True
    control = load_catalog()["audit_process_creation"]
    changes = ChangeSet()
    changes.add(control, True, from_value=None)              # as the pane does
    (change,) = changes.changes
    assert change.revert_token is None
    assert change.revert_note == NO_PROBE_NOTE
    assert change in changes.one_way_changes
    assert all(not s.get("revert_command") for s in change.resolved_steps())

    from modules.security_dashboard.security_module import ReviewDialog
    assert NO_PROBE_NOTE in ReviewDialog(changes).details_text()

    backup, engine = pipeline
    apply_batch(changes, engine, backup, invalidate_reads=_noop)
    sets_after_apply = [c for c in machine.commands if "/set" in c]
    assert sets_after_apply == [f"auditpol /set /subcategory:{_PROC.guid} /success:enable"]
    revert_control(control.id, backup, {control.id: control}, invalidate_reads=_noop)
    assert [c for c in machine.commands if "/set" in c] == sets_after_apply


@pytest.mark.parametrize("token", ["64 & calc", "/success:enable /failure:enable & x",
                                   "TRUE", "", "1" * 13])
def test_a_token_outside_the_allowlist_never_reaches_a_command(machine, token):
    catalog = load_catalog()
    for cid in ("security_log_size", "taskscheduler_log_enabled", "audit_logon"):
        control = dataclasses.replace(catalog[cid], revert_probe=lambda t=token: t)
        changes = ChangeSet()
        changes.add(control, True, from_value=False)
        (change,) = changes.changes
        assert all(not s.get("revert_command") for s in change.resolved_steps()), cid
        assert change.revert_note == NO_PROBE_NOTE


def test_probes_reject_malformed_machine_text(machine, monkeypatch):
    from modules.security_dashboard import log_readiness
    catalog = load_catalog()
    monkeypatch.setattr(log_readiness, "read_setting", lambda n, k, run=None: "64; calc")
    assert catalog["security_log_size"].revert_probe() is None
    assert catalog["taskscheduler_log_enabled"].revert_probe() is None


# -- process_cmdline_auditing: a registry step BackupService reverts itself ----

_SANDBOX = r"Software\WinClientToolTest_CmdlineAudit"


def _delete_tree(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            subs = [winreg.EnumKey(k, i) for i in range(winreg.QueryInfoKey(k)[0])]
    except FileNotFoundError:
        return
    for sub in subs:
        _delete_tree(path + "\\" + sub)
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


def _value(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SANDBOX) as k:
            return winreg.QueryValueEx(k, name)[0]
    except FileNotFoundError:
        return None


@pytest.mark.parametrize("before", [None, 0])
def test_cmdline_auditing_registry_value_reverts_to_its_before_value(pipeline, before):
    real = load_catalog()["process_cmdline_auditing"]
    (on,) = real.on_steps
    assert on["type"] == "registry" and on["key"].startswith("HKLM\\")
    assert real.revert_probe is None
    name = on["value"]
    _delete_tree(_SANDBOX)
    try:
        if before is not None:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _SANDBOX) as k:
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, before)

        def read():
            return {"available": True, "enabled": _value(name) == 1}
        control = dataclasses.replace(
            real, reader=read,
            on_steps=(dict(on, key="HKCU\\" + _SANDBOX),))
        backup, engine = pipeline
        change, applied, reverted = _round_trip(control, backup, engine)
        assert applied.results[0].state is ControlState.APPLIED_VERIFIED
        assert reverted.state is ControlState.APPLIED_VERIFIED
        assert _value(name) == before
    finally:
        _delete_tree(_SANDBOX)
