"""The Audit & Logging category: logs sized for an investigation."""
from datetime import datetime, timedelta, timezone

import pytest

from modules.security_dashboard import log_readiness
from modules.security_dashboard.catalog import load_catalog
from modules.security_dashboard.catalog.model import Category
from modules.security_dashboard.log_readiness import LogStatus

IDS = ("security_log_size", "system_log_size", "application_log_size",
       "powershell_log_size", "taskscheduler_log_enabled",
       "process_cmdline_auditing", "sysmon_installed")
MB = 1024 * 1024


def _patch(monkeypatch, status):
    monkeypatch.setattr(log_readiness, "read_log", lambda name, run=None: status)


def _status(name, mb, oldest=None):
    return LogStatus(name, True, enabled=True, max_bytes=mb * MB,
                     mode="circular", oldest=oldest)


def _ctl(cid):
    return load_catalog()[cid]


def test_every_control_is_in_the_category():
    cat = load_catalog()
    for cid in IDS:
        assert cid in cat
        assert cat[cid].category is Category.AUDIT_LOGGING


def test_security_log_size_threshold(monkeypatch):
    _patch(monkeypatch, _status("Security", 20))
    assert _ctl("security_log_size").read() is False
    _patch(monkeypatch, _status("Security", 200))
    assert _ctl("security_log_size").read() is True


def test_refused_read_is_none(monkeypatch):
    _patch(monkeypatch, LogStatus("Security", None, refused="needs administrator rights"))
    for cid in ("security_log_size", "system_log_size", "powershell_log_size",
                "taskscheduler_log_enabled", "sysmon_installed"):
        assert _ctl(cid).read() is None, cid


def test_absent_channel_is_not_a_refusal(monkeypatch):
    _patch(monkeypatch, LogStatus("x", False))
    assert _ctl("sysmon_installed").read() is False


def test_powershell_details_mention_coverage(monkeypatch):
    oldest = datetime.now(timezone.utc) - timedelta(days=1)
    _patch(monkeypatch, _status("PS", 15, oldest=oldest))
    d = _ctl("powershell_log_size").reader()
    text = " ".join(f"{a} {b}" for a, b in d["details"])
    assert "day" in text and "history" in text
    assert d["enabled"] is False


def test_security_size_known_when_only_coverage_refused(monkeypatch):
    st = _status("Security", 200)
    st.refused = "needs administrator rights"
    _patch(monkeypatch, st)
    assert _ctl("security_log_size").read() is True
    d = _ctl("security_log_size").reader()
    assert "could not" in " ".join(b for _, b in d["details"]).lower()


def test_steps_are_constant_and_revertible():
    for cid in IDS:
        c = _ctl(cid)
        if cid == "sysmon_installed":
            assert not c.writable and c.read_only_reason
            continue
        assert c.on_steps and c.off_steps and c.requires_admin
        assert c.desired is True and not c.requires_reboot
        for s in c.on_steps + c.off_steps:
            assert s["type"] in ("command", "registry", "registry_delete")
    assert _ctl("process_cmdline_auditing").off_steps[0]["type"] == "registry_delete"


def test_ui_iterates_new_category():
    assert Category.AUDIT_LOGGING.value == "Audit & Logging"
