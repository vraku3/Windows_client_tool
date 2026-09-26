"""Firewall exposure audit: Qt-free engine, fakes plus the real registry."""
import sys

import pytest

from modules.firewall_rules import firewall_audit as fa
from modules.firewall_rules.firewall_manager_module import FirewallRule


def _rule(**kw):
    base = dict(name="r", enabled="Yes", direction="In", action="Allow", protocol="TCP",
                local_port="Any", remote_port="Any", program="", profile="Domain,Private,Public")
    base.update(kw)
    return FirewallRule(**base)


def test_parse_registry_rule_joins_repeated_keys_and_defaults_profile():
    v = ("v2.33|Action=Allow|Active=TRUE|Dir=In|Protocol=6|Profile=Domain|Profile=Public|"
         "LPort=80|LPort=443|RA4=LocalSubnet|RA6=LocalSubnet|App=C:\\x\\a.exe|Svc=Foo|Name=Web|PFN=pkg|")
    rule, extra = fa.parse_registry_rule(v)
    assert (rule.enabled, rule.direction, rule.action, rule.protocol) == ("Yes", "In", "Allow", "TCP")
    assert rule.local_port == "80,443" and rule.profile == "Domain,Public"
    assert extra.remote_ip == "LocalSubnet" and extra.service == "Foo" and extra.package == "pkg"
    rule2, _ = fa.parse_registry_rule("v2.33|Action=Block|Active=FALSE|Dir=Out|Name=n|")
    assert rule2.profile == fa.ALL_PROFILES and rule2.protocol == "Any" and rule2.enabled == "No"
    assert fa.parse_registry_rule("garbage") is None


def test_resource_names_get_full_dll_path_before_resolving():
    seen = []
    fa.parse_registry_rule("v2.33|Action=Allow|Active=TRUE|Dir=In|Name=@FirewallAPI.dll,-1|",
                           lambda n: seen.append(n) or "Resolved")
    assert seen and seen[0].startswith("@%SystemRoot%") and seen[0].endswith("FirewallAPI.dll,-1")


def test_open_inbound_public_is_high_but_scoped_or_icmp_is_not():
    rules = [_rule(name="open", profile="Public"),
             _rule(name="icmp", protocol="ICMPv6", profile="Public"),
             _rule(name="prog", program=r"C:\a.exe", profile="Public"),
             _rule(name="disabled", enabled="No", profile="Public")]
    extras = [fa.RuleExtra(), fa.RuleExtra(), fa.RuleExtra(), fa.RuleExtra()]
    got = fa.audit(rules, extras, exists=lambda p: True)
    high = [f for f in got if f.key == "open_public"]
    assert len(high) == 1 and high[0].severity == "high" and high[0].rule_names == ["open"]


def test_package_or_service_bound_rule_is_not_open():
    r = _rule(name="pkg")
    assert not fa.chip_matches("any_any", r, fa.RuleExtra(package="Some.App_x"))
    assert not fa.chip_matches("any_any", r, fa.RuleExtra(service="Spooler"))
    assert fa.chip_matches("any_any", r, fa.RuleExtra())
    assert not fa.chip_matches("any_any", r, fa.RuleExtra(remote_ip="LocalSubnet"))


def test_profile_off_and_default_allow_findings():
    ps = [fa.ProfileState("Public", False, "Allow", "Allow"),
          fa.ProfileState("Domain", None, "", "", "could not read")]
    keys = {f.key for f in fa.audit([], None, ps)}
    assert {"profile_off", "default_allow", "profile_unread"} <= keys


def test_unread_profile_is_not_reported_as_off():
    ps = fa.ProfileState("Domain", None, "", "", "x")
    assert not any(f.key == "profile_off" for f in fa.audit([], None, [ps]))


def test_program_missing_duplicates_broad_ranges_and_allow_vs_block():
    rules = [_rule(name="a", program=r"C:\gone.exe"),
             _rule(name="b", program=r"C:\gone.exe"),
             _rule(name="wide", local_port="1000-3000"),
             _rule(name="blk", action="Block", program=r"C:\gone.exe")]
    keys = {f.key: f for f in fa.audit(rules, exists=lambda p: False)}
    assert "prog_missing" in keys and len(keys["prog_missing"].rule_names) == 3
    assert set(keys["duplicates"].rule_names) == {"a", "b"}
    assert keys["broad_ports"].rule_names == ["wide"]
    assert "allow_vs_block" in keys


def test_icmp_type_makes_rules_distinct():
    r1, r2 = _rule(name="1", protocol="ICMPv6", program="x"), _rule(name="2", protocol="ICMPv6", program="x")
    e1, e2 = fa.RuleExtra(icmp="128:0"), fa.RuleExtra(icmp="129:0")
    assert not any(f.key == "duplicates" for f in fa.audit([r1, r2], [e1, e2], exists=lambda p: True))


def test_chip_counts_and_port_span():
    rules = [_rule(name="x"), _rule(name="y", enabled="No", direction="Out", action="Block")]
    c = fa.chip_counts(rules)
    assert c["all"] == 2 and c["enabled"] == 1 and c["disabled"] == 1
    assert c["in_allow"] == 1 and c["out_block"] == 1
    assert fa.port_span("80,443") == 1 and fa.port_span("1-65535") == 65535 and fa.port_span("Any") == 65535


def test_profile_from_registry_defaults_are_windows_defaults():
    p = fa._profile_from("Public", lambda n: {"EnableFirewall": 1}.get(n))
    assert p.enabled is True and p.default_inbound == "Block" and p.default_outbound == "Allow" and p.note
    q = fa._profile_from("Public", lambda n: {"EnableFirewall": 0, "DefaultInboundAction": 0,
                                              "DefaultOutboundAction": 1}.get(n))
    assert q.enabled is False and q.default_inbound == "Allow" and q.default_outbound == "Block"


def test_csv_quotes_and_has_header():
    text = fa.rules_to_csv([_rule(name='Say "hi", ok')], [fa.RuleExtra(remote_ip="Any")])
    lines = text.strip().splitlines()
    assert lines[0].startswith("Name,Enabled") and '"Say ""hi"", ok"' in lines[1]


def test_snapshot_failure_is_an_error_not_an_empty_audit(monkeypatch):
    def boom(_r=None):
        raise PermissionError("denied")
    monkeypatch.setattr(fa, "read_registry_rules", boom)
    snap = fa.take_snapshot()
    assert snap.error and not snap.findings


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_real_registry_matches_plausible_shape():
    try:
        rules, extras = fa.read_registry_rules(fa.resolve_indirect)
    except OSError:
        pytest.skip("firewall policy unreadable here")
    assert len(rules) == len(extras) and len(rules) > 50
    assert sum(r.name.startswith("@") for r in rules) < len(rules) * 0.1  # names resolve
    counts = fa.chip_counts(rules, extras)
    assert counts["enabled"] + counts["disabled"] == counts["all"]
    profiles = fa.read_profile_states()
    assert [p.name for p in profiles] == ["Domain", "Private", "Public"]
