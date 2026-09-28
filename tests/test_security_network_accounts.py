"""Network and credential readers, against what this machine really returns.

Task 7 binds these into the catalog, and running them first turned up the same
family of defect Task 6 did -- a verdict rendered from something the reader
never established:

  check_firewall_stealth   DefaultInboundAction 0 is NotConfigured, not Allow.
      The live enum is NotConfigured=0, Allow=2, Block=4. All three profiles
      read 0 here, and the card said "Inbound Allowed", in red, on a machine
      whose firewall blocks inbound by default.
  check_network_profile    mapped "0"/"1"/"2" but `Select -ExpandProperty
      NetworkCategory` yields the NAME. The card read "Unknown (Private)".
  check_smbv1              when Get-WindowsOptionalFeature is refused (it
      needs elevation) it answered "Probably Disabled (Win10+)", green, with
      available=True. A guess, presented as a reading.
  check_wpad               read HKCU AutoDetect, absent here, and called that
      "Disabled (default)" in green. The real setting is bit 0x08 of
      DefaultConnectionSettings, which is SET on this machine: WPAD
      auto-detect is ON.
  check_smb_ghost          read the SMB1 value. SMBGhost (CVE-2020-0796) is
      the SMBv3.1.1 compression RCE; its mitigation is DisableCompression,
      and only builds 18362/18363 were ever affected. It was answering about
      a different vulnerability entirely.
  check_sam_hive_permissions  os.path.exists on C:\\Windows\\System32\\config\\SAM
      returns False unelevated -- os.stat raises PermissionError, winerror 5 --
      so "file not found" meant "cannot look", and the card read
      "N/A", green, enabled=True. The icacls fallback fails unelevated too
      (rc=1), and that path ALSO fell through to "Restricted (safe)", green.
  check_audit_policy       auditpol needs admin; the amber answer carried no
      `available`, so the catalog could not tell it apart from a real reading.
"""
import pytest

from modules.security_dashboard import security_reader
from modules.security_dashboard import snapshots


#: Get-NetFirewallProfile on this machine, 2026-08-28. Every profile is 0.
REAL_FIREWALL_PROFILES = (
    '[{"Name":"Domain","DefaultInboundAction":0,"DefaultOutboundAction":0},'
    '{"Name":"Private","DefaultInboundAction":0,"DefaultOutboundAction":0},'
    '{"Name":"Public","DefaultInboundAction":0,"DefaultOutboundAction":0}]')


@pytest.fixture
def registry(monkeypatch):
    """A fake registry: {(key, value): data}, everything else absent."""
    values = {}

    def read(key, value, kind=None):
        return values.get((key, value))

    monkeypatch.setattr(security_reader, "_reg_read", read)
    return values


@pytest.fixture
def powershell(monkeypatch):
    """Answer _ps() from a list of (substring, (rc, out, err)) rules."""
    rules = []

    def fake_ps(cmd, timeout=30):
        for needle, response in rules:
            if needle in cmd:
                return response
        return 1, "", "no rule for this command"

    monkeypatch.setattr(security_reader, "_ps", fake_ps)
    return rules


def _detail(result, label):
    for name, value in result["details"]:
        if name == label:
            return value
    return None


# -- check_firewall_stealth --------------------------------------------------

def test_not_configured_inbound_is_not_inbound_allowed(powershell):
    powershell.append(("Get-NetFirewallProfile", (0, REAL_FIREWALL_PROFILES, "")))

    result = security_reader.check_firewall_stealth()

    assert result["available"] is True
    assert result["color"] != "red", (
        "NotConfigured (0) was read as Allow; Windows blocks inbound by default")
    assert result["enabled"] is True


def test_an_explicitly_allowed_inbound_profile_is_red(powershell):
    powershell.append(("Get-NetFirewallProfile", (
        0, '[{"Name":"Public","DefaultInboundAction":2,'
           '"DefaultOutboundAction":0}]', "")))

    result = security_reader.check_firewall_stealth()

    assert result["color"] == "red"
    assert result["enabled"] is False


def test_an_explicitly_blocked_inbound_profile_is_green(powershell):
    powershell.append(("Get-NetFirewallProfile", (
        0, '[{"Name":"Public","DefaultInboundAction":4,'
           '"DefaultOutboundAction":0}]', "")))

    result = security_reader.check_firewall_stealth()

    assert result["color"] == "green"
    assert result["enabled"] is True


def test_a_refused_firewall_profile_read_says_so(powershell):
    powershell.append(("Get-NetFirewallProfile", (1, "", "Access is denied.")))

    result = security_reader.check_firewall_stealth()

    assert result["available"] is False
    assert result.get("enabled") is not True


def test_not_configured_outbound_is_labelled_allow_not_block(powershell):
    # The real bug this pins: NotConfigured (0) used to be hardcoded as
    # "(default: block)" regardless of direction, so the OUTBOUND row (whose
    # real default is allow) displayed the exact opposite of the truth.
    powershell.append(("Get-NetFirewallProfile", (0, REAL_FIREWALL_PROFILES, "")))

    result = security_reader.check_firewall_stealth()

    outbound = _detail(result, "Domain Outbound")
    assert "allow" in outbound.lower() and "block" not in outbound.lower()


def test_not_configured_inbound_is_still_labelled_block(powershell):
    powershell.append(("Get-NetFirewallProfile", (0, REAL_FIREWALL_PROFILES, "")))

    result = security_reader.check_firewall_stealth()

    inbound = _detail(result, "Domain Inbound")
    assert "block" in inbound.lower()


# -- check_firewall_outbound --------------------------------------------------

def test_not_configured_outbound_reads_as_effectively_allowed_not_blocked(powershell):
    powershell.append(("Get-NetFirewallProfile", (0, REAL_FIREWALL_PROFILES, "")))

    result = security_reader.check_firewall_outbound()

    assert result["available"] is True
    assert result["enabled"] is False, (
        "NotConfigured (0) outbound is Windows' real allow-everything default; "
        "reading it as blocked would be the same bug check_firewall_stealth had for inbound")
    assert result["color"] == "amber"


def test_an_explicitly_blocked_outbound_profile_is_green(powershell):
    powershell.append(("Get-NetFirewallProfile", (
        0, '[{"Name":"Public","DefaultOutboundAction":4}]', "")))

    result = security_reader.check_firewall_outbound()

    assert result["color"] == "green" and result["enabled"] is True


def test_an_explicitly_allowed_outbound_profile_is_amber_not_green(powershell):
    powershell.append(("Get-NetFirewallProfile", (
        0, '[{"Name":"Public","DefaultOutboundAction":2}]', "")))

    result = security_reader.check_firewall_outbound()

    assert result["enabled"] is False and result["color"] == "amber"


def test_a_refused_outbound_read_says_so(powershell):
    powershell.append(("Get-NetFirewallProfile", (1, "", "Access is denied.")))

    result = security_reader.check_firewall_outbound()

    assert result["available"] is False
    assert result.get("enabled") is not True


def test_real_firewall_outbound_is_readable_and_matches_stealth_reading():
    # Confirmed live 2026-09-27: all three profiles report NotConfigured for
    # DefaultOutboundAction, so this reads as NOT blocked (amber), while
    # check_firewall_stealth's inbound reading is separately blocked (green)
    # -- the two directions really do disagree on this real machine.
    outbound = security_reader.check_firewall_outbound()
    assert outbound["available"] is True


# -- check_firewall_logging ---------------------------------------------------

_LOGGING_KEYS = {
    "Domain": r"HKLM\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\DomainProfile\Logging",
    "Private": r"HKLM\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\StandardProfile\Logging",
    "Public": r"HKLM\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\PublicProfile\Logging",
}


def test_no_logdroppedpackets_value_reads_as_off_not_unknown(registry):
    # The real reading on this machine, 2026-09-28: the DWORD is absent on
    # all three profiles, which is Windows' own shipped default (off), not a
    # refusal -- this branch is confirmed readable unelevated.
    result = security_reader.check_firewall_logging()

    assert result["available"] is True
    assert result["enabled"] is False
    assert result["color"] == "amber"


def test_all_profiles_logging_dropped_packets_is_green(registry):
    for key in _LOGGING_KEYS.values():
        registry[(key, "LogDroppedPackets")] = 1

    result = security_reader.check_firewall_logging()

    assert result["enabled"] is True
    assert result["color"] == "green"


def test_one_profile_missing_logging_is_still_amber(registry):
    registry[(_LOGGING_KEYS["Domain"], "LogDroppedPackets")] = 1
    registry[(_LOGGING_KEYS["Private"], "LogDroppedPackets")] = 1
    # Public left unset.

    result = security_reader.check_firewall_logging()

    assert result["enabled"] is False
    assert "Public" in result["status"]


def test_real_firewall_logging_is_readable_unelevated():
    # Confirmed live 2026-09-28, unelevated: reads a definite value, never a
    # refusal, on the same FirewallPolicy branch CLAUDE.md already documents
    # as readable without admin for firewall rules.
    result = security_reader.check_firewall_logging()
    assert result["available"] is True


# -- check_network_profile ---------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Private", "Private"),
    ("Public", "Public"),
    ("DomainAuthenticated", "DomainAuthenticated"),
    ("1", "Private"),
    ("0", "Public"),
])
def test_the_network_category_name_is_understood(powershell, raw, expected):
    """PowerShell hands back the NAME; the reader only knew the numbers."""
    powershell.append(("Get-NetConnectionProfile", (0, raw, "")))

    result = security_reader.check_network_profile()

    assert expected in result["status"]
    assert "Unknown" not in result["status"]


# -- check_smbv1 -------------------------------------------------------------

def test_a_refused_feature_list_is_not_a_probably(monkeypatch):
    """Both sources refused. smbv1 now asks Get-SmbServerConfiguration first
    (1.02s, and it answers an ordinary user) rather than building the 8s
    feature list, so refusing the list alone is no longer the whole story --
    this pins the case where nothing answers, which is the one that used to
    produce a green "Probably Disabled"."""
    monkeypatch.setattr(security_reader, "_ps",
                        lambda cmd, timeout=30: (1, "", "Access is denied."))
    monkeypatch.setattr(snapshots, "optional_features", dict)
    monkeypatch.setattr(snapshots, "service_states",
                        lambda: {"lanmanserver": {"status": "Running"}})
    monkeypatch.setattr(
        snapshots, "unavailable",
        lambda name: "requires elevation" if name == "optional_features" else None)

    result = security_reader.check_smbv1()

    assert result["available"] is False, (
        "answered 'Probably Disabled' in green from a refused read")
    assert result.get("enabled") is None


# -- check_wpad --------------------------------------------------------------

def test_wpad_auto_detect_is_read_from_the_connection_flags(registry):
    """AutoDetect is absent on this machine but bit 0x08 of
    DefaultConnectionSettings is set, so auto-detect is ON."""
    registry[(r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion"
              r"\Internet Settings\Connections",
              "DefaultConnectionSettings")] = bytes([70, 0, 0, 0, 5, 0, 0, 0, 9])

    result = security_reader.check_wpad()

    assert result["enabled"] is True
    assert result["color"] == "red"


def test_wpad_off_in_the_connection_flags_is_green(registry):
    registry[(r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion"
              r"\Internet Settings\Connections",
              "DefaultConnectionSettings")] = bytes([70, 0, 0, 0, 5, 0, 0, 0, 1])

    result = security_reader.check_wpad()

    assert result["enabled"] is False
    assert result["color"] == "green"


def test_wpad_with_nothing_to_read_does_not_claim_it_is_off(registry):
    result = security_reader.check_wpad()

    assert result.get("enabled") is None
    assert result["available"] is False


# -- check_smb_ghost ---------------------------------------------------------

def test_smbghost_asks_about_compression_not_smb1(registry, monkeypatch):
    monkeypatch.setattr(security_reader, "_windows_build", lambda: 18363)
    registry[(r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters",
              "DisableCompression")] = 1

    result = security_reader.check_smb_ghost()

    assert result["enabled"] is True
    assert result["color"] == "green"
    assert "SMBv1" not in str(result["details"])


def test_an_affected_build_without_the_mitigation_is_red(registry, monkeypatch):
    monkeypatch.setattr(security_reader, "_windows_build", lambda: 18362)

    result = security_reader.check_smb_ghost()

    assert result["enabled"] is False
    assert result["color"] == "red"


def test_a_build_that_was_never_affected_says_so(registry, monkeypatch):
    """Only 1903 and 1909 shipped the vulnerable compression code."""
    monkeypatch.setattr(security_reader, "_windows_build", lambda: 26200)

    result = security_reader.check_smb_ghost()

    assert result["available"] is True
    assert result["color"] == "green"
    assert "affected" in result["status"].lower()


# -- check_sam_hive_permissions ----------------------------------------------

def test_a_sam_file_we_cannot_stat_is_not_a_clean_bill_of_health(monkeypatch):
    # None means "it is there and we were refused" -- which is what os.stat
    # raises for the SAM hive unelevated (winerror 5) and what
    # os.path.exists silently flattens into False.
    monkeypatch.setattr(security_reader, "_file_present", lambda path: None)

    result = security_reader.check_sam_hive_permissions()

    assert result["available"] is False, (
        "os.path.exists returns False on a permission error, and that read "
        "as 'file not found', which read as safe")
    assert result.get("enabled") is not True


def test_a_failed_icacls_is_not_a_clean_bill_of_health(monkeypatch, powershell):
    monkeypatch.setattr(security_reader, "_file_present", lambda path: True)
    powershell.append(("icacls", (1, "Failed processing 1 files", "")))

    result = security_reader.check_sam_hive_permissions()

    assert result["available"] is False
    assert result.get("enabled") is not True


def test_a_readable_sam_acl_without_users_is_green(monkeypatch, powershell):
    monkeypatch.setattr(security_reader, "_file_present", lambda path: True)
    powershell.append(("icacls", (0, "NT AUTHORITY\\SYSTEM:(F)\n"
                                     "BUILTIN\\Administrators:(F)", "")))

    result = security_reader.check_sam_hive_permissions()

    assert result["available"] is True
    assert result["enabled"] is True
    assert result["color"] == "green"


def test_users_with_read_on_the_sam_hive_is_the_vulnerability(monkeypatch,
                                                              powershell):
    monkeypatch.setattr(security_reader, "_file_present", lambda path: True)
    powershell.append(("icacls", (0, "BUILTIN\\Users:(I)(RX)", "")))

    result = security_reader.check_sam_hive_permissions()

    assert result["available"] is True
    assert result["enabled"] is False
    assert result["color"] == "red"


# -- check_audit_policy ------------------------------------------------------

def test_auditpol_needing_admin_is_reported_as_unavailable(monkeypatch):
    monkeypatch.setattr(security_reader, "_cmd_run",
                        lambda *a, **k: (1, "", "Access denied"))

    result = security_reader.check_audit_policy()

    assert result["available"] is False


# -- the values the catalog compares against ---------------------------------
#
# A control whose reader never returns a machine-readable value reads None for
# ever: the card can render, but "is this at the value we want" is unanswerable.

def test_llmnr_not_configured_means_llmnr_is_on(registry):
    """No policy is not "unknown" -- Windows enables LLMNR by default, which
    is what the reader's own detail line already said in words."""
    result = security_reader.check_llmnr()

    assert result["enabled"] is True
    assert result["available"] is True


def test_llmnr_disabled_by_policy_reads_false(registry):
    registry[(r"HKLM\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient",
              "EnableMulticast")] = 0

    assert security_reader.check_llmnr()["enabled"] is False


def test_mdns_not_configured_means_mdns_is_on(registry):
    assert security_reader.check_mdns()["enabled"] is True


def test_netbios_not_configured_means_netbios_is_on(registry):
    assert security_reader.check_netbios_tcpip()["enabled"] is True


def test_wdigest_not_configured_is_the_secure_default(registry):
    """UseLogonCredential absent means WDigest does not cache plaintext on
    8.1 and later. That is a definite state, not an unknown."""
    result = security_reader.check_wdigest()

    assert result["enabled"] is False
    assert result["available"] is True


def test_ntlm_level_carries_its_number(registry):
    registry[(r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa",
              "LmCompatibilityLevel")] = 5

    assert security_reader.check_ntlm_level()["level"] == 5


def test_cached_logons_carries_its_count(registry):
    registry[(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
              "CachedLogonsCount")] = "4"

    assert security_reader.check_cached_logons()["count"] == 4


def test_cached_logons_defaults_to_the_documented_ten(registry):
    assert security_reader.check_cached_logons()["count"] == 10


def test_password_min_length_carries_its_length(monkeypatch):
    monkeypatch.setattr(
        security_reader, "_cmd_run",
        lambda *a, **k: (0, "Minimum password length:  14\n", ""))

    assert security_reader.check_password_min_length()["length"] == 14


def test_anonymous_restrict_is_a_yes_or_no(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
    registry[(key, "RestrictAnonymous")] = 1
    registry[(key, "RestrictAnonymousSAM")] = 1

    assert security_reader.check_anonymous_restrict()["enabled"] is True

    registry[(key, "RestrictAnonymous")] = 0
    assert security_reader.check_anonymous_restrict()["enabled"] is False


# -- a reader must agree with the writer wired to the same card --------------

def test_the_llmnr_reader_agrees_with_the_llmnr_writer(registry, monkeypatch):
    """The Network Hardening tab wires check_llmnr's `enabled` straight into
    a toggle whose writer is set_llmnr, and whose tooltip says "ON = enabled
    (insecure)". The reader returned `enabled` meaning "LLMNR is OFF", so the
    switch rendered the opposite of what flipping it would do.
    """
    key = r"HKLM\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient"
    written = {}

    monkeypatch.setattr(security_reader, "_reg_write",
                        lambda k, v, d, kind=None: written.update({(k, v): d}) or True)

    for wanted in (True, False):
        security_reader.set_llmnr(wanted)
        registry[(key, "EnableMulticast")] = written[(key, "EnableMulticast")]
        assert security_reader.check_llmnr()["enabled"] is wanted, (
            f"set_llmnr({wanted}) then check_llmnr disagreed")


# -- `x and y` is None, not False --------------------------------------------

def test_no_autologon_value_reads_as_a_definite_no(registry):
    """`val and str(val) != "0"` evaluates to None when the value is absent,
    so a machine with no auto-logon reported `enabled=None` -- "we could not
    look" -- while its own status line said "Disabled"."""
    result = security_reader.check_autologon()

    assert result["enabled"] is False
    assert result["available"] is True


def test_autologon_configured_reads_as_true(registry):
    registry[(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
              "AutoAdminLogon")] = "1"

    assert security_reader.check_autologon()["enabled"] is True


def test_no_screensaver_lock_value_reads_as_a_definite_no(registry):
    result = security_reader.check_screensaver_secure()

    assert result["enabled"] is False
    assert result["available"] is True


# -- auditpol and Get-ExecutionPolicy: two readers that answered about
#    something other than what the card claims ---------------------------------

#: Verbatim from `auditpol /get /category:*` on this machine, run elevated,
#: 2026-08-29. The word "Enabled" appears NOWHERE in auditpol's output -- the
#: settings are Success, Failure, "Success and Failure" and "No Auditing" --
#: so the old parser, which counted lines containing "Enabled", counted zero
#: on every machine in the world and rendered "No Categories Enabled" in red.
#: 12 of this machine's 60 subcategories are auditing.
REAL_AUDITPOL = """System audit policy
Category/Subcategory                      Setting
System
  Security System Extension               No Auditing
  System Integrity                        Success and Failure
  IPsec Driver                            No Auditing
  Other System Events                     Success and Failure
  Security State Change                   Success
Logon/Logoff
  Logon                                   Success and Failure
  Logoff                                  Success
  Account Lockout                         Success
  IPsec Main Mode                         No Auditing
"""


def test_auditpol_categories_are_counted_by_their_real_setting(monkeypatch):
    monkeypatch.setattr(security_reader, "_cmd_run",
                        lambda *a, **k: (0, REAL_AUDITPOL, ""))

    result = security_reader.check_audit_policy()

    assert result["available"] is True
    assert result["audited"] == 6, "counted the subcategories that audit"
    assert result["total"] == 9
    assert result["color"] != "red"


def test_a_machine_auditing_nothing_is_the_red_case(monkeypatch):
    nothing = REAL_AUDITPOL.replace("Success and Failure", "No Auditing")
    nothing = nothing.replace("     Success\n", "     No Auditing\n")
    monkeypatch.setattr(security_reader, "_cmd_run",
                        lambda *a, **k: (0, nothing, ""))

    result = security_reader.check_audit_policy()

    assert result["audited"] == 0
    assert result["color"] == "red"


def test_the_execution_policy_read_is_the_machines_not_this_shells(powershell):
    """Get-ExecutionPolicy with no scope returns the EFFECTIVE policy of the
    process it runs in. Launched from a shell started with -ExecutionPolicy
    Bypass, this reader reported "Bypass" -- a fact about its own launcher,
    not about the machine. The control's own on_steps write LocalMachine, so
    LocalMachine is what has to be read back."""
    powershell.append(("-Scope LocalMachine", (0, "RemoteSigned", "")))
    powershell.append(("Get-ExecutionPolicy", (0, "Bypass", "")))

    result = security_reader.check_ps_execution_policy()

    assert result["policy"] == "RemoteSigned"
