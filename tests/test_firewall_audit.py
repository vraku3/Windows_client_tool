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


def test_scoped_open_public_is_distinct_from_open_inbound_and_broad_ports():
    third_party = _rule(name="outlook_udp", protocol="UDP", local_port="6004",
                        program=r"C:\Program Files\Microsoft Office\root\Office16\outlook.exe",
                        profile="Public")
    builtin = _rule(name="dosvc", protocol="TCP", local_port="7680",
                    program=r"%SystemRoot%\system32\svchost.exe", profile="Public")
    discovery = _rule(name="mdns", protocol="UDP", local_port="5353",
                      program=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      profile="Public")
    domain_only = _rule(name="domain_scoped", protocol="UDP", local_port="6004",
                        program=r"C:\a.exe", profile="Domain")
    rules = [third_party, builtin, discovery, domain_only]
    extras = [fa.RuleExtra() for _ in rules]
    got = fa.audit(rules, extras, exists=lambda p: True)
    hit = next(f for f in got if f.key == "scoped_open_public")
    assert hit.rule_names == ["outlook_udp"]
    # None of these should also show up as the fully-open or broad-range findings.
    assert not any(f.key in ("open_public", "broad_ports") for f in got)
    assert fa.chip_matches("scoped_open_public", third_party, fa.RuleExtra())
    assert not fa.chip_matches("scoped_open_public", builtin, fa.RuleExtra())
    assert not fa.chip_matches("scoped_open_public", discovery, fa.RuleExtra())


def test_scoped_open_public_excludes_restricted_remote_and_service_bound():
    restricted = _rule(name="restricted", protocol="TCP", local_port="9000",
                       program=r"C:\a.exe", profile="Public")
    assert fa.chip_matches("scoped_open_public", restricted, fa.RuleExtra(remote_ip="LocalSubnet")) is False
    svc = _rule(name="svc", protocol="TCP", local_port="9000", profile="Public")
    assert fa.chip_matches("scoped_open_public", svc, fa.RuleExtra(service="Foo"))


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_real_registry_program_missing_is_a_handful_not_hundreds():
    """"Program missing" is the orphaned-firewall-rule check: a rule whose
    Program (App=) field names a path that no longer exists on disk, the same
    "registration outlives the uninstall" pattern as the orphaned services and
    orphaned scheduled tasks checks. Measured on this real machine (526 rules,
    305 carrying a non-"System" Program field): 12 rules point at a missing
    program, all explicable (Windows Peer-to-Peer Collaboration Foundation and
    Media Center Extenders rules for optional components not installed on this
    edition; two EdgeWebView rules pinned to a specific already-superseded
    version subfolder) -- a plausible handful, not the hundreds a relative-path
    resolution bug would produce (that exact bug turned 736 healthy drivers
    into 201 false "missing" results in check_orphaned_services before
    %SystemRoot% expansion was fixed there). Guards against the same class of
    regression here: if `expand_program`/`program_missing` ever stopped
    expanding %SystemRoot% or started resolving relative paths against the
    wrong base, this count would jump into the hundreds."""
    try:
        rules, extras = fa.read_registry_rules(fa.resolve_indirect)
    except OSError:
        pytest.skip("firewall policy unreadable here")
    with_program = [r for r in rules if r.program and r.program.strip().lower() != "system"]
    assert len(with_program) > 50  # sanity: most rules here do carry a Program
    missing = [r for r in rules if fa.program_missing(r)]
    # A handful, never a large fraction -- see docstring for the measured shape.
    assert len(missing) < max(20, len(with_program) * 0.15)
    for r in missing:
        # Every flagged rule really did have a Program the check could expand;
        # "System" and unresolved %vars% are never flagged (program_missing's
        # own conservative rules -- an unresolved %var% is unknown, not missing).
        expanded = fa.expand_program(r.program)
        assert expanded and expanded.lower() != "system" and "%" not in expanded
    profiles = fa.read_profile_states()
    assert [p.name for p in profiles] == ["Domain", "Private", "Public"]


def test_group_summary_counts_and_flags_mixed_state():
    rules = [_rule(name="a", enabled="Yes"), _rule(name="b", enabled="No"),
             _rule(name="c", enabled="Yes"), _rule(name="ungrouped", enabled="No")]
    extras = [fa.RuleExtra(grouping="Network Discovery"),
              fa.RuleExtra(grouping="Network Discovery"),
              fa.RuleExtra(grouping="WhatsApp"),
              fa.RuleExtra()]  # no group at all -- must not appear
    groups = {g.name: g for g in fa.group_summary(rules, extras)}
    assert set(groups) == {"Network Discovery", "WhatsApp"}
    nd = groups["Network Discovery"]
    assert (nd.total, nd.enabled, nd.disabled, nd.mixed) == (2, 1, 1, True)
    wa = groups["WhatsApp"]
    assert (wa.total, wa.enabled, wa.mixed) == (1, 1, False)


def test_group_summary_sorts_mixed_groups_first():
    rules = [_rule(name="x", enabled="Yes"), _rule(name="y", enabled="Yes"),
             _rule(name="z", enabled="No")]
    extras = [fa.RuleExtra(grouping="Zebra"), fa.RuleExtra(grouping="Alpha"),
              fa.RuleExtra(grouping="Alpha")]
    groups = fa.group_summary(rules, extras)
    assert groups[0].name == "Alpha" and groups[0].mixed
    assert groups[1].name == "Zebra" and not groups[1].mixed


def test_group_summary_resolves_a_windows_resource_group_name():
    """The registry stores a group as a bare `@dll,-id` string, exactly the
    shape parse_registry_rule's Name field handles -- resolve_group_name must
    apply the same %SystemRoot%-qualifying step before resolving, or every
    Windows-supplied group name comes back as the raw resource string."""
    seen = []
    resolve = lambda full: seen.append(full) or "Network Discovery"  # noqa: E731
    rules = [_rule(name="a", enabled="Yes")]
    extras = [fa.RuleExtra(grouping="@FirewallAPI.dll,-32752")]
    groups = fa.group_summary(rules, extras, resolve)
    assert groups[0].name == "Network Discovery" and groups[0].builtin
    assert seen and seen[0].startswith("@%SystemRoot%") and seen[0].endswith(",-32752")


def test_group_summary_falls_back_to_the_raw_string_when_resolution_fails():
    rules = [_rule(name="a", enabled="Yes")]
    extras = [fa.RuleExtra(grouping="@FirewallAPI.dll,-99999")]
    groups = fa.group_summary(rules, extras, resolve=lambda _n: "")
    assert groups[0].name == "@FirewallAPI.dll,-99999"


def test_group_summary_treats_a_literal_third_party_group_as_non_builtin():
    rules = [_rule(name="a", enabled="Yes")]
    extras = [fa.RuleExtra(grouping="WhatsApp")]
    groups = fa.group_summary(rules, extras)
    assert groups[0].builtin is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_real_registry_has_a_mixed_state_group_worth_a_bulk_toggle():
    """Measured on this real machine (529 rules, 109 named groups): exactly
    three groups are mixed -- Network Discovery (22 enabled / 30 disabled),
    Remote Assistance and Windows Media Player. That is the real, actionable
    case this feature exists for: Windows' own per-profile toggling left a
    group neither fully on nor fully off, and finding that by scanning rows
    one at a time in a 500+ row table is impractical."""
    try:
        rules, extras = fa.read_registry_rules(fa.resolve_indirect)
    except OSError:
        pytest.skip("firewall policy unreadable here")
    groups = fa.group_summary(rules, extras)
    assert len(groups) > 50  # most rules on a real machine carry a group
    mixed = [g for g in groups if g.mixed]
    assert 0 < len(mixed) < 10
    assert any(g.name == "Network Discovery" for g in mixed)
    # Sorted mixed-first: every mixed group precedes every non-mixed one.
    first_non_mixed = next(i for i, g in enumerate(groups) if not g.mixed)
    assert all(g.mixed for g in groups[:first_non_mixed])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_real_registry_scoped_open_public_is_rare_and_third_party():
    """Measured on this real machine (529 rules): 30 program/service-scoped
    inbound-allow rules leave RemoteAddress at Any on a specific port -- but 27
    of those are Windows-supplied (dosvc, dhcp, rpcss, mdeserver, the
    ms-resource system apps) or a standard mDNS/SSDP discovery port, which
    `_is_scoped_open_public` excludes by design (see its docstring). What is
    left is a small, real, third-party handful -- "Microsoft Office Outlook"
    UDP 6004 on the Public profile among them -- never the bulk of the 30."""
    try:
        rules, extras = fa.read_registry_rules(fa.resolve_indirect)
    except OSError:
        pytest.skip("firewall policy unreadable here")
    hits = [(r, e) for r, e in zip(rules, extras) if fa.chip_matches("scoped_open_public", r, e)]
    assert 0 < len(hits) < 10
    for r, e in hits:
        assert not fa.is_builtin(r, e)
        assert "Public" in fa._profiles(r)
