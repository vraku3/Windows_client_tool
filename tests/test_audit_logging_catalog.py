"""The Audit & Logging category: sizes, task history, 4688 command lines.

Every control here either reads a log through the `log_readiness` engine or a
single registry value, and every write is a `wevtutil sl` / registry step
built from app-owned constants. The refusal rule holds: a read Windows would
not answer is None, never "0 days" or "not auditing".
"""
from datetime import datetime, timedelta, timezone

import pytest

from modules.security_dashboard import log_readiness
from modules.security_dashboard.catalog import load_catalog
from modules.security_dashboard.catalog.model import Category

_NEW_IDS = {
    "security_log_size", "system_log_size", "application_log_size",
    "powershell_log_size", "taskscheduler_log_enabled",
    "process_cmdline_auditing", "sysmon_installed",
}

_ALLOWED_COMMAND_TOKENS = {
    "wevtutil", "sl",
    "Security", "System", "Application",
    "Microsoft-Windows-PowerShell/Operational",
    "Microsoft-Windows-TaskScheduler/Operational",
    "/ms:201326592", "/ms:20971520", "/ms:33554432", "/ms:15728640",
    "/ms:268435456", "/e:true", "/e:false",
}


def _new_controls():
    catalog = load_catalog()
    return {cid: catalog[cid] for cid in _NEW_IDS}


def _status(max_bytes, oldest=None, present=True, refused=None, mode="circular"):
    return log_readiness.LogStatus(
        name="Security", present=present, enabled=True, max_bytes=max_bytes,
        mode=mode, oldest=oldest, refused=refused)


def test_the_seven_controls_are_in_the_new_category():
    controls = load_catalog()
    found = {cid for cid, c in controls.items()
             if c.category is Category.AUDIT_LOGGING}
    assert _NEW_IDS <= found
    assert Category.AUDIT_LOGGING.value == "Audit & Logging"


def test_a_20mb_security_log_is_not_at_desired(monkeypatch):
    monkeypatch.setattr(log_readiness, "read_log",
                        lambda name, run=None: _status(20971520))
    assert load_catalog()["security_log_size"].read() is False


def test_a_200mb_security_log_is_at_desired(monkeypatch):
    monkeypatch.setattr(log_readiness, "read_log",
                        lambda name, run=None: _status(200 * 1024 * 1024))
    assert load_catalog()["security_log_size"].read() is True


def test_a_refused_log_read_is_none_not_false(monkeypatch):
    monkeypatch.setattr(log_readiness, "read_log",
                        lambda name, run=None: _status(
                            None, present=None, refused="needs administrator rights"))
    control = load_catalog()["security_log_size"]
    assert control.read() is None
    assert control.reader()["available"] is False


def test_powershell_details_state_coverage_in_days(monkeypatch):
    oldest = datetime.now(timezone.utc) - timedelta(days=1)
    monkeypatch.setattr(log_readiness, "read_log",
                        lambda name, run=None: _status(
                            15728640, oldest=oldest))
    result = load_catalog()["powershell_log_size"].reader()
    joined = " | ".join(f"{k}: {v}" for k, v in result["details"])
    assert "1.0 day(s) of history" in joined
    assert "small" not in joined.lower()


def test_a_coverage_refusal_still_reports_the_size(monkeypatch):
    """Security's size reads unelevated; only its oldest event is refused.
    The value is known, so the control is a value -- not Unknown."""
    monkeypatch.setattr(log_readiness, "read_log",
                        lambda name, run=None: _status(
                            20971520, refused="needs administrator rights"))
    control = load_catalog()["security_log_size"]
    assert control.read() is False
    joined = " | ".join(f"{k}: {v}" for k, v in control.reader()["details"])
    assert "could not be read" in joined


def test_every_write_is_a_constant_command_or_registry_step():
    for control in _new_controls().values():
        for step in control.on_steps + control.off_steps:
            assert step["type"] in ("command", "registry", "registry_delete")
            if step["type"] == "command":
                extra = set(step["cmd"].split()) - _ALLOWED_COMMAND_TOKENS
                assert not extra, f"{control.id}: unexpected tokens {extra}"
            else:
                assert step["key"].startswith("HKLM\\")


def test_every_write_has_its_revert():
    for control in _new_controls().values():
        if control.writable:
            assert control.on_steps and control.off_steps, control.id


def test_sysmon_is_read_only_with_a_reason():
    control = load_catalog()["sysmon_installed"]
    assert not control.writable
    assert "kernel driver" in (control.read_only_reason or "")


@pytest.mark.real_machine
def test_the_new_category_reads_on_this_machine():
    for control in _new_controls().values():
        assert control.read() is not None, control.id
