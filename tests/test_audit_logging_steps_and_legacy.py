"""Exact steps of the Audit & Logging controls, and legacy MB profile values."""
import pytest

from modules.security_dashboard import log_readiness
from modules.security_dashboard.catalog import load_catalog
from modules.security_dashboard.catalog.audit_logging import migrate_legacy_value
from modules.security_dashboard.log_readiness import LogStatus
from modules.security_dashboard.staging import ChangeSet

MB = 1024 * 1024


def _cmd(log, arg):
    return {"type": "command", "cmd": f"wevtutil sl {log} {arg}"}


_PS = "Microsoft-Windows-PowerShell/Operational"
_TS = "Microsoft-Windows-TaskScheduler/Operational"
_AK = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit"
_CL = "ProcessCreationIncludeCmdLine_Enabled"
EXACT = {
    "security_log_size": ((_cmd("Security", "/ms:201326592"),),
                          (_cmd("Security", "/ms:20971520"),)),
    "system_log_size": ((_cmd("System", "/ms:67108864"),),
                        (_cmd("System", "/ms:20971520"),)),
    "application_log_size": ((_cmd("Application", "/ms:33554432"),),
                             (_cmd("Application", "/ms:20971520"),)),
    "powershell_log_size": ((_cmd(_PS, "/ms:268435456"),),
                            (_cmd(_PS, "/ms:15728640"),)),
    "taskscheduler_log_enabled": ((_cmd(_TS, "/e:true"),),
                                  (_cmd(_TS, "/e:false"),)),
    "process_cmdline_auditing": (
        ({"type": "registry", "key": _AK, "value": _CL, "data": 1,
          "kind": "DWORD"},),
        ({"type": "registry_delete", "key": _AK, "value": _CL},)),
}


def _ctl(cid):
    return load_catalog()[cid]


def _at(monkeypatch, name, mb):
    st = LogStatus(name, True, enabled=True, max_bytes=mb * MB, mode="circular")
    monkeypatch.setattr(log_readiness, "read_log", lambda n, run=None: st)


@pytest.mark.parametrize("cid", sorted(EXACT))
def test_exact_steps(cid):
    on, off = EXACT[cid]
    assert _ctl(cid).on_steps == on
    assert _ctl(cid).off_steps == off


def test_legacy_system_profile_stages_64mb_and_verifies(monkeypatch):
    _at(monkeypatch, "System", 20)
    ctl = _ctl("system_log_size")
    cs = ChangeSet()
    cs.add(ctl, 64)
    (change,) = cs.changes
    assert change.to_value is True
    assert ctl.steps_for(change.to_value) == EXACT["system_log_size"][0]
    _at(monkeypatch, "System", 64)
    assert ctl.read() == change.to_value


def test_legacy_small_security_value_never_shrinks(monkeypatch, caplog):
    ctl = _ctl("security_log_size")
    for current in (20, 200):
        _at(monkeypatch, "Security", current)
        cs = ChangeSet()
        with caplog.at_level("WARNING"):
            cs.add(ctl, 20)
        assert len(cs) == 0
    assert "security_log_size" in caplog.text


def test_bool_values_are_untouched(monkeypatch):
    _at(monkeypatch, "Security", 20)
    cs = ChangeSet()
    cs.add(_ctl("security_log_size"), True)
    assert cs.changes[0].to_value is True
    assert migrate_legacy_value("security_log_size", False) is False
    assert migrate_legacy_value("uac", 64) == 64
