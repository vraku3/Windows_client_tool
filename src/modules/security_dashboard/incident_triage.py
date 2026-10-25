"""Incident triage: "did anyone get in, and what changed?" -- from the logs
Windows already keeps. Qt-free.

What an admin reads first on a machine they suspect, grouped into findings
with a count, first/last time and the evidence, never a wall of raw events.
Measured on this machine (2026-10-15, elevated, 30 days):

* System 7045 (service installed): 140 events, 65 distinct services -- 76
  kernel drivers, 64 services; one Corsair driver reinstalled 25 times. One
  KERNEL DRIVER was loaded from a user-writable folder: HWiNFO's
  `AppData\\Local\\Temp\\HWiNFO_x64_215.sys` (benign: HWiNFO extracts its
  driver there -- and exactly the shape a malicious driver load takes).
* Defender Operational 5007 (config changed): 593 events, of which only the
  14 under `Exclusions\\` matter (Battle.net/WoW/CurseForge added on
  2026-09-18). The rest is Defender's own housekeeping (component progress,
  config hashes, service state). 5007 names no actor.
* PowerShell Operational 4104 at Level 3 ("Warning") is PowerShell's own
  suspicious-keyword heuristic: 84 flagged blocks, 75 of them PowerShell's
  auto-generated CDXML (`Cmdletization`) module wrappers -- benign, counted
  apart -- and the rest this session's own tooling (Base64 command plumbing).
* Security 4738 (account changed): 19, all by SYSTEM (Microsoft-account
  sync) -- noise, so only changes made by a PERSON are reported.
* Security 4719 (audit policy changed): the six writes of the elevated
  Audit & Logging round trip, by this user -- reported with who and which
  subcategory, because a policy turned OFF by someone else is the classic
  anti-forensics step.
* Security 4625/4740/1102, 4624 type 10: none in 30 days.
* Task creation is NOT recorded here: TaskScheduler/Operational is disabled
  (Windows' default) and 4698 needs "Other Object Access Events" auditing,
  off by default. That is said as a coverage finding, never as "no tasks".

A source that cannot be read (Security unelevated: access denied) is an
UNKNOWN finding with the reason -- never a clean result.
"""
from __future__ import annotations
from core.system_paths import powershell_exe

import logging
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import audit_policy, log_readiness

logger = logging.getLogger(__name__)

HIGH, MEDIUM, INFO, UNKNOWN = "high", "medium", "info", "unknown"
SEVERITY_RANK = {HIGH: 0, MEDIUM: 1, UNKNOWN: 2, INFO: 3}

#: Newest-first cap per query. A source that hits it says "at least".
MAX_EVENTS = 3000
_TIMEOUT_S = 30
_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"

SECURITY = "Security"
SYSTEM = "System"
DEFENDER = "Microsoft-Windows-Windows Defender/Operational"
POWERSHELL = "Microsoft-Windows-PowerShell/Operational"
TASKS = "Microsoft-Windows-TaskScheduler/Operational"

Run = Callable[[List[str]], Tuple[int, str, str]]


@dataclass
class Event:
    id: int
    time: datetime                          # UTC
    data: Dict[str, str]
    level: int = 4


@dataclass
class Query:
    """One channel read: its events, or why there are none."""
    channel: str
    events: List[Event] = field(default_factory=list)
    refused: Optional[str] = None           # could not read -> UNKNOWN, never "none"
    absent: bool = False                    # the channel does not exist on this PC
    capped: bool = False                    # hit MAX_EVENTS: counts are "at least"
    unparsed: int = 0


@dataclass
class Finding:
    key: str
    severity: str
    title: str
    detail: str
    count: int = 0
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    evidence: List[str] = field(default_factory=list)
    source: str = ""
    fix: str = ""


@dataclass
class Triage:
    days: int
    findings: List[Finding] = field(default_factory=list)
    sources: List[Query] = field(default_factory=list)


# ---- reading ---------------------------------------------------------------------------------

def _default_run(args: List[str]) -> Tuple[int, str, str]:
    # Piped wevtutil output is the ANSI code page, not UTF-8 (measured, CLAUDE.md "Diagnose").
    p = subprocess.run(args, capture_output=True, text=True, encoding="mbcs", errors="replace",
                       timeout=_TIMEOUT_S, creationflags=subprocess.CREATE_NO_WINDOW)
    return p.returncode, p.stdout or "", p.stderr or ""


def xpath(ids: Iterable[int], days: int, extra: str = "") -> str:
    ms = int(days) * 86_400_000
    id_part = " or ".join(f"EventID={i}" for i in ids)
    return f"*[System[({id_part}) and TimeCreated[timediff(@SystemTime) <= {ms}]{extra}]]"


def _time(raw: str) -> Optional[datetime]:
    return log_readiness.parse_oldest(f"<Event SystemTime='{raw}'/>")


def parse_events(text: str) -> Tuple[List[Event], int]:
    """`wevtutil qe /f:xml` output -> events. Each <Event> is parsed on its
    own, so one malformed record costs that record, not the read."""
    events, bad = [], 0
    for chunk in re.findall(r"<Event[ >].*?</Event>", text, re.S):
        try:
            root = ET.fromstring(chunk)
        except ET.ParseError:
            bad += 1
            continue
        sys_el = root.find(_NS + "System")
        when = sys_el.find(_NS + "TimeCreated") if sys_el is not None else None
        t = _time(when.get("SystemTime", "")) if when is not None else None
        try:
            eid = int((sys_el.findtext(_NS + "EventID") or "").strip())
            level = int((sys_el.findtext(_NS + "Level") or "4").strip() or 4)
        except (ValueError, AttributeError):
            bad += 1
            continue
        if t is None:
            bad += 1
            continue
        data = {d.get("Name", f"_{i}"): (d.text or "")
                for i, d in enumerate(root.iter(_NS + "Data"))}
        events.append(Event(eid, t, data, level))
    return events, bad


def read(channel: str, query: str, run: Optional[Run] = None) -> Query:
    """Newest-first events matching `query`; a refusal is a reason, not []."""
    q = Query(channel)
    rc, text, refusal = log_readiness._ask(
        run or _default_run, ["wevtutil", "qe", channel, f"/q:{query}", "/f:xml",
                              f"/c:{MAX_EVENTS}", "/rd:true"])
    if refusal:
        q.refused = refusal
        return q
    if "<Event" not in text:
        if log_readiness._is_not_found(rc or 0, text):
            q.absent = True
        elif log_readiness._is_access_denied(rc or 0, text):
            q.refused = log_readiness._NEEDS_ADMIN
        elif rc:
            q.refused = f"wevtutil failed ({rc}): {text.strip()[:120]}"
        return q
    q.events, q.unparsed = parse_events(text)
    q.capped = len(q.events) + q.unparsed >= MAX_EVENTS
    return q


# ---- helpers ---------------------------------------------------------------------------------

def _span(events: List[Event]) -> Tuple[Optional[datetime], Optional[datetime]]:
    if not events:
        return None, None
    times = [e.time for e in events]
    return min(times), max(times)


def _local(t: Optional[datetime]) -> str:
    return t.astimezone().strftime("%Y-%m-%d %H:%M") if t else "?"


def _finding(key, severity, title, detail, events, evidence, source, fix="") -> Finding:
    first, last = _span(events)
    return Finding(key, severity, title, detail, len(events), first, last, evidence, source, fix)


_WRITABLE = ("\\users\\", "\\appdata\\", "\\temp\\", "\\tmp\\", "\\downloads\\",
             "\\programdata\\", "\\windows\\tasks\\", "\\recycle")
_TRUSTED_PROGRAMDATA = ("\\programdata\\microsoft\\windows defender\\",)
_SCRIPT_HOSTS = re.compile(r"\b(cmd(\.exe)?\s+/c|powershell|pwsh|mshta|wscript|cscript|rundll32|"
                           r"regsvr32|bitsadmin|certutil)\b|-enc(odedcommand)?\b", re.I)


def normalise_image_path(raw: str) -> str:
    """A service ImagePath as a lowercase Win32 path: quotes, `\\??\\` and
    `\\SystemRoot\\` resolved, environment variables expanded."""
    p = (raw or "").strip()
    if p.startswith('"'):
        p = p[1:].split('"', 1)[0]
    p = p.replace("\\??\\", "")
    windir = os.environ.get("SystemRoot") or os.environ.get("windir") or ""
    if p.lower().startswith("\\systemroot\\") and windir:
        p = windir + p[len("\\SystemRoot"):]
    elif p.lower().startswith("system32\\") and windir:
        p = os.path.join(windir, p)              # relative driver paths resolve against %SystemRoot%
    return os.path.expandvars(p).lower()


def user_writable(path: str) -> bool:
    low = "\\" + path.lower().lstrip("\\")
    if any(t in low for t in _TRUSTED_PROGRAMDATA):
        return False
    return any(t in low for t in _WRITABLE)


# ---- checks ----------------------------------------------------------------------------------

def check_services(q: Query) -> List[Finding]:
    """System 7045: new services and drivers, worst first."""
    if not q.events:
        return []
    out, odd_events, odd_lines = [], [], []
    groups: "OrderedDict[str, List[Event]]" = OrderedDict()
    for e in q.events:
        groups.setdefault(e.data.get("ServiceName", "?"), []).append(e)
    for name, evs in groups.items():
        d = evs[0].data
        raw = d.get("ImagePath", "")
        path = normalise_image_path(raw)
        kind = d.get("ServiceType", "")
        if user_writable(path) or _SCRIPT_HOSTS.search(raw):
            odd_events += evs
            why = ("from a user-writable folder" if user_writable(path)
                   else "through a script host / living-off-the-land binary")
            odd_lines.append(f"{name} ({kind or 'service'}, {d.get('AccountName') or 'kernel'}) "
                             f"installed {why}: {raw}  x{len(evs)}, last {_local(evs[0].time)}")
    if odd_events:
        out.append(_finding(
            "services.suspicious", HIGH, "Service or driver installed from a risky location",
            "A service or kernel driver whose binary sits where an ordinary user can write "
            "(Temp, AppData, Users, ProgramData), or that starts through cmd/PowerShell/"
            "rundll32, is how malware gets SYSTEM or kernel access. Some tools do it "
            "legitimately (HWiNFO extracts its driver to Temp) -- confirm each one.",
            odd_events, odd_lines, SYSTEM,
            "Check the file and its signature in Startup & Boot > Persistence; remove the "
            "service in System Management > Services if you do not recognise it."))
    lines = [f"{name}  x{len(evs)}  ({evs[0].data.get('ServiceType', '')}) -- "
             f"{evs[0].data.get('ImagePath', '')}" for name, evs in groups.items()]
    drivers = sum(1 for evs in groups.values() if "kernel" in evs[0].data.get("ServiceType", ""))
    out.append(_finding(
        "services.installed", INFO,
        f"{len(groups)} services/drivers installed ({drivers} kernel drivers)",
        "Every service installation Windows recorded (System 7045). Driver and software "
        "updates reinstall the same service many times; a name you do not recognise is the "
        "one to look at.", q.events, lines, SYSTEM))
    return out


def _defender_value(d: Dict[str, str]) -> Tuple[str, str]:
    return d.get("Old Value", "") or "", d.get("New Value", "") or ""


def av_products_on(text: str) -> Optional[List[str]]:
    """Names of antivirus products Security Center reports ON, from
    `AntiVirusProduct` as "name|productState" lines; None when unreadable.

    The middle byte of productState is the scanner state: 0x10/0x11 on,
    0x00/0x01 off (measured here: Bitdefender 0x41000 on, Defender 0x60100
    passive since Bitdefender was installed)."""
    names = []
    rows = [ln for ln in (text or "").splitlines() if "|" in ln]
    if not rows:
        return None
    for line in rows:
        name, _, state = line.rpartition("|")
        try:
            on = ((int(state.strip()) >> 8) & 0xFF) in (0x10, 0x11)
        except ValueError:
            logger.debug("incident_triage: unreadable productState %r for %r", state, name)
            continue
        if on:
            names.append(name.strip())
    return names


_MS_DEFENDER_NAMES = ("windows defender", "microsoft defender antivirus", "microsoft defender")


def _read_av(run: Optional[Run]) -> Optional[List[str]]:
    cmd = ("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | "
           "ForEach-Object { $_.displayName + '|' + $_.productState }")
    rc, text, refusal = log_readiness._ask(
        run or _default_run, [powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", cmd])
    if refusal or rc:
        logger.info("incident_triage: Security Center not readable: %s", refusal or text[:120])
        return None
    return av_products_on(text)


def check_defender(q: Query, av_on: Optional[List[str]] = None) -> List[Finding]:
    """Defender detections, real-time protection off, exclusions changed."""
    out = []
    detections = [e for e in q.events if e.id in (1116, 1117)]
    if detections:
        lines = [f"{_local(e.time)}  {e.data.get('Threat Name', '?')}  -- "
                 f"{e.data.get('Path', '')}  [{e.data.get('Action Name', '')}]" for e in detections]
        out.append(_finding("defender.detections", HIGH,
                            f"Microsoft Defender detected malware ({len(detections)} events)",
                            "Defender found and acted on (1117) or only detected (1116) a threat.",
                            detections, lines, DEFENDER,
                            "Open Windows Security > Protection history for the full record."))
    rtp_off = [e for e in q.events if e.id == 5001]
    # By exact name: "Bitdefender" contains "defender" too.
    others = [n for n in (av_on or []) if n.strip().lower() not in _MS_DEFENDER_NAMES]
    if rtp_off and others:
        out.append(_finding("defender.rtp_off", INFO,
                            f"Defender stepped aside for {', '.join(others)}",
                            "Defender's real-time protection turned off, and Security Center "
                            f"reports {', '.join(others)} protecting this PC now -- Windows does "
                            "this on purpose when another antivirus registers.",
                            rtp_off, [_local(e.time) for e in rtp_off], DEFENDER))
    elif rtp_off:
        out.append(_finding("defender.rtp_off", HIGH, "Defender real-time protection was turned off",
                            "No other antivirus reports itself active"
                            + (" (Security Center could not be read)" if av_on is None else "")
                            + ". Turning real-time protection off is the first thing an attacker "
                            "does; check that it is back on.",
                            rtp_off, [_local(e.time) for e in rtp_off], DEFENDER,
                            "Security Dashboard > Defender shows its current state."))
    added, removed = [], []
    for e in q.events:
        old, new = _defender_value(e.data)
        if e.id == 5007 and "\\Exclusions\\" in new and not old.strip():
            added.append(e)
        elif e.id == 5007 and "\\Exclusions\\" in old and not new.strip():
            removed.append(e)
    if added:
        lines = [f"{_local(e.time)}  + {_exclusion(_defender_value(e.data)[1])}" for e in added]
        out.append(_finding("defender.exclusions_added", MEDIUM,
                            f"{len(added)} Defender exclusions added",
                            "An excluded path, process or extension is never scanned. Games and "
                            "launchers add their own; malware adds itself. Defender does not "
                            "record who added an exclusion.", added, lines, DEFENDER,
                            "Review the current list in Security Dashboard > Defender "
                            "(Defender exclusions)."))
    if removed:
        out.append(_finding("defender.exclusions_removed", INFO,
                            f"{len(removed)} Defender exclusions removed", "Exclusions taken away.",
                            removed, [f"{_local(e.time)}  - {_exclusion(_defender_value(e.data)[0])}"
                                      for e in removed], DEFENDER))
    return out


def _exclusion(value: str) -> str:
    r"""`HKLM\...\Exclusions\Paths\C:\X = 0x0` -> `Paths: C:\X`."""
    tail = value.split("\\Exclusions\\", 1)[-1].rsplit(" = ", 1)[0]
    kind, _, what = tail.partition("\\")
    return f"{kind}: {what}" if what else tail


_ADMIN_GROUPS = ("s-1-5-32-544",)


def check_accounts(q: Query) -> List[Finding]:
    """Accounts created/enabled/deleted, group membership, changes by a person."""
    out = []
    by_id: Dict[int, List[Event]] = {}
    for e in q.events:
        by_id.setdefault(e.id, []).append(e)

    def who(e: Event) -> str:
        return f"{e.data.get('SubjectDomainName', '')}\\{e.data.get('SubjectUserName', '?')}"

    added = by_id.get(4732, []) + by_id.get(4728, []) + by_id.get(4756, [])
    admins = [e for e in added if e.data.get("TargetSid", "").lower() in _ADMIN_GROUPS
              or e.data.get("TargetUserName", "").lower() == "administrators"]
    if admins:
        out.append(_finding("accounts.admin_added", HIGH, "Someone was added to Administrators",
                            "A new local administrator is the end goal of most intrusions.",
                            admins, [f"{_local(e.time)}  {e.data.get('MemberName') or e.data.get('MemberSid')}"
                                     f"  added by {who(e)}" for e in admins], SECURITY,
                            "Check Local Users & Groups; remove anyone you did not add."))
    other = [e for e in added if e not in admins]
    if other:
        out.append(_finding("accounts.group_added", MEDIUM, "Members added to security groups",
                            "Group membership changes outside Administrators.", other,
                            [f"{_local(e.time)}  {e.data.get('MemberName') or e.data.get('MemberSid')} -> "
                             f"{e.data.get('TargetUserName')}  by {who(e)}" for e in other], SECURITY))
    for eid, key, sev, title in ((4720, "created", HIGH, "User account created"),
                                 (4722, "enabled", MEDIUM, "User account enabled"),
                                 (4726, "deleted", MEDIUM, "User account deleted")):
        evs = by_id.get(eid, [])
        if evs:
            out.append(_finding(f"accounts.{key}", sev, f"{title} ({len(evs)})",
                                "Local account lifecycle. A built-in Administrator or Guest "
                                "enabled is worth a second look.", evs,
                                [f"{_local(e.time)}  {e.data.get('TargetUserName')}  by {who(e)}"
                                 for e in evs], SECURITY))
    by_person = [e for e in by_id.get(4738, []) if e.data.get("SubjectUserSid") != "S-1-5-18"]
    if by_person:
        out.append(_finding("accounts.changed", INFO, "Accounts changed by a person",
                            "Account properties changed by someone other than SYSTEM (SYSTEM's "
                            "own changes -- Microsoft-account sync -- are left out).", by_person,
                            [f"{_local(e.time)}  {e.data.get('TargetUserName')}  by {who(e)}"
                             for e in by_person], SECURITY))
    return out


_SUBCATEGORY_NAMES = {s.guid.strip("{}").lower(): s.name for s in audit_policy.SUBCATEGORIES}


def check_audit_trail(security: Query, system: Query) -> List[Finding]:
    """Cleared logs and changed audit policy: the anti-forensics steps."""
    out = []
    cleared = [e for e in security.events if e.id == 1102] + [e for e in system.events if e.id == 104]
    if cleared:
        out.append(_finding("trail.cleared", HIGH, "An event log was cleared",
                            "Clearing a log erases the evidence before it. Admins rarely do it; "
                            "attackers routinely do.", cleared,
                            [f"{_local(e.time)}  {e.data.get('Channel') or 'Security'}  by "
                             f"{e.data.get('SubjectUserName') or e.data.get('SubjectUserSid', '?')}"
                             for e in cleared], SECURITY))
    changed = [e for e in security.events if e.id == 4719]
    if changed:
        lines = []
        for e in changed:
            guid = e.data.get("SubcategoryGuid", "").strip("{}").lower()
            name = _SUBCATEGORY_NAMES.get(guid, e.data.get("SubcategoryGuid", "?"))
            lines.append(f"{_local(e.time)}  {name}  by {e.data.get('SubjectUserName', '?')}")
        out.append(_finding("trail.audit_policy", MEDIUM, "Audit policy changed",
                            "Someone changed what Windows audits. Turning auditing off hides "
                            "what follows; if these were your own changes (this app's Audit & "
                            "Logging tab writes them), they are expected.", changed, lines,
                            SECURITY))
    return out


def check_logons(q: Query) -> List[Finding]:
    """Failed sign-ins, lockouts, and remote sign-ins, grouped by source."""
    out = []
    failed = [e for e in q.events if e.id == 4625]
    if failed:
        groups = Counter((e.data.get("TargetUserName", "?"), e.data.get("IpAddress", "-"),
                          e.data.get("LogonType", "?")) for e in failed)
        lines = [f"{user} from {ip or '-'} (logon type {lt})  x{n}"
                 for (user, ip, lt), n in groups.most_common()]
        sev = MEDIUM if len(failed) >= 10 or any(n >= 5 for n in groups.values()) else INFO
        out.append(_finding("logons.failed", sev, f"{len(failed)} failed sign-ins",
                            "Repeated failures for one account or from one address are password "
                            "guessing; a few are typos.", failed, lines, SECURITY))
    lockouts = [e for e in q.events if e.id == 4740]
    if lockouts:
        out.append(_finding("logons.lockouts", MEDIUM, "Accounts locked out",
                            "Too many bad passwords locked an account.", lockouts,
                            [f"{_local(e.time)}  {e.data.get('TargetUserName')}" for e in lockouts],
                            SECURITY))
    remote = [e for e in q.events if e.id == 4624 and e.data.get("LogonType") == "10"]
    if remote:
        groups = Counter((e.data.get("TargetUserName", "?"), e.data.get("IpAddress", "-"))
                         for e in remote)
        out.append(_finding("logons.rdp", MEDIUM, "Remote Desktop sign-ins",
                            "Interactive sign-ins over Remote Desktop, by account and address.",
                            remote, [f"{u} from {ip}  x{n}" for (u, ip), n in groups.most_common()],
                            SECURITY))
    return out


_CDXML = re.compile(r"Microsoft\.PowerShell\.Cmdletization", re.I)


def check_powershell(q: Query) -> List[Finding]:
    """PowerShell's own 'suspicious script block' flags (4104, Level 3), grouped."""
    firsts = [e for e in q.events if e.data.get("MessageNumber", "1") == "1"]
    if not firsts:
        return []
    benign = [e for e in firsts if _CDXML.search(e.data.get("ScriptBlockText", ""))]
    rest = [e for e in firsts if e not in benign]
    out = []
    if rest:
        groups: "OrderedDict[str, List[Event]]" = OrderedDict()
        for e in rest:
            head = " ".join(e.data.get("ScriptBlockText", "").split())[:160]
            groups.setdefault(head, []).append(e)
        lines = [f"x{len(evs)}, last {_local(evs[0].time)}"
                 f"{'  [' + evs[0].data.get('Path') + ']' if evs[0].data.get('Path') else ''}:  {head}"
                 for head, evs in groups.items()]
        out.append(_finding("powershell.flagged", MEDIUM,
                            f"PowerShell flagged {len(rest)} script blocks as suspicious "
                            f"({len(groups)} distinct)",
                            "PowerShell logs a script block as a Warning when it contains "
                            "keywords attackers use (Base64, reflection, downloads). Many are "
                            "admin tooling -- read each distinct one.", rest, lines, POWERSHELL))
    if benign:
        out.append(_finding("powershell.cdxml", INFO,
                            f"{len(benign)} flagged blocks are PowerShell's own CDXML wrappers",
                            "Auto-generated modules for CIM cmdlets (Get-Volume, Get-NetAdapter...) "
                            "trip the heuristic every time they load; they are not a finding.",
                            benign, [], POWERSHELL))
    return out


def check_tasks(q: Query, task_log_enabled: Optional[bool]) -> List[Finding]:
    """Scheduled tasks registered (Task Scheduler 106) -- or why they are not recorded."""
    if task_log_enabled is False:
        return [Finding("tasks.not_recorded", UNKNOWN, "Scheduled-task creation is not recorded",
                        "Task Scheduler's history log is off (Windows' default), so a task "
                        "created to run malware at night leaves no event here.", source=TASKS,
                        fix="Turn on Task Scheduler history in Security Dashboard > Audit & "
                            "Logging, and review existing tasks in Startup & Boot > Persistence.")]
    if not q.events:
        return []
    lines = [f"{_local(e.time)}  {e.data.get('TaskName', '?')}  by {e.data.get('UserContext', '?')}"
             for e in q.events]
    return [_finding("tasks.registered", INFO, f"{len(q.events)} scheduled tasks registered",
                     "Tasks registered or updated. Software updaters do this constantly; an "
                     "unfamiliar name or a task created by an unexpected account is the lead.",
                     q.events, lines, TASKS)]


# ---- orchestration ---------------------------------------------------------------------------

def _coverage(q: Query) -> Optional[Finding]:
    if q.refused:
        return Finding(f"coverage.{q.channel}", UNKNOWN, f"Could not read {q.channel}",
                       f"{q.refused}. Nothing from this log is checked -- this is not a clean "
                       "result.", source=q.channel,
                       fix="Restart the app as administrator." if "admin" in q.refused else "")
    if q.capped:
        return Finding(f"coverage.{q.channel}.capped", INFO, f"{q.channel}: only the newest "
                       f"{MAX_EVENTS} matching events were read",
                       "Counts from this log are 'at least'.", source=q.channel)
    return None


def triage(days: int = 30, run: Optional[Run] = None) -> Triage:
    """Every check over the last `days` days. Never raises for an unreadable log."""
    sec = read(SECURITY, xpath((4625, 4740, 4720, 4722, 4726, 4738, 4728, 4732, 4756, 1102,
                                4719), days), run)
    # Successful sign-ins are by far the busiest event; only Remote Desktop ones are read.
    sec_rdp = read(SECURITY, xpath((4624,), days)[:-1]
                   + " and EventData[Data[@Name='LogonType']='10']]", run)
    sys_q = read(SYSTEM, xpath((7045, 104), days), run)
    dfn = read(DEFENDER, xpath((1116, 1117, 5001, 5007), days), run)
    ps = read(POWERSHELL, xpath((4104,), days, " and Level=3"), run)
    tasks = read(TASKS, xpath((106,), days), run)
    task_status = log_readiness.read_log(TASKS, run)
    result = Triage(days, sources=[sec, sec_rdp, sys_q, dfn, ps, tasks])
    sec = Query(SECURITY, sec.events + sec_rdp.events, sec.refused, sec.absent, sec.capped)
    f = result.findings
    f += check_services(Query(SYSTEM, [e for e in sys_q.events if e.id == 7045]))
    f += check_defender(dfn, _read_av(run) if any(e.id == 5001 for e in dfn.events) else [])
    f += check_accounts(sec)
    f += check_audit_trail(sec, sys_q)
    f += check_logons(sec)
    f += check_powershell(ps)
    f += check_tasks(tasks, task_status.enabled)
    for q in result.sources:
        cov = _coverage(q)
        if cov is not None and cov.key not in {x.key for x in f}:
            f.append(cov)
    f.sort(key=lambda x: (SEVERITY_RANK.get(x.severity, 9),
                          -(x.last.timestamp() if x.last else 0)))
    return result


def summary(t: Triage) -> str:
    counts = Counter(f.severity for f in t.findings)
    parts = [f"{counts[s]} {s}" for s in (HIGH, MEDIUM, UNKNOWN) if counts[s]]
    return (f"Last {t.days} days: " + (", ".join(parts) if parts else "nothing that needs a look")
            + ".")


def as_text(t: Triage) -> str:
    """A plain-text report (copy to clipboard / save)."""
    lines = [summary(t), ""]
    for f in t.findings:
        when = f"{_local(f.first)} .. {_local(f.last)}" if f.count else ""
        lines.append(f"[{f.severity.upper()}] {f.title}" + (f"  ({f.count}; {when})" if when else ""))
        lines.append(f"    {f.detail}")
        lines += [f"    - {ev}" for ev in f.evidence]
        if f.fix:
            lines.append(f"    Fix: {f.fix}")
        lines.append("")
    return "\n".join(lines)
