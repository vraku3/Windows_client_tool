"""Defender readers that answered without having read anything.

Task 6 binds every Defender reader into the catalog, and binding them meant
running them against this machine. Four of them were answering with a value
they never obtained:

  check_defender_cloud_timeout   read prefs["CloudTimeout"], a field
      Get-MpPreference does not return. The `.get(field, 50)` default then
      rendered "50s / green" on every machine in the world. The real field is
      CloudExtendedTimeout, and on this machine it is 0.

  check_defender_exclusions      counted the length of each exclusion list.
      Unelevated, Get-MpPreference does not refuse -- it answers with
      ['N/A: Must be an administrator to view exclusions'], one string. Three
      redaction markers were counted as "3 exclusions / amber".

  _threat_check                  coloured a refused read GREEN (None is not
      in (0, 6, 9)) while reporting enabled=False to the model. Green to the
      eye, False to the catalog, and both from a read that never happened.

  check_exploit_protection_aslr  returned a hardcoded "enabled": True whatever
      the two registry values said, so the catalog would have read a constant.

The `['N/A: Must be an administrator...']` strings below are what this machine
really returned on 2026-08-28.
"""
import pytest

from modules.security_dashboard import security_reader
from modules.security_dashboard import snapshots


REDACTED = ["N/A: Must be an administrator to view exclusions"]


@pytest.fixture
def prefs(monkeypatch):
    """Serve a Get-MpPreference dict without touching the machine."""
    state = {"data": {}, "reason": None}

    monkeypatch.setattr(snapshots, "mp_preference", lambda: state["data"])
    monkeypatch.setattr(
        snapshots, "unavailable",
        lambda name: state["reason"] if name == "mp_preference" else None)
    return state


def _detail(result, label):
    for name, value in result["details"]:
        if name == label:
            return value
    return None


# -- _threat_check (Ruling 17) ------------------------------------------------

def test_a_refused_threat_action_read_is_not_coloured_green(prefs):
    prefs["reason"] = "Access is denied."

    result = security_reader.check_defender_threat_severe()

    assert result["available"] is False
    assert result["color"] == "amber", "a read that was refused rendered green"
    assert "Access is denied" in _detail(result, "Severe Threat Action")


def test_a_refused_threat_action_read_does_not_reach_the_catalog_as_a_value(prefs):
    """SecurityControl.read() must return None, not the reader's `enabled`.

    Through the real catalog entry, so the reader's `available` key and the
    control's binding are checked together.
    """
    from modules.security_dashboard.catalog import load_catalog

    prefs["reason"] = "Access is denied."

    assert load_catalog()["defender_threat_severe"].read() is None


def test_a_readable_threat_action_carries_its_numeric_action_code(prefs):
    prefs["data"] = {"SevereThreatDefaultAction": 2}

    result = security_reader.check_defender_threat_severe()

    assert result["available"] is True
    assert result["value"] == 2, "the catalog compares action codes, not labels"
    assert result["status"] == "Quarantine"


def test_an_unset_threat_action_is_still_reported_as_read(prefs):
    prefs["data"] = {"SevereThreatDefaultAction": 0}

    result = security_reader.check_defender_threat_severe()

    assert result["available"] is True
    assert result["value"] == 0
    assert result["color"] == "red", "0 (Default) keeps the reader's polarity"


# -- check_defender_cloud_timeout --------------------------------------------

def test_cloud_timeout_reads_the_field_get_mppreference_actually_returns(prefs):
    prefs["data"] = {"CloudExtendedTimeout": 0}

    result = security_reader.check_defender_cloud_timeout()

    assert result["available"] is True
    assert result["seconds"] == 0
    assert "50" not in result["status"], (
        "reported 50s from a .get() default on a field that does not exist")


def test_cloud_timeout_at_the_maximum_is_green(prefs):
    prefs["data"] = {"CloudExtendedTimeout": 50}

    result = security_reader.check_defender_cloud_timeout()

    assert result["seconds"] == 50
    assert result["color"] == "green"


def test_a_refused_cloud_timeout_read_says_so(prefs):
    prefs["reason"] = "Access is denied."

    result = security_reader.check_defender_cloud_timeout()

    assert result["available"] is False
    assert result.get("seconds") is None


# -- check_defender_exclusions -----------------------------------------------

def test_redacted_exclusions_are_not_counted_as_exclusions(prefs):
    prefs["data"] = {"ExclusionPath": list(REDACTED),
                     "ExclusionProcess": list(REDACTED),
                     "ExclusionExtension": list(REDACTED)}

    result = security_reader.check_defender_exclusions()

    assert result["available"] is False
    assert "3" not in result["status"], (
        "counted three 'must be an administrator' markers as three exclusions")
    assert "administrator" in str(result["details"]).lower()


def test_genuinely_empty_exclusions_read_as_zero(prefs):
    prefs["data"] = {"ExclusionPath": [], "ExclusionProcess": [],
                     "ExclusionExtension": []}

    result = security_reader.check_defender_exclusions()

    assert result["available"] is True
    assert result["total"] == 0
    assert result["color"] == "green"


def test_real_exclusions_are_counted(prefs):
    prefs["data"] = {"ExclusionPath": [r"C:\Tools", r"D:\Games"],
                     "ExclusionProcess": ["msbuild.exe"],
                     "ExclusionExtension": []}

    result = security_reader.check_defender_exclusions()

    assert result["available"] is True
    assert result["total"] == 3
    assert _detail(result, "Paths") == "2"


def test_a_refused_exclusion_read_says_so(prefs):
    prefs["reason"] = "Access is denied."

    result = security_reader.check_defender_exclusions()

    assert result["available"] is False
    assert result.get("total") is None


def test_the_exclusions_are_listed_not_just_counted(prefs):
    """A count an admin cannot see into is useless -- the whole point is which
    paths are exempt, so they can be reviewed and removed."""
    prefs["data"] = {"ExclusionPath": [r"C:\Tools", r"D:\Games"],
                     "ExclusionProcess": ["msbuild.exe"],
                     "ExclusionExtension": [], "ExclusionIpAddress": []}

    result = security_reader.check_defender_exclusions()

    assert _detail(result, "Paths excluded") == r"C:\Tools; D:\Games"
    assert _detail(result, "Processes excluded") == "msbuild.exe"


def test_ip_address_exclusions_are_counted(prefs):
    prefs["data"] = {"ExclusionPath": [], "ExclusionProcess": [],
                     "ExclusionExtension": [],
                     "ExclusionIpAddress": ["10.0.0.5", "1.2.3.4"]}

    result = security_reader.check_defender_exclusions()

    assert result["total"] == 2
    assert _detail(result, "IP addresses") == "2"


def test_a_long_exclusion_list_is_capped_in_the_detail(prefs):
    prefs["data"] = {"ExclusionPath": [f"C:\\p{i}" for i in range(20)],
                     "ExclusionProcess": [], "ExclusionExtension": [],
                     "ExclusionIpAddress": []}

    result = security_reader.check_defender_exclusions()

    assert _detail(result, "Paths") == "20"
    text = _detail(result, "Paths excluded")
    assert text.endswith("(+12 more)")


# -- check_defender_cloud_block_level ----------------------------------------

def test_cloud_block_level_uses_microsofts_own_value_names(prefs):
    """Set-MpPreference -CloudBlockLevel: 0 Default, 1 Moderate, 2 High,
    4 High+, 6 ZeroTolerance. The table said 2 was "Low"."""
    prefs["data"] = {"CloudBlockLevel": 2}

    result = security_reader.check_defender_cloud_block_level()

    assert result["level"] == 2, "the catalog needs the number, not the label"
    assert result["status"] == "High"
    assert result["color"] == "green", (
        "High is the level the catalog asks for; it cannot render as amber")


def test_cloud_block_level_default_is_not_green(prefs):
    prefs["data"] = {"CloudBlockLevel": 0}

    result = security_reader.check_defender_cloud_block_level()

    assert result["level"] == 0
    assert result["status"] == "Default"
    assert result["color"] == "amber"


# -- check_exploit_protection_aslr -------------------------------------------

def test_aslr_enabled_reflects_the_registry_rather_than_a_constant(
        mitigation, monkeypatch):
    """MoveImages 0 is bottom-up randomization OFF. The reader used to say
    enabled=True regardless, so the catalog read a constant."""
    key = (r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager"
           r"\Memory Management")

    def reg(k, value):
        if k == key and value == "MoveImages":
            return 0
        if k == key and value == "HighEntropyASLROn64Bit":
            return 0
        return None

    monkeypatch.setattr(security_reader, "_reg_read", reg)

    result = security_reader.check_exploit_protection_aslr()

    assert result["enabled"] is False
    assert result["color"] == "amber"


def test_aslr_at_the_windows_default_reads_as_enabled(mitigation):

    result = security_reader.check_exploit_protection_aslr()

    assert result["enabled"] is True
    assert result["color"] == "green"


# -- check_pua_protection ----------------------------------------------------
#
# Verified against the live cmdlet on 2026-08-28, not from memory:
#   (Get-Command Set-MpPreference).Parameters['PUAProtection'].ParameterType
#   -> Disabled=0  Enabled=1  AuditMode=2
# The reader's table read 1 as "Audit Mode" and 2 as "On", and coloured green
# at 2 -- so a machine that was only AUDITING potentially unwanted apps was
# told it was blocking them. This machine reads 2.

def test_pua_blocking_is_the_green_state_not_audit(prefs):
    prefs["data"] = {"PUAProtection": 1}

    result = security_reader.check_pua_protection()

    assert result["level"] == 1
    assert result["status"] == "On (Block)"
    assert result["color"] == "green"


def test_pua_audit_mode_is_not_reported_as_on(prefs):
    prefs["data"] = {"PUAProtection": 2}

    result = security_reader.check_pua_protection()

    assert result["level"] == 2
    assert result["status"] == "Audit Mode"
    assert result["color"] == "amber", "audit-only was rendered as protected"


def test_pua_off_is_red(prefs):
    prefs["data"] = {"PUAProtection": 0}

    result = security_reader.check_pua_protection()

    assert result["status"] == "Off"
    assert result["color"] == "red"


# -- the threat-action enum has no 0 -----------------------------------------
#
# ThreatAction: Clean=1 Quarantine=2 Remove=3 Allow=6 UserDefined=8
#               NoAction=9 Block=10 None=11
# Get-MpPreference reports 0 for a severity nobody has configured, but 0 is
# not a member of the enum, so `Set-MpPreference -LowThreatDefaultAction 0`
# cannot be the off/revert step -- it would fail parameter binding.

def test_the_unconfigured_threat_action_code_is_labelled(prefs):
    prefs["data"] = {"SevereThreatDefaultAction": 11}

    result = security_reader.check_defender_threat_severe()

    assert result["value"] == 11
    assert "Unknown" not in result["status"]
    assert result["color"] == "red", (
        "no configured action is not a protected state")


def test_no_threat_control_tries_to_set_a_value_the_enum_does_not_have():
    from modules.security_dashboard.catalog import load_catalog

    valid = {"Clean", "Quarantine", "Remove", "Allow", "UserDefined",
             "NoAction", "Block", "None"}
    for cid, control in load_catalog().items():
        if not cid.startswith("defender_threat_"):
            continue
        for step in control.on_steps + control.off_steps:
            action = step["command"].rsplit(" ", 1)[1]
            assert action in valid, f"{cid}: {action!r} is not a ThreatAction"


# -- Get-ProcessMitigation: one call, three readers ---------------------------
#
# The real shape, from this machine on 2026-08-28: one object per mitigation
# group at the TOP level, each field a tri-state (0 NOTSET, 1 ON, 2 OFF).
# check_exploit_protection_cfg looked for a key named "CFG" INSIDE each group,
# which matches nothing, so it reported "Off / red" everywhere.

REAL_MITIGATION = {
    "ProcessName": "System", "Source": "System Defaults", "Id": 0,
    "Dep": {"OverrideDEP": False, "Enable": 0, "EmulateAtlThunks": 0},
    "Aslr": {"OverrideForceRelocateImages": False, "ForceRelocateImages": 0,
             "RequireInfo": 0, "BottomUp": 0, "HighEntropy": 0},
    "Cfg": {"OverrideCfg": False, "Enable": 0, "SuppressExports": 0},
}


@pytest.fixture
def mitigation(monkeypatch):
    state = {"data": dict(REAL_MITIGATION), "reason": None, "calls": 0}

    def fetch():
        state["calls"] += 1
        return state["data"]

    monkeypatch.setattr(snapshots, "process_mitigation", fetch)
    monkeypatch.setattr(
        snapshots, "unavailable",
        lambda name: state["reason"] if name == "process_mitigation" else None)
    monkeypatch.setattr(security_reader, "_reg_read", lambda k, v: None)
    return state


def test_the_three_exploit_readers_share_one_cmdlet_result(mitigation):
    security_reader.check_exploit_protection_system()
    security_reader.check_exploit_protection_cfg()
    security_reader.check_exploit_protection_aslr()

    assert mitigation["calls"] <= 3, "each reader fetched separately"
    # the point of the snapshot: they ask the module, not the machine
    assert mitigation["calls"] >= 1


def test_cfg_at_the_windows_default_is_not_reported_as_off(mitigation):
    result = security_reader.check_exploit_protection_cfg()

    assert result["available"] is True
    assert result["color"] == "amber", "NOTSET was rendered as an explicit Off"
    assert "default" in result["status"].lower()


def test_cfg_explicitly_enabled_reads_as_on(mitigation):
    mitigation["data"] = dict(REAL_MITIGATION, Cfg={"Enable": 1})

    result = security_reader.check_exploit_protection_cfg()

    assert result["enabled"] is True
    assert result["color"] == "green"


def test_cfg_explicitly_disabled_is_red(mitigation):
    mitigation["data"] = dict(REAL_MITIGATION, Cfg={"Enable": 2})

    result = security_reader.check_exploit_protection_cfg()

    assert result["enabled"] is False
    assert result["color"] == "red"


def test_a_refused_mitigation_read_is_not_a_verdict(mitigation):
    mitigation["reason"] = "Access is denied."

    for reader in (security_reader.check_exploit_protection_cfg,
                   security_reader.check_exploit_protection_system):
        result = reader()
        assert result["available"] is False, reader.__name__
        assert result.get("enabled") is not True, reader.__name__


def test_a_machine_at_windows_defaults_is_not_reported_as_unprotected(mitigation):
    """0 explicitly-enabled mitigations was rendered "0 mitigations active"
    in red. Nothing is overridden there; nothing is disabled either."""
    result = security_reader.check_exploit_protection_system()

    assert result["color"] == "green"
    assert result["enabled"] is True


def test_an_explicitly_disabled_mitigation_is_what_turns_it_red(mitigation):
    mitigation["data"] = dict(REAL_MITIGATION, Dep={"Enable": 2})

    result = security_reader.check_exploit_protection_system()

    assert result["color"] == "red"
    assert result["enabled"] is False
    assert "Dep.Enable" in str(result["details"])
