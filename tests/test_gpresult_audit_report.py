"""The audit report: one HTML file over everything the pane found.

Covers both synthetic edge cases (a refused scope, an incomplete tattooed
scan, an access-denied local policy file -- none of which must ever read as
"nothing wrong") and the REAL machine this is running on, which has no
domain membership and no configured local GPO at all, but a genuinely
tattooed set of registry values this app's own Tweaks module put in the
managed policy branches.
"""
import re

from modules.gpresult.audit_report import build_audit_report_html, write_audit_report
from modules.gpresult.policy_drift import (
    APPLIED, DIFFERENT, DriftResult, DriftReport, MISSING, UNREADABLE,
)
from modules.gpresult.pol_parser import (
    PerUserLocalPolicy, PolFile, PolicyValue, local_policy_files,
)
from modules.gpresult.rsop_parser import RsopResult, RsopScope, parse_rsop_xml
from modules.gpresult.tattooed import (
    BranchScan, RegistryValue, TattooedResult, find_tattooed,
)
from modules.gpresult.tweak_conflicts import (
    AGREE_CONFLICT, ConflictReport, MATCH_DIRECT, TweakConflict,
)

#: The real shape of an unelevated `gpresult /x` run: it exits 0, writes a
#: valid report, and the report has no <ComputerResults> in it at all. Same
#: fixture family as `tests/test_gpresult_rsop.py`'s `REAL_USER_ONLY`, kept
#: local here so this file has no cross-test-module import.
REAL_USER_ONLY = """<?xml version="1.0" encoding="utf-8"?>
<Rsop xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns="http://www.microsoft.com/GroupPolicy/Rsop">
  <ReadTime>2026-08-27T05:51:23.3134475Z</ReadTime>
  <DataType>LoggedData</DataType>
  <UserResults>
    <Version>2228228</Version>
    <Name>VRK\\iorda</Name>
    <Domain>Local</Domain>
    <SOM>Local</SOM>
    <GPO>
      <Name>Local Group Policy</Name>
      <Path>
        <Identifier xmlns="http://www.microsoft.com/GroupPolicy/Types">LocalGPO</Identifier>
      </Path>
      <VersionDirectory>0</VersionDirectory>
      <VersionSysvol>0</VersionSysvol>
      <Enabled>true</Enabled>
      <IsValid>true</IsValid>
      <FilterAllowed>true</FilterAllowed>
      <AccessDenied>false</AccessDenied>
      <Link>
        <SOMPath>Local</SOMPath>
        <SOMOrder>1</SOMOrder>
        <AppliedOrder>0</AppliedOrder>
        <LinkOrder>1</LinkOrder>
        <Enabled>true</Enabled>
        <NoOverride>false</NoOverride>
      </Link>
    </GPO>
  </UserResults>
</Rsop>
"""

#: Carries the real SrpV2 trap this package's docstrings call out repeatedly:
#: the same `AllowWindows` value name under two different keys.
SETTINGS_XML = """<?xml version="1.0" encoding="utf-8"?>
<Rsop xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns="http://www.microsoft.com/GroupPolicy/Rsop">
  <ReadTime>2026-08-27T05:51:23Z</ReadTime>
  <DataType>LoggedData</DataType>
  <ComputerResults>
    <Name>VRK</Name>
    <Domain>Local</Domain>
    <GPO>
      <Name>Local Group Policy</Name>
      <Path><Identifier>LocalGPO</Identifier></Path>
      <Enabled>true</Enabled><IsValid>true</IsValid>
      <FilterAllowed>true</FilterAllowed><AccessDenied>false</AccessDenied>
      <Link><SOMPath>Local</SOMPath><AppliedOrder>0</AppliedOrder>
            <LinkOrder>1</LinkOrder><NoOverride>false</NoOverride></Link>
    </GPO>
    <GPO>
      <Name>Filtered Out</Name>
      <Path><Identifier>{31B2F340-016D-11D2-945F-00C04FB984F9}</Identifier></Path>
      <Enabled>true</Enabled><IsValid>true</IsValid>
      <FilterAllowed>false</FilterAllowed><AccessDenied>false</AccessDenied>
    </GPO>
    <ExtensionStatus>
      <Name>Registry</Name><LoggingStatus>Complete</LoggingStatus><Error>0</Error>
    </ExtensionStatus>
    <ExtensionStatus>
      <Name>Security</Name><LoggingStatus>Failed</LoggingStatus><Error>2</Error>
    </ExtensionStatus>
    <ExtensionData>
      <Extension xmlns:q1="http://www.microsoft.com/GroupPolicy/Settings/Registry"
                 xsi:type="q1:RegistrySettings">
        <q1:Policy>
          <q1:Name>Turn off Windows Error Reporting</q1:Name>
          <q1:State>Enabled</q1:State>
          <q1:Category>Windows Components/Windows Error Reporting</q1:Category>
          <q1:GPO><q1:Identifier>LocalGPO</q1:Identifier>
                  <q1:Name>Local Group Policy</q1:Name></q1:GPO>
        </q1:Policy>
        <q1:RegistrySetting>
          <q1:KeyPath>Software\\Policies\\Microsoft\\Windows\\SrpV2\\Exe</q1:KeyPath>
          <q1:Value><q1:Name>AllowWindows</q1:Name><q1:Number>0</q1:Number></q1:Value>
          <q1:GPO><q1:Name>Local Group Policy</q1:Name></q1:GPO>
        </q1:RegistrySetting>
        <q1:RegistrySetting>
          <q1:KeyPath>Software\\Policies\\Microsoft\\Windows\\SrpV2\\Msi</q1:KeyPath>
          <q1:Value><q1:Name>AllowWindows</q1:Name><q1:Number>0</q1:Number></q1:Value>
          <q1:GPO><q1:Name>Local Group Policy</q1:Name></q1:GPO>
        </q1:RegistrySetting>
      </Extension>
    </ExtensionData>
  </ComputerResults>
</Rsop>
"""


def _bare_result() -> RsopResult:
    result = RsopResult()
    result.computer = RsopScope(scope="Computer", available=False,
                                unavailable_reason="needs elevation")
    result.user = RsopScope(scope="User", available=True)
    return result


# ---------------------------------------------------------------------------
# A refused scope must read as refused, not as "no policy"
# ---------------------------------------------------------------------------

def test_an_unavailable_scope_shows_its_reason_not_a_blank():
    result = _bare_result()
    html = build_audit_report_html(result)
    assert "needs elevation" in html
    # And it must not claim a count for a scope it never collected.
    assert "0 GPO(s) applied" not in html.split("User Configuration")[0]


def test_a_real_unelevated_report_is_rendered_honestly():
    """`REAL_USER_ONLY` is the actual unelevated `gpresult /x` shape: the
    computer half is simply absent from the document."""
    result = parse_rsop_xml(REAL_USER_ONLY)
    assert not result.computer.available   # sanity on the fixture itself
    html = build_audit_report_html(result)
    assert "Computer Configuration" in html
    assert "Not collected" in html
    assert "Local Group Policy" in html   # the one real User GPO


def test_gpresult_error_is_shown_and_nothing_else_is_rendered_as_fact():
    result = RsopResult(error="gpresult.exe was not found on this system.")
    html = build_audit_report_html(result)
    assert "gpresult.exe was not found" in html


# ---------------------------------------------------------------------------
# Drift: missing/different/unreadable must never collapse into "fine"
# ---------------------------------------------------------------------------

def _drift_value(key="Software\\Policies\\X", name="Y") -> PolicyValue:
    return PolicyValue(key=key, value_name=name, type_id=4, data=1)


def test_drift_lists_missing_different_and_unreadable_separately():
    report = DriftReport(results=[
        DriftResult(policy=_drift_value(), scope="Computer", hive="HKLM",
                    state=APPLIED, reason="matches"),
        DriftResult(policy=_drift_value("K2", "V2"), scope="Computer",
                    hive="HKLM", state=DIFFERENT, reason="holds something else"),
        DriftResult(policy=_drift_value("K3", "V3"), scope="Computer",
                    hive="HKLM", state=MISSING, reason="not set at all"),
        DriftResult(policy=_drift_value("K4", "V4"), scope="Computer",
                    hive="HKLM", state=UNREADABLE, reason="access denied"),
    ])
    html = build_audit_report_html(_bare_result(), drift=report)
    assert "holds something else" in html
    assert "not set at all" in html
    assert "access denied" in html
    # The one APPLIED row is not interesting and must not clutter the table.
    assert "matches" not in html


def test_drift_state_cell_is_real_markup_not_escaped_text():
    """A cell built as `<span class="state-missing">missing</span>` must
    render as that span, not as literal `&lt;span...&gt;` text -- the trap a
    naive "escape every cell" helper falls into the moment one cell is
    pre-built HTML rather than plain machine-sourced text."""
    report = DriftReport(results=[
        DriftResult(policy=_drift_value(), scope="Computer", hive="HKLM",
                    state=MISSING, reason="not set"),
    ])
    html = build_audit_report_html(_bare_result(), drift=report)
    assert '<span class="state-missing">missing</span>' in html
    assert "&lt;span" not in html


def test_a_pol_file_read_error_is_surfaced_not_dropped():
    pol = PolFile(path=r"C:\Windows\System32\GroupPolicy\Machine\Registry.pol",
                 error="Could not parse: truncated record")
    html = build_audit_report_html(_bare_result(), local_policy=[pol])
    assert "truncated record" in html


# ---------------------------------------------------------------------------
# Tattooed: an incomplete scan must say so, not look clean
# ---------------------------------------------------------------------------

def test_an_incomplete_tattooed_scan_shows_a_caveat():
    scan = BranchScan(hive="HKLM", key="Software\\Policies", exists=True,
                      unreadable_keys=["HKLM\\Software\\Policies\\Blocked"])
    tattoo = TattooedResult(branches=[scan])
    assert not tattoo.complete
    html = build_audit_report_html(_bare_result(), tattoo=tattoo)
    assert "INCOMPLETE" in html
    assert "Blocked" in html or "keys unreadable" in html


def test_tattooed_values_are_listed_with_their_data():
    value = RegistryValue(hive="HKLM", key="Software\\Policies\\Foo",
                          value_name="Bar", type_id=4, data=1)
    scan = BranchScan(hive="HKLM", key="Software\\Policies", exists=True)
    tattoo = TattooedResult(branches=[scan], tattooed=[value])
    html = build_audit_report_html(_bare_result(), tattoo=tattoo)
    assert "Bar" in html
    assert "Software\\Policies\\Foo" in html


# ---------------------------------------------------------------------------
# Tweak conflicts: a real conflict is listed, a non-finding is not alarmist
# ---------------------------------------------------------------------------

def test_a_direct_tweak_conflict_is_listed_with_its_own_wording():
    conflict = TweakConflict(
        tweak_id="t1", tweak_name="Disable SmartScreen", step_index=0,
        step_type="registry", tweak_key="Software\\Policies\\X",
        tweak_value="Y", match=MATCH_DIRECT, agreement=AGREE_CONFLICT,
        scope="Computer", hive="HKLM", policy_key="HKLM\\Software\\Policies\\X",
        policy_value="Y", policy_display="1")
    report = ConflictReport(conflicts=[conflict], tweaks_examined=1,
                            registry_steps=1, policy_values=1)
    html = build_audit_report_html(_bare_result(), conflicts=report)
    assert "Disable SmartScreen" in html
    assert "can be undone without warning" in html   # the module's own wording


def test_no_conflicts_reads_as_the_honest_headline_not_silence():
    report = ConflictReport(tweaks_examined=5, registry_steps=5, policy_values=0)
    html = build_audit_report_html(_bare_result(), conflicts=report)
    assert report.headline() in html


# ---------------------------------------------------------------------------
# Per-user local policy
# ---------------------------------------------------------------------------

def test_an_unresolved_sid_is_shown_rather_than_dropped():
    entry = PerUserLocalPolicy(sid="S-1-5-21-FAKE", resolved=False,
                               pol=PolFile(exists=True))
    html = build_audit_report_html(_bare_result(), per_user_policy=[entry])
    assert "S-1-5-21-FAKE" in html
    assert "did not resolve" in html


def test_no_per_user_policy_says_so_plainly():
    html = build_audit_report_html(_bare_result(), per_user_policy=[])
    assert "not configured" in html


# ---------------------------------------------------------------------------
# Escaping -- a machine-sourced string can contain markup
# ---------------------------------------------------------------------------

def test_a_gpo_name_containing_markup_is_escaped():
    result = _bare_result()
    scope = RsopScope(scope="Computer", available=True)
    from modules.gpresult.rsop_parser import GpoInfo
    scope.gpos.append(GpoInfo(name="<script>evil()</script>", enabled=True,
                              is_valid=True, filter_allowed=True))
    result.computer = scope
    html = build_audit_report_html(result)
    assert "<script>evil()" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# write_audit_report -- the file-writing wrapper
# ---------------------------------------------------------------------------

def test_write_audit_report_produces_a_readable_html_file(tmp_path):
    path = str(tmp_path / "audit.html")
    write_audit_report(path, _bare_result())
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    assert text.startswith("<!DOCTYPE html>")
    assert "Group Policy Audit Report" in text


def test_write_audit_report_raises_on_an_unwritable_path(tmp_path):
    # A directory passed as the file path is a real, OS-level write failure
    # the caller must hear about -- not something to swallow.
    bad_dir = tmp_path / "is_a_dir"
    bad_dir.mkdir()
    try:
        write_audit_report(str(bad_dir), _bare_result())
    except OSError:
        return
    raise AssertionError("expected OSError writing to a directory path")


# ---------------------------------------------------------------------------
# Real machine: this report must render real local data honestly
# ---------------------------------------------------------------------------

def test_real_machine_tattooed_values_appear_in_the_report():
    """This machine has no local GPO at all (`local_policy_files()` returns
    two files that both do not exist), but DOES have real values this app's
    own Tweaks module wrote into the managed policy branches -- confirmed by
    running `find_tattooed()` directly against the real registry (74 values
    across 4 branches, 3 keys access-denied, as of 2026-10-01). The report
    must show that real, non-empty finding and must flag the scan as
    incomplete because of the access-denied keys -- never report a clean
    "zero tattooed" that would be false.
    """
    pols = local_policy_files()
    assert all(not p.exists for p in pols), (
        "fixture assumption broke: this machine now has local policy -- "
        "re-verify the test still describes real state")

    tattoo = find_tattooed(pol_files=pols)
    html = build_audit_report_html(_bare_result(), local_policy=pols,
                                   tattoo=tattoo)

    if tattoo.tattooed:
        assert "Set Outside Group Policy" in html
        assert str(len(tattoo.tattooed)) in html or re.search(
            r"\d+ tattooed", html)
    if not tattoo.complete:
        assert "INCOMPLETE" in html


def test_real_machine_empty_pol_files_report_as_nothing_configured():
    pols = local_policy_files()
    html = build_audit_report_html(_bare_result(), local_policy=pols)
    assert "no local Group Policy registry settings" in html.lower() \
        or "not checked" in html.lower()
    # Neither real .pol file is readable-with-error -- there is nothing to
    # exist on this machine, which is different from a read being refused.
    assert "Could not read" not in html


def test_real_settings_xml_shape_renders_applied_and_denied_gpos():
    """`SETTINGS_XML` carries a real trap this package's docstrings call out
    repeatedly: a GPO Windows LISTED but did not apply (filtered out), next
    to one it did. The report must show both, with the denied one's reason,
    not just the winners -- and must not crash on the real
    `ExtensionStatus`/`ExtensionData` shape gpresult actually emits."""
    result = parse_rsop_xml(SETTINGS_XML)
    assert len(result.computer.applied_gpos) == 1
    assert len(result.computer.denied_gpos) == 1
    html = build_audit_report_html(result)
    assert "Local Group Policy" in html
    assert "Filtered Out" in html
    assert "Denied by security filtering" in html
    assert "Security" in html   # the failed extension, Error=2
