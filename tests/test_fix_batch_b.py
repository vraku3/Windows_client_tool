"""Whole-app review batch B: tweak/registry reverts, restore throttle, task state."""
import subprocess
import types
import winreg

import pytest

import modules.tweaks.tweak_engine as te
import core.system_restore as sr
from core.backup_service import BackupService, StepRecord


class _Key:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _engine(monkeypatch, query):
    backups = types.SimpleNamespace(
        backup_registry_key=lambda *a: types.SimpleNamespace(ok=True, exported=True, reason=""))
    eng = te.TweakEngine.__new__(te.TweakEngine)
    eng._backup = backups
    monkeypatch.setattr(te.winreg, "OpenKey", lambda *a, **k: _Key())
    monkeypatch.setattr(te.winreg, "CreateKeyEx", lambda *a, **k: _Key())
    monkeypatch.setattr(te.winreg, "QueryValueEx", query)
    writes = []
    monkeypatch.setattr(te.winreg, "SetValueEx", lambda k, n, r, kind, d: writes.append((kind, d)))
    return eng, writes


def test_the_revert_type_is_the_original_one(monkeypatch):
    eng, writes = _engine(monkeypatch, lambda k, n: ("1", winreg.REG_SZ))
    rec = eng._apply_registry({"key": r"HKCU\Software\X", "value": "V", "data": 0,
                               "kind": "DWORD"}, "rp")
    assert writes == [(winreg.REG_DWORD, 0)]
    assert rec.before_value == "1" and rec.reg_kind == winreg.REG_SZ


def test_an_absent_value_reverts_with_the_new_type(monkeypatch):
    def missing(k, n):
        raise FileNotFoundError()
    eng, _ = _engine(monkeypatch, missing)
    rec = eng._apply_registry({"key": r"HKCU\Software\X", "value": "V", "data": 0,
                               "kind": "DWORD"}, "rp")
    assert rec.before_value is None and rec.reg_kind == winreg.REG_DWORD


def test_a_refused_read_changes_nothing(monkeypatch):
    def refused(k, n):
        raise PermissionError(5, "Access is denied")
    eng, writes = _engine(monkeypatch, refused)
    with pytest.raises(PermissionError):
        eng._apply_registry({"key": r"HKLM\SOFTWARE\X", "value": "V", "data": 0,
                             "kind": "DWORD"}, "rp")
    assert writes == []


def test_a_refused_read_is_not_already_deleted(monkeypatch):
    def refused(k, n):
        raise PermissionError(5, "Access is denied")
    eng, _ = _engine(monkeypatch, refused)
    with pytest.raises(PermissionError):
        eng._apply_registry_delete({"key": r"HKLM\SOFTWARE\X", "value": "V"}, "rp")


def _xml(enabled):
    tag = f"<Enabled>{enabled}</Enabled>" if enabled is not None else ""
    return ('<?xml version="1.0" encoding="UTF-16"?><Task version="1.2" '
            'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
            f"<Settings>{tag}</Settings></Task>")


@pytest.mark.parametrize("enabled,expected", [("false", "Disabled"), ("true", "Enabled"),
                                              (None, "Enabled")])
def test_task_state_comes_from_the_xml(monkeypatch, enabled, expected):
    monkeypatch.setattr(te.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout=_xml(enabled), stderr=""))
    assert te._task_enabled_state(r"\T") == expected


def test_an_unreadable_task_is_not_disabled(monkeypatch):
    calls = []

    def run(args, **k):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="ERROR: Access is denied.")
    monkeypatch.setattr(te.subprocess, "run", run)
    eng = te.TweakEngine.__new__(te.TweakEngine)
    with pytest.raises(RuntimeError):
        eng._apply_scheduled_task({"task_name": r"\T"}, "rp")
    assert not any("/disable" in a for a in calls)


def test_a_failed_winget_reinstall_stays_pending(monkeypatch, tmp_path):
    import core.backup_service as bs
    monkeypatch.setattr(bs.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, stdout="No package found", stderr=""))
    s = BackupService(data_dir=str(tmp_path))
    try:
        rp = s.create_restore_point("x", "Debloat")
        s.record_steps("t", [StepRecord("appx", "Some.App", "Some.App", None)], rp)
        assert not s.restore_point(rp).success
    finally:
        s.close()


def test_the_throttle_value_is_put_back(monkeypatch):
    state = {}
    monkeypatch.setattr(sr, "_bypass_frequency_throttle", lambda: ("value", 1440, winreg.REG_DWORD))
    monkeypatch.setattr(sr, "_restore_frequency_throttle", lambda b: state.setdefault("restored", b))
    monkeypatch.setattr(sr, "_checkpoint", lambda d, t: (True, "RESTORE-POINT-CREATED 1"))
    monkeypatch.setattr(sr.subprocess, "run", lambda *a, **k: None)
    assert sr.create_restore_point("x")[0] is True
    assert state["restored"] == ("value", 1440, winreg.REG_DWORD)


def test_the_throttle_is_put_back_even_when_the_checkpoint_raises(monkeypatch):
    state = {}
    monkeypatch.setattr(sr, "_bypass_frequency_throttle", lambda: ("absent",))
    monkeypatch.setattr(sr, "_restore_frequency_throttle", lambda b: state.setdefault("restored", b))

    def boom(d, t):
        raise RuntimeError("boom")
    monkeypatch.setattr(sr, "_checkpoint", boom)
    monkeypatch.setattr(sr.subprocess, "run", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        sr.create_restore_point("x")
    assert state["restored"] == ("absent",)
