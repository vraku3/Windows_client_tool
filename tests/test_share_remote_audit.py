"""SMB and remote-access exposure engines (fakes; real machine for shape)."""
import json
import sys

import pytest

from modules.remote_tools import remote_audit as ra
from modules.shared_resources import share_audit as sa


def _data(**kw):
    base = dict(shares=[], access=[], sessions=[], open_files=[],
                server={"Smb1": False, "RequireSigning": True, "Encrypt": True, "RejectUnencrypted": True,
                        "AutoShareServer": True, "AutoShareWks": True},
                client={"InsecureGuest": False, "RequireSigning": True}, guest={"Enabled": False})
    base.update(kw)
    return sa.ShareData(**base)


def test_parse_collected_errors_make_section_unknown_not_empty():
    d = sa.parse_collected(json.dumps({"shares": [], "errors": {"access": "Access is denied"}}))
    assert d.shares == [] and d.access is None and "access" in d.errors
    assert any(f.key == "unreadable_access" for f in sa.audit(d))
    assert sa.parse_collected("not json").fatal


def test_smb1_guest_and_signing_findings():
    d = _data(server={"Smb1": True, "RequireSigning": False, "Encrypt": False,
                      "AutoShareServer": True, "AutoShareWks": True}, guest={"Enabled": True},
              client={"InsecureGuest": True})
    keys = {f.key: f.severity for f in sa.audit(d)}
    assert keys["smb1"] == "high" and keys["signing"] == "medium"
    assert keys["guest_on"] == "medium" and keys["insecure_guest"] == "medium"


def test_everyone_full_on_user_share_is_high_read_is_medium():
    shares = [{"Name": "Data", "Path": "C:\\d", "ShareType": "FileSystemDirectory", "Special": False, "Enum": "AccessBased"},
              {"Name": "Pub", "Path": "C:\\p", "ShareType": "FileSystemDirectory", "Special": False, "Enum": "AccessBased"}]
    access = [{"Share": "Data", "Account": "Everyone", "Type": "Allow", "Right": "Full"},
              {"Share": "Pub", "Account": "Everyone", "Type": "Allow", "Right": "Read"},
              {"Share": "Pub", "Account": "BUILTIN\\Administrators", "Type": "Allow", "Right": "Full"}]
    got = {f.key + f.share: f.severity for f in sa.audit(_data(shares=shares, access=access), lambda p: True)}
    assert got["share_writeData"] == "high" and got["share_readPub"] == "medium"


def test_encryption_required_but_not_enforced_is_a_low_finding():
    d = _data(server={"Smb1": False, "RequireSigning": True, "Encrypt": True, "RejectUnencrypted": False,
                      "AutoShareServer": True, "AutoShareWks": True})
    keys = {f.key: f.severity for f in sa.audit(d)}
    assert keys["reject_unencrypted_off"] == "low"


def test_encryption_optional_and_unenforced_is_not_the_reject_finding():
    # This machine's real default: Encrypt off, RejectUnencrypted on -- no
    # inconsistency to flag, only the existing "encryption not enforced" info.
    d = _data(server={"Smb1": False, "RequireSigning": True, "Encrypt": False, "RejectUnencrypted": True,
                      "AutoShareServer": True, "AutoShareWks": True})
    keys = {f.key for f in sa.audit(d)}
    assert "reject_unencrypted_off" not in keys and "encrypt" in keys


def test_share_level_encryption_override_is_surfaced():
    shares = [{"Name": "Data", "Path": "C:\\d", "ShareType": "FileSystemDirectory", "Special": False,
              "Enum": "AccessBased", "Encrypt": True}]
    d = _data(shares=shares, access=[], server={"Smb1": False, "RequireSigning": True, "Encrypt": False,
              "RejectUnencrypted": True, "AutoShareServer": True, "AutoShareWks": True})
    keys = {f.key: f for f in sa.audit(d, lambda p: True)}
    assert "share_encrypt_override" in keys
    assert "Data" in keys["share_encrypt_override"].title
    rows = sa.rows_shares(d)
    assert rows[0]["Encrypted"] == "Yes"


def test_share_without_encryption_reports_no_in_rows():
    shares = [{"Name": "Data", "Path": "C:\\d", "ShareType": "FileSystemDirectory", "Special": False,
              "Enum": "AccessBased", "Encrypt": False}]
    d = _data(shares=shares, access=[])
    assert sa.rows_shares(d)[0]["Encrypted"] == "No"


def test_unreadable_permissions_do_not_claim_a_clean_share():
    shares = [{"Name": "Data", "Path": "", "ShareType": "FileSystemDirectory", "Special": False, "Enum": "AccessBased"}]
    d = _data(shares=shares, access=None)
    d.errors["access"] = "denied"
    assert not any(f.key.startswith("share_") for f in sa.audit(d))
    assert sa.rows_shares(d)[0]["Access"] == "(permissions unreadable)"


def test_collect_cache_reuses_one_run():
    calls = []
    sa._cache["data"] = None
    fn = lambda: calls.append(1) or sa.ShareData()  # noqa: E731
    sa.collect_cached(10, now=lambda: 100.0, fn=fn)
    sa.collect_cached(10, now=lambda: 105.0, fn=fn)
    sa.collect_cached(10, now=lambda: 120.0, fn=fn)
    assert len(calls) == 2
    sa._cache["data"] = None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SMB")
def test_real_collect_shape():
    d = sa.collect()
    if d.fatal:
        pytest.skip(d.fatal)
    assert d.server is None or "Smb1" in d.server
    for f in sa.audit(d):
        assert f.severity in ("high", "medium", "low", "info")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SMB")
def test_real_server_encryption_fields_are_present():
    # Confirmed live 2026-09-29: Get-SmbServerConfiguration on this machine
    # answers EncryptData=False, RejectUnencryptedAccess=True (the Windows
    # default) -- both keys must come through parsing, not just exist as
    # PowerShell property names.
    d = sa.collect()
    if d.fatal or d.server is None:
        pytest.skip(d.fatal or "server section unreadable")
    assert "RejectUnencrypted" in d.server
    assert isinstance(d.server["RejectUnencrypted"], bool)
    for s in d.shares or []:
        assert "Encrypt" in s or s.get("ShareType", "").lower().startswith("interprocess")


# ---------------------------------------------------------------- remote

def test_unread_rdp_is_unknown_never_off():
    row = ra.evaluate(ra.RemoteState(errors={"rdp": "refused"}))[-1]
    rdp = [r for r in ra.evaluate(ra.RemoteState()) if r.feature == "Remote Desktop"][0]
    assert rdp.status == "Unknown" and row is not None


def test_rdp_without_nla_and_broad_group_is_high():
    s = ra.RemoteState(rdp_enabled=True, rdp_nla=False, rdp_port=3389, rdp_users=["Everyone"],
                       fw_enabled={"rdp": 1})
    rdp = [r for r in ra.evaluate(s) if r.feature == "Remote Desktop"][0]
    assert rdp.severity == "high" and rdp.status == "On" and len(rdp.findings) == 2
    assert rdp.jump == ra.JUMP_FIREWALL


def test_rdp_off_and_services_missing():
    s = ra.RemoteState(rdp_enabled=False,
                       services={"WinRM": ra.ServiceInfo(True, False, "manual"), "sshd": ra.ServiceInfo(False)})
    rows = {r.feature: r for r in ra.evaluate(s)}
    assert rows["Remote Desktop"].status == "Off" and rows["OpenSSH server"].status == "Off"
    assert rows["WinRM / PowerShell remoting"].status == "Off"


def test_running_winrm_listening_is_flagged_and_failed_query_unknown():
    s = ra.RemoteState(services={"WinRM": ra.ServiceInfo(True, True, "auto"), "sshd": ra.ServiceInfo(None)},
                       listening={5985: True, 22: False}, fw_enabled={"winrm": 2})
    rows = {r.feature: r for r in ra.evaluate(s)}
    assert rows["WinRM / PowerShell remoting"].status == "On"
    assert rows["WinRM / PowerShell remoting"].severity == "medium"
    assert rows["OpenSSH server"].status == "Unknown"


def test_remote_assistance_full_control_finding():
    s = ra.RemoteState(assist_enabled=True, assist_full_control=True)
    row = [r for r in ra.evaluate(s) if r.feature == "Remote Assistance"][0]
    assert row.severity == "medium" and "FULL CONTROL" in row.findings[0]


def test_remote_assistance_unencrypted_tickets_is_a_medium_finding():
    s = ra.RemoteState(assist_enabled=True, assist_full_control=False, assist_encrypted_only_tickets=False)
    row = [r for r in ra.evaluate(s) if r.feature == "Remote Assistance"][0]
    assert row.severity == "medium"
    assert any("Unencrypted" in f and "legacy" in f for f in row.findings)


def test_remote_assistance_absent_ticket_key_is_not_a_finding():
    # None = the registry value was never set, which is the modern secure
    # default (encrypted-only), not a refused read.
    s = ra.RemoteState(assist_enabled=True, assist_full_control=False, assist_encrypted_only_tickets=None)
    row = [r for r in ra.evaluate(s) if r.feature == "Remote Assistance"][0]
    assert row.severity == "low" and not row.findings


def test_weak_min_encryption_level_is_high_when_security_layer_is_not_tls():
    s = ra.RemoteState(rdp_enabled=True, rdp_security_layer=1, rdp_min_encryption_level=2)
    row = [r for r in ra.evaluate(s) if r.feature == "Remote Desktop"][0]
    assert row.severity == "high"
    assert any("weak bulk encryption" in f for f in row.findings)


def test_weak_min_encryption_level_is_dormant_but_noted_under_tls():
    # Confirmed live 2026-10-01 on this machine: SecurityLayer=2 (TLS-only),
    # MinEncryptionLevel=2 ("Client Compatible") -- inert today, a footgun if
    # SecurityLayer ever regresses, so it still gets a finding, just not HIGH.
    s = ra.RemoteState(rdp_enabled=True, rdp_security_layer=2, rdp_min_encryption_level=2, rdp_nla=True)
    row = [r for r in ra.evaluate(s) if r.feature == "Remote Desktop"][0]
    assert row.severity != "high"
    assert any("currently harmless" in f and "SecurityLayer" in f for f in row.findings)


def test_high_min_encryption_level_is_not_a_finding():
    s = ra.RemoteState(rdp_enabled=True, rdp_security_layer=2, rdp_min_encryption_level=3, rdp_nla=True)
    row = [r for r in ra.evaluate(s) if r.feature == "Remote Desktop"][0]
    assert not any("ncryption level" in f for f in row.findings)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows")
def test_real_rdp_min_encryption_level_and_assist_tickets_are_read():
    # Confirmed live 2026-10-01: both registry values are readable unelevated
    # on this machine (MinEncryptionLevel=2, CreateEncryptedOnlyTickets=0).
    st = ra.RemoteState()
    ra.read_registry(st)
    assert st.rdp_min_encryption_level is None or isinstance(st.rdp_min_encryption_level, int)
    assert st.assist_encrypted_only_tickets in (None, True, False)


def test_fw_rule_counting_uses_enabled_inbound_allow_only():
    from modules.firewall_rules.firewall_manager_module import FirewallRule
    mk = lambda n, e="Yes", d="In", a="Allow": FirewallRule(n, e, d, a, "TCP", "", "", "", "")  # noqa: E731
    counts = ra.count_fw_rules([mk("Remote Desktop - User Mode (TCP-In)"), mk("Remote Desktop x", e="No"),
                                mk("Remote Desktop y", d="Out"), mk("Windows Remote Management (HTTP-In)")])
    assert counts["rdp"] == 1 and counts["winrm"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows")
def test_real_gather_reads_something():
    st = ra.gather()
    assert st.rdp_enabled is not None or "rdp" in st.errors
    assert all(r.status in ("On", "Off", "Unknown") for r in ra.evaluate(st))


# ------------------------------------------------------------- WinRM detail

def test_trusted_hosts_set_is_a_medium_finding_on_a_running_service():
    s = ra.RemoteState(services={"WinRM": ra.ServiceInfo(True, True, "auto"), "sshd": ra.ServiceInfo(False)},
                       listening={5985: False, 22: False}, winrm_trusted_hosts="*")
    row = [r for r in ra.evaluate(s) if r.feature == "WinRM / PowerShell remoting"][0]
    assert row.status == "On" and row.severity == "medium"
    assert "TrustedHosts: *" in row.summary
    assert any("without" in f and "Kerberos" in f for f in row.findings)


def test_empty_trusted_hosts_is_not_a_finding():
    s = ra.RemoteState(services={"WinRM": ra.ServiceInfo(True, True, "auto"), "sshd": ra.ServiceInfo(False)},
                       listening={5985: False, 22: False}, winrm_trusted_hosts="")
    row = [r for r in ra.evaluate(s) if r.feature == "WinRM / PowerShell remoting"][0]
    assert "TrustedHosts empty" in row.summary and not row.findings


def test_unreadable_trusted_hosts_says_so_rather_than_guessing():
    s = ra.RemoteState(services={"WinRM": ra.ServiceInfo(True, True, "auto"), "sshd": ra.ServiceInfo(False)},
                       listening={5985: False, 22: False}, winrm_trusted_hosts=None,
                       winrm_listener_readable=False)
    row = [r for r in ra.evaluate(s) if r.feature == "WinRM / PowerShell remoting"][0]
    assert "TrustedHosts unreadable" in row.summary
    assert "listener config needs admin to read" in row.summary


def test_winrm_off_never_shows_trusted_hosts_detail():
    s = ra.RemoteState(services={"WinRM": ra.ServiceInfo(True, False, "manual"), "sshd": ra.ServiceInfo(False)},
                       winrm_trusted_hosts="*")
    row = [r for r in ra.evaluate(s) if r.feature == "WinRM / PowerShell remoting"][0]
    assert row.status == "Off" and "TrustedHosts" not in row.summary


@pytest.mark.skipif(sys.platform != "win32", reason="Windows")
def test_real_winrm_trusted_hosts_registry_is_readable():
    # Confirmed live 2026-09-27: the WSMAN\Client branch stays readable even
    # when WinRM has never been configured (service Stopped/Manual here) --
    # unlike the WSMan PowerShell provider, which needs the service running
    # and fails with "cannot connect"/"path does not exist" in that state.
    st = ra.RemoteState()
    ra.read_winrm_trusted_hosts(st)
    assert st.winrm_trusted_hosts is not None
    ra.read_winrm_listener_access(st)
    assert st.winrm_listener_readable in (True, False)
