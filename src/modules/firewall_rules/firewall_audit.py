"""Qt-free firewall exposure audit: registry reader, chips, findings, CSV.

The registry (`...\\SharedAccess\\Parameters\\FirewallPolicy\\FirewallRules`)
is readable WITHOUT elevation, unlike `netsh ... verbose` in some setups, and
holds the same rules the netsh listing shows. Everything here works on
`FirewallRule` records so netsh output and registry output audit identically.

Rules of this file:
* A value we could not read is `None` / an `unread` reason, never "off".
* Findings describe exposure; they never claim a rule is malicious.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from core.windows_utils import system_root

logger = logging.getLogger(__name__)

_POLICY = r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy"
_PROTOCOLS = {"6": "TCP", "17": "UDP", "1": "ICMPv4", "58": "ICMPv6",
              "2": "IGMP", "41": "IPv6", "47": "GRE", "50": "ESP", "51": "AH"}
ALL_PROFILES = "Domain,Private,Public"

SEV_HIGH, SEV_MEDIUM, SEV_LOW, SEV_INFO = "high", "medium", "low", "info"
_SEV_ORDER = {SEV_HIGH: 0, SEV_MEDIUM: 1, SEV_LOW: 2, SEV_INFO: 3}

# A port range wider than this on an inbound allow is worth a look.
BROAD_PORT_SPAN = 1000

# Standard multicast/broadcast local-discovery ports: every browser and several
# Windows components carry a rule for one of these with RemoteAddress=Any by
# design (mDNS, SSDP, WS-Discovery, LLMNR all work by soliciting a reply from
# whatever is on the local segment) -- flagging them as "overly broad" would be
# noise, not a finding. Measured on this real machine: Edge/Chrome/Brave each
# carry an identical "(mDNS-In)" rule on UDP 5353, RemoteAddress=Any, enabled
# on Public -- none of them are the kind of exposure this check exists to find.
_DISCOVERY_PORTS = {"5353", "1900", "3702", "5355"}


@dataclass
class RuleExtra:
    """What the registry knows that FirewallRule (netsh columns) does not."""
    package: str = ""
    service: str = ""
    remote_ip: str = ""
    local_ip: str = ""
    grouping: str = ""
    icmp: str = ""                   # ICMP type/code filter; part of a rule's identity


@dataclass
class Finding:
    severity: str
    key: str
    title: str
    detail: str
    rule_names: List[str] = field(default_factory=list)
    fix: str = ""


@dataclass
class ProfileState:
    name: str
    enabled: Optional[bool]          # None = could not read
    default_inbound: str             # "Block" / "Allow" / "" when unread
    default_outbound: str
    note: str = ""


@dataclass
class GroupInfo:
    """One netsh/PowerShell rule "group" -- the same unit
    `netsh advfirewall firewall set rule group=<name> new enable=yes|no` and
    `Set-NetFirewallRule -Group <name>` address in one call.

    `mixed` is the reason this exists: a group with some rules enabled and
    some disabled is neither "on" nor "off" as a whole, and is the one shape
    where a bulk toggle changes real behaviour rather than being a no-op.
    """
    name: str
    total: int
    enabled: int
    disabled: int
    builtin: bool

    @property
    def mixed(self) -> bool:
        return 0 < self.enabled < self.total


# ---------------------------------------------------------------- registry

def parse_registry_rule(value: str, resolve: Optional[Callable[[str], str]] = None):
    """One FirewallRules registry string -> (FirewallRule, RuleExtra) or None.

    Repeated keys (`Profile=Domain|Profile=Private`, several `LPort`) are
    joined in order; an absent Profile means every profile.
    """
    from modules.firewall_rules.firewall_manager_module import FirewallRule
    if not isinstance(value, str) or "|" not in value:
        return None
    multi: Dict[str, List[str]] = {}
    for part in value.split("|")[1:]:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        multi.setdefault(k, []).append(v)

    def one(key: str) -> str:
        return ",".join(multi.get(key, []))

    raw_name = one("Name")
    name = raw_name
    if resolve and raw_name.startswith("@"):
        try:
            name = resolve(_full_resource_path(raw_name)) or raw_name
        except Exception:
            logger.debug("name resolve failed for %r", raw_name, exc_info=True)
    proto = one("Protocol")
    lports = ",".join(multi.get("LPort", []) + multi.get("LPort2_10", []))
    rports = ",".join(multi.get("RPort", []) + multi.get("RPort2_10", []))
    ra = _uniq(multi.get("RA4", []) + multi.get("RA6", []))
    la = _uniq(multi.get("LA4", []) + multi.get("LA6", []))
    rule = FirewallRule(
        name=name,
        enabled="Yes" if one("Active").upper() == "TRUE" else "No",
        direction="In" if one("Dir") == "In" else "Out",
        action="Allow" if one("Action") == "Allow" else "Block",
        protocol=_PROTOCOLS.get(proto, proto) if proto else "Any",
        local_port=lports or "Any",
        remote_port=rports or "Any",
        program=one("App"),
        profile=one("Profile") or ALL_PROFILES,
    )
    extra = RuleExtra(package=one("PFN"), service=one("Svc"),
                      remote_ip=ra or "Any", local_ip=la or "Any",
                      icmp=_uniq(multi.get("ICMP4", []) + multi.get("ICMP6", [])),
                      grouping=one("EmbedCtxt"))
    return rule, extra


def _full_resource_path(name: str) -> str:
    """`@FirewallAPI.dll,-1` -> `@%SystemRoot%\\system32\\FirewallAPI.dll,-1`.

    SHLoadIndirectString resolved 0 of these until the DLL had a full path.
    """
    if re.match(r"^@[A-Za-z0-9_.]+\.dll,", name):
        return "@%SystemRoot%\\system32\\" + name[1:]
    return name


def resolve_indirect(name: str) -> str:
    """Resolve any `@dll,-id` / `@{pkg?ms-resource:...}` name; "" if MRT cannot."""
    from modules.firewall_rules.firewall_manager_module import _mrt_lookup
    return _mrt_lookup(name)


def resolve_group_name(raw: str, resolve: Optional[Callable[[str], str]] = None) -> str:
    """The registry's `EmbedCtxt` value -> the group name netsh/PowerShell show.

    A Windows-supplied group is stored as a bare resource string
    (`@FirewallAPI.dll,-32752`), exactly like the Name field, and needs the
    same `%SystemRoot%`-qualifying step before `SHLoadIndirectString` can
    resolve it -- confirmed live 2026-09-30: `-32752` resolves to "Network
    Discovery", matching netsh's own "Grouping:" column for the same rules.
    A third-party group (WhatsApp, Google Chrome, ...) is a plain literal
    string with no `@` prefix and passes through unchanged. A resolve
    failure falls back to the raw string rather than hiding the group.
    """
    raw = (raw or "").strip()
    if not raw or not raw.startswith("@"):
        return raw
    resolve = resolve or resolve_indirect
    try:
        resolved = resolve(_full_resource_path(raw))
    except Exception:
        logger.debug("group name resolve failed for %r", raw, exc_info=True)
        return raw
    return resolved or raw


def _uniq(items: List[str]) -> str:
    seen: List[str] = []
    for x in items:
        if x not in seen:
            seen.append(x)
    return ",".join(seen)


def read_registry_rules(resolve: Optional[Callable[[str], str]] = None):
    """All rules from the registry: (rules, extras-by-index) or raises OSError."""
    import winreg
    rules, extras = [], []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _POLICY + r"\FirewallRules") as k:
        n = winreg.QueryInfoKey(k)[1]
        for i in range(n):
            _, val, _ = winreg.EnumValue(k, i)
            parsed = parse_registry_rule(val, resolve)
            if parsed:
                rules.append(parsed[0])
                extras.append(parsed[1])
    return rules, extras


def read_profile_states() -> List[ProfileState]:
    """Per-profile enable + default actions. An absent default is Windows'
    own default (inbound Block, outbound Allow), and says so in `note`."""
    import winreg
    out: List[ProfileState] = []
    for label, sub in (("Domain", "DomainProfile"), ("Private", "StandardProfile"),
                       ("Public", "PublicProfile")):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _POLICY + "\\" + sub) as k:
                out.append(_profile_from(label, lambda n, k=k: _qv(winreg, k, n)))
        except OSError as exc:
            logger.warning("Cannot read firewall profile %s: %s", label, exc)
            out.append(ProfileState(label, None, "", "", "could not read: %s" % exc))
    return out


def _qv(winreg, key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def _profile_from(label: str, get: Callable[[str], Optional[int]]) -> ProfileState:
    en = get("EnableFirewall")
    din, dout = get("DefaultInboundAction"), get("DefaultOutboundAction")
    note = ""
    if din is None or dout is None:
        note = "default actions not set in registry: Windows defaults apply"
    return ProfileState(
        label,
        None if en is None else bool(en),
        "Allow" if din == 0 else "Block",
        "Block" if dout == 1 else "Allow",
        note,
    )


# ------------------------------------------------------------------ helpers

def _profiles(rule) -> set:
    return {p.strip() for p in (rule.profile or "").split(",") if p.strip()} or \
        {"Domain", "Private", "Public"}


def expand_program(path: str) -> str:
    return os.path.expandvars(path.strip().strip('"')) if path else ""


def is_any(value: str) -> bool:
    return (value or "").strip().lower() in ("", "any", "*")


def port_span(spec: str) -> int:
    """Widest numeric range in a port spec ("1-65535", "80,443"): span size.
    Non-numeric tokens (RPC, IPHTTPSIn) count as 1; Any counts as 65535."""
    if is_any(spec):
        return 65535
    widest = 1
    for tok in spec.split(","):
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", tok)
        if m:
            widest = max(widest, int(m.group(2)) - int(m.group(1)) + 1)
    return widest


def is_builtin(rule, extra: Optional[RuleExtra] = None) -> bool:
    """Windows-supplied: named by resource string, or program under %SystemRoot%."""
    if rule.name.startswith("@") or (extra and extra.grouping.startswith("@")):
        return True
    prog = expand_program(rule.program).lower()
    root = system_root().lower()
    return bool(prog) and prog.startswith(root + "\\")


def group_summary(rules: Sequence, extras: Sequence[RuleExtra],
                  resolve: Optional[Callable[[str], str]] = None) -> List[GroupInfo]:
    """One `GroupInfo` per named rule group, sorted mixed-first then by name.

    Only rules that actually carry a group (most third-party and many
    Windows rules do not) contribute; a rule with no `EmbedCtxt` is not part
    of any group and never appears here. Cached per raw string within one
    call so 511 registry rules do not each pay for their own MRT lookup.
    """
    cache: Dict[str, str] = {}

    def name_for(raw: str) -> str:
        if raw not in cache:
            cache[raw] = resolve_group_name(raw, resolve)
        return cache[raw]

    counts: Dict[str, Dict[str, int]] = {}
    builtin: Dict[str, bool] = {}
    for r, e in zip(rules, extras or []):
        raw = (e.grouping if e else "") or ""
        if not raw.strip():
            continue
        name = name_for(raw)
        if not name:
            continue
        c = counts.setdefault(name, {"total": 0, "enabled": 0})
        c["total"] += 1
        if r.enabled == "Yes":
            c["enabled"] += 1
        builtin.setdefault(name, raw.startswith("@"))

    out = [GroupInfo(name, c["total"], c["enabled"], c["total"] - c["enabled"], builtin[name])
           for name, c in counts.items()]
    out.sort(key=lambda g: (not g.mixed, g.name.lower()))
    return out


def is_scoped(rule, extra: Optional[RuleExtra]) -> bool:
    """Tied to a program, service or app package, so 'any port' is not 'any process'."""
    if not is_any(rule.program) and rule.program.strip().lower() != "system":
        return True
    return bool(extra and (extra.package or extra.service))


# --------------------------------------------------------------------- chips

CHIPS: List[Tuple[str, str]] = [
    ("all", "All"),
    ("in_allow", "Inbound allow"),
    ("out_block", "Outbound block"),
    ("any_any", "Allow any-port inbound"),
    ("enabled", "Enabled"),
    ("disabled", "Disabled"),
    ("third_party", "Non-Microsoft"),
    ("prog_missing", "Program missing"),
    ("public", "Public profile"),
    ("scoped_open_public", "Scoped, open remote, Public"),
]


def program_missing(rule, exists: Callable[[str], bool] = os.path.exists) -> bool:
    p = expand_program(rule.program)
    if not p or p.lower() == "system" or "%" in p:
        return False
    return not exists(p)


def chip_matches(chip: str, rule, extra: Optional[RuleExtra] = None,
                 exists: Callable[[str], bool] = os.path.exists) -> bool:
    if chip == "all":
        return True
    if chip == "in_allow":
        return rule.direction == "In" and rule.action == "Allow"
    if chip == "out_block":
        return rule.direction == "Out" and rule.action == "Block"
    if chip == "any_any":
        return _is_open_inbound(rule, extra)
    if chip == "enabled":
        return rule.enabled == "Yes"
    if chip == "disabled":
        return rule.enabled != "Yes"
    if chip == "third_party":
        return not is_builtin(rule, extra)
    if chip == "prog_missing":
        return program_missing(rule, exists)
    if chip == "public":
        return "Public" in _profiles(rule)
    if chip == "scoped_open_public":
        return _is_scoped_open_public(rule, extra)
    return False


def chip_counts(rules: Sequence, extras: Optional[Sequence[RuleExtra]] = None,
                exists: Callable[[str], bool] = os.path.exists) -> Dict[str, int]:
    counts = {k: 0 for k, _ in CHIPS}
    cache: Dict[str, bool] = {}

    def ex(path: str) -> bool:
        if path not in cache:
            cache[path] = exists(path)
        return cache[path]

    for i, r in enumerate(rules):
        e = extras[i] if extras else None
        for k, _ in CHIPS:
            if chip_matches(k, r, e, ex):
                counts[k] += 1
    return counts


# ------------------------------------------------------------------ findings

def _is_open_inbound(rule, extra) -> bool:
    return (rule.enabled == "Yes" and rule.direction == "In"
            and rule.action == "Allow" and not is_scoped(rule, extra)
            and rule.protocol in ("TCP", "UDP", "Any")
            and is_any(rule.local_port)
            and is_any(extra.remote_ip if extra else "Any"))


def _is_scoped_open_public(rule, extra) -> bool:
    """A program/service-bound inbound allow, on the Public profile, that still
    accepts from ANY remote address on a specific, non-discovery port.

    `_is_open_inbound` above only counts a rule with no program/service/port
    limit at all (`is_scoped` excludes these), and `broad_ports` only counts a
    wide RANGE of ports -- so a rule tied to one real program, on one specific
    port, with RemoteAddress left at its default of Any, is invisible to both.
    Measured on this real machine: 526 rules include "Microsoft Office Outlook"
    inbound-allow UDP 6004, RemoteAddress=Any, enabled on the Public profile --
    scoped to a named program (so it never appears as "open"), but on an
    untrusted network (airport/cafe Wi-Fi) any device on that network can still
    reach it on that port. Windows-supplied rules are excluded: Microsoft's own
    rules for dosvc/dhcp/rpcss/etc. on Public are assumed already vetted, and
    flagging every one of them (measured: 27 rules match the raw shape before
    this exclusion) would bury the one third-party rule actually worth a look.
    """
    if not (rule.enabled == "Yes" and rule.direction == "In" and rule.action == "Allow"):
        return False
    if "Public" not in _profiles(rule):
        return False
    if is_builtin(rule, extra) or not is_scoped(rule, extra):
        return False
    if rule.protocol not in ("TCP", "UDP") or is_any(rule.local_port):
        return False
    ports = {p.strip() for p in rule.local_port.split(",")}
    if ports <= _DISCOVERY_PORTS:
        return False
    return is_any(extra.remote_ip if extra else "Any")


def _dup_key(r, e) -> tuple:
    return (r.direction, r.action, r.protocol, r.local_port, r.remote_port,
            expand_program(r.program).lower(), tuple(sorted(_profiles(r))),
            e.remote_ip if e else "", e.service if e else "",
            e.icmp if e else "", e.package if e else "")


def audit(rules: Sequence, extras: Optional[Sequence[RuleExtra]] = None,
          profiles: Optional[Sequence[ProfileState]] = None,
          exists: Callable[[str], bool] = os.path.exists) -> List[Finding]:
    """Computed findings, most severe first. Enabled rules only unless stated."""
    out: List[Finding] = []
    ext = list(extras) if extras else [None] * len(rules)

    for p in profiles or []:
        if p.enabled is None:
            out.append(Finding(SEV_INFO, "profile_unread", "%s profile state unreadable" % p.name,
                               p.note))
        elif not p.enabled:
            out.append(Finding(SEV_HIGH, "profile_off", "Firewall is OFF on the %s profile" % p.name,
                               "No rules are enforced while this profile is active.",
                               fix="Turn it on: Set-NetFirewallProfile -Profile %s -Enabled True" % p.name))
        if p.default_inbound == "Allow":
            out.append(Finding(SEV_HIGH, "default_allow", "%s profile allows inbound by default" % p.name,
                               "Unsolicited inbound traffic is permitted unless a rule blocks it."))

    open_in = [(r, e) for r, e in zip(rules, ext) if _is_open_inbound(r, e)]
    for prof_sev, pname in ((SEV_HIGH, "Public"), (SEV_MEDIUM, "Private"), (SEV_LOW, "Domain")):
        hit = [r for r, _ in open_in if pname in _profiles(r)]
        if hit:
            out.append(Finding(
                prof_sev, "open_" + pname.lower(),
                "%d inbound allow rule(s) with no program, service or port limit on %s" % (len(hit), pname),
                "Any process may accept connections on any port from any address.",
                [r.name for r in hit], "Disable or scope the rule (program/port/remote address)."))

    scoped_open = [r for r, e in zip(rules, ext) if _is_scoped_open_public(r, e)]
    if scoped_open:
        out.append(Finding(
            SEV_MEDIUM, "scoped_open_public",
            "%d non-Microsoft inbound allow rule(s) accept any remote address on the Public profile"
            % len(scoped_open),
            "Each is tied to one program or service, so it is not \"any process, any port\" and does "
            "not appear above -- but Remote address is still unrestricted on the Public profile, the "
            "one meant for untrusted networks like a cafe or airport. Worth checking whether the "
            "program genuinely needs to accept connections from an arbitrary host there.",
            [r.name for r in scoped_open],
            "Restrict Remote address to what actually needs it, or drop Public from the rule's profile."))

    broad = [r for r, e in zip(rules, ext)
             if r.enabled == "Yes" and r.direction == "In" and r.action == "Allow"
             and not is_any(r.local_port) and port_span(r.local_port) >= BROAD_PORT_SPAN]
    if broad:
        out.append(Finding(SEV_MEDIUM, "broad_ports",
                           "%d inbound allow rule(s) open a range of %d+ ports" % (len(broad), BROAD_PORT_SPAN),
                           "Wide ranges expose far more than the one service that needed them.",
                           [r.name for r in broad]))

    missing = [r for r in rules if r.enabled == "Yes" and program_missing(r, exists)]
    if missing:
        allow = [r for r in missing if r.action == "Allow"]
        out.append(Finding(SEV_LOW, "prog_missing",
                           "%d enabled rule(s) point at a program that no longer exists" % len(missing),
                           "%d of them allow traffic. Left-over rules from uninstalled software; "
                           "anything later installed at that path inherits the rule." % len(allow),
                           [r.name for r in missing], "Delete the stale rule."))

    groups: Dict[tuple, List[str]] = {}
    for r, e in zip(rules, ext):
        if r.enabled == "Yes":
            groups.setdefault(_dup_key(r, e), []).append(r.name)
    dups = {k: v for k, v in groups.items() if len(v) > 1 and k[5]}
    if dups:
        names = [n for v in dups.values() for n in v]
        out.append(Finding(SEV_LOW, "duplicates",
                           "%d group(s) of enabled rules that say the same thing" % len(dups),
                           "Same direction, action, protocol, ports, program and profile.", names))

    both = _allow_and_block_same_program(rules)
    if both:
        out.append(Finding(SEV_INFO, "allow_vs_block",
                           "%d program(s) have both an allow and a block rule (same direction)" % len(both),
                           "Block rules win over allow rules in Windows Firewall.", both))
    out.sort(key=lambda f: _SEV_ORDER[f.severity])
    return out


def _allow_and_block_same_program(rules: Sequence) -> List[str]:
    seen: Dict[tuple, set] = {}
    for r in rules:
        p = expand_program(r.program).lower()
        if r.enabled == "Yes" and p and p != "system":
            seen.setdefault((p, r.direction), set()).add(r.action)
    return sorted(p for (p, _), acts in seen.items() if len(acts) > 1)


# ----------------------------------------------------------------------- csv

CSV_COLS = ["Name", "Enabled", "Direction", "Action", "Protocol", "Local Port",
            "Remote Port", "Program", "Profile", "Remote Address", "Service", "Package"]


def rules_to_csv(rules: Sequence, extras: Optional[Sequence[RuleExtra]] = None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLS)
    for i, r in enumerate(rules):
        e = extras[i] if extras else RuleExtra()
        w.writerow([r.name, r.enabled, r.direction, r.action, r.protocol, r.local_port,
                    r.remote_port, r.program, r.profile, e.remote_ip, e.service, e.package])
    return buf.getvalue()


def rule_key(rule) -> tuple:
    return (rule.name.lower(), rule.direction, expand_program(rule.program).lower())


def extras_index(rules: Sequence, extras: Sequence[RuleExtra]) -> Dict[tuple, RuleExtra]:
    """Registry extras keyed so a netsh-listed rule can find its own."""
    return {rule_key(r): e for r, e in zip(rules, extras)}


@dataclass
class Snapshot:
    """One registry read: rules + extras + profiles, or the reason it failed."""
    rules: List = field(default_factory=list)
    extras: List[RuleExtra] = field(default_factory=list)
    profiles: List[ProfileState] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    error: str = ""


def take_snapshot() -> Snapshot:
    """Read and audit. A refused/failed read fills `error`; it never yields
    an empty (and therefore reassuring) findings list."""
    try:
        rules, extras = read_registry_rules(resolve_indirect)
        profiles = read_profile_states()
    except OSError as exc:
        logger.warning("Firewall registry read failed: %s", exc)
        return Snapshot(error="Could not read the firewall policy: %s" % exc)
    return Snapshot(rules, extras, profiles, audit(rules, extras, profiles))


def rule_detail(rule, extra: Optional[RuleExtra] = None) -> str:
    lines = [rule.name,
             "%s  %s  %s  (%s)" % (rule.direction, rule.action, rule.protocol,
                                   "enabled" if rule.enabled == "Yes" else "disabled"),
             "Program:  %s" % (rule.program or "(any)"),
             "Local port:  %s    Remote port:  %s" % (rule.local_port or "Any", rule.remote_port or "Any"),
             "Profiles:  %s" % rule.profile]
    if extra:
        if extra.remote_ip:
            lines.append("Remote address:  %s" % extra.remote_ip)
        if extra.service:
            lines.append("Service:  %s" % extra.service)
        if extra.package:
            lines.append("App package:  %s" % extra.package)
    lines.append("Origin:  %s" % ("Windows-supplied" if is_builtin(rule, extra) else "added by software or a person"))
    return "\n".join(lines)
