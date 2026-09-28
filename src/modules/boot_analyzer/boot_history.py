"""What Windows itself measured about the last boots -- Qt-free.

Source: the Microsoft-Windows-Diagnostics-Performance/Operational log, which
is readable without elevation (checked on this machine):

* event 100 -- one per boot: BootTime, MainPathBootTime, PostBootTime, the
  number of startup apps, and the degradation flag + root-cause bits.
* events 101 / 102 / 103 -- an app / driver / service that slowed a boot
  (TotalTime and DegradationTime in ms).

plus Microsoft-Windows-Kernel-Boot event 27 (boot type: full / fast startup /
resume from hibernate) from the System log, and the Fast Startup registry
switch.

A log that cannot be read is `None` with a reason, never an empty list:
"no boots recorded" and "we were refused" are different claims.
"""
import ctypes
import logging
import subprocess
import winreg
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PERF_LOG = "Microsoft-Windows-Diagnostics-Performance/Operational"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BOOT_TYPES = {0: "Full boot", 1: "Fast Startup (hybrid)", 2: "Resume from hibernate"}

#: BootRootCause*Bits are a bitmask; Microsoft documents the names in the
#: Windows Performance Toolkit, not in a public header, so they are shown as
#: the raw mask rather than a guessed list of causes.
SLOW_KIND = {101: "App", 102: "Driver", 103: "Service"}


@dataclass
class BootRecord:
    when: datetime                # local time the boot finished being logged
    boot_ms: int
    main_path_ms: int
    post_boot_ms: int
    startup_apps: int
    degraded: bool
    degradation_delta_ms: int
    root_cause_bits: int
    boot_type: Optional[str] = None   # matched from Kernel-Boot event 27


@dataclass
class SlowItem:
    when: datetime
    kind: str                     # App / Driver / Service
    name: str
    friendly: str
    company: str
    path: str
    total_ms: int
    degradation_ms: int


@dataclass
class SlowSummary:
    """One name across the events in the window."""
    kind: str
    name: str
    company: str
    path: str
    count: int
    worst_ms: int
    avg_ms: int
    last_seen: datetime


@dataclass
class BootFacts:
    boots: List[BootRecord] = field(default_factory=list)
    slow: List[SlowItem] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)   # logs we could not read
    fast_startup: Optional[bool] = None
    hibernate_file: Optional[bool] = None
    uptime_s: Optional[float] = None


# ---- parsing (pure, unit-tested) ---------------------------------------

def _data(event: ET.Element) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for node in event.iter():
        if node.tag.endswith("}Data") or node.tag == "Data":
            name = node.get("Name")
            if name:
                out[name] = (node.text or "").strip()
    return out


def _time_created(event: ET.Element) -> Optional[datetime]:
    for node in event.iter():
        if node.tag.endswith("TimeCreated"):
            text = node.get("SystemTime", "")
            try:
                base = text.rstrip("Z")
                if "." in base:
                    head, frac = base.split(".")
                    base = f"{head}.{frac[:6]}"
                return datetime.fromisoformat(base).replace(
                    tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
            except ValueError:
                logger.debug("Unparseable SystemTime %r", text)
    return None


def _event_id(event: ET.Element) -> Optional[int]:
    for node in event.iter():
        if node.tag.endswith("}EventID") or node.tag == "EventID":
            try:
                return int((node.text or "").strip())
            except ValueError:
                return None
    return None


def split_events(xml_text: str) -> List[ET.Element]:
    """`wevtutil /f:xml` prints one <Event> document after another."""
    if not xml_text.strip():
        return []
    root = ET.fromstring("<Events>" + xml_text + "</Events>")
    return list(root)


def _int(data: Dict[str, str], key: str) -> int:
    try:
        return int(data.get(key, "0") or 0)
    except ValueError:
        return 0


def parse_boot_event(event: ET.Element) -> Optional[BootRecord]:
    when = _time_created(event)
    data = _data(event)
    if when is None or "BootTime" not in data:
        return None
    causes = sum(_int(data, k) for k in (
        "BootRootCauseStepImprovementBits", "BootRootCauseGradualImprovementBits",
        "BootRootCauseStepDegradationBits", "BootRootCauseGradualDegradationBits"))
    return BootRecord(
        when=when, boot_ms=_int(data, "BootTime"),
        main_path_ms=_int(data, "MainPathBootTime"),
        post_boot_ms=_int(data, "BootPostBootTime"),
        startup_apps=_int(data, "BootNumStartupApps"),
        degraded=data.get("BootIsDegradation", "").lower() == "true",
        degradation_delta_ms=_int(data, "BootDegradationDelta"),
        root_cause_bits=causes)


def parse_slow_event(event: ET.Element) -> Optional[SlowItem]:
    when = _time_created(event)
    event_id = _event_id(event)
    data = _data(event)
    if when is None or event_id not in SLOW_KIND or not data.get("Name"):
        return None
    return SlowItem(
        when=when, kind=SLOW_KIND[event_id], name=data["Name"],
        friendly=data.get("FriendlyName", ""), company=data.get("CompanyName", ""),
        path=data.get("Path", "").strip('"'), total_ms=_int(data, "TotalTime"),
        degradation_ms=_int(data, "DegradationTime"))


def summarise_slow(items: List[SlowItem]) -> List[SlowSummary]:
    """Group by (kind, name), worst first."""
    grouped: Dict[Tuple[str, str], List[SlowItem]] = {}
    for item in items:
        grouped.setdefault((item.kind, item.name.lower()), []).append(item)
    out = []
    for group in grouped.values():
        first = max(group, key=lambda i: i.when)
        totals = [i.total_ms for i in group]
        out.append(SlowSummary(
            kind=first.kind, name=first.friendly or first.name,
            company=first.company, path=first.path, count=len(group),
            worst_ms=max(totals), avg_ms=sum(totals) // len(totals),
            last_seen=first.when))
    return sorted(out, key=lambda s: s.worst_ms, reverse=True)


def match_boot_types(boots: List[BootRecord],
                     kernel_events: List[Tuple[datetime, int]]) -> None:
    """Attach the Kernel-Boot type to the boot whose log time follows it.

    Event 27 is written as the kernel starts; event 100 minutes later. The
    nearest preceding type within 3 hours is the boot's. Two boots can never
    share one type event.
    """
    used = set()
    for boot in sorted(boots, key=lambda b: b.when):
        best = None
        for index, (when, code) in enumerate(kernel_events):
            if index in used or when > boot.when:
                continue
            if boot.when - when > timedelta(hours=3):
                continue
            if best is None or when > kernel_events[best][0]:
                best = index
        if best is not None:
            used.add(best)
            boot.boot_type = BOOT_TYPES.get(kernel_events[best][1],
                                            f"Unknown type {kernel_events[best][1]:#x}")


def parse_kernel_boot(event: ET.Element) -> Optional[Tuple[datetime, int]]:
    when = _time_created(event)
    data = _data(event)
    raw = data.get("BootType")
    if when is None or raw is None:
        return None
    try:
        return when, int(raw, 0)
    except ValueError:
        return None


# ---- reading the machine -----------------------------------------------

def _query(log: str, xpath: str, count: int) -> Tuple[Optional[List[ET.Element]], Optional[str]]:
    """Newest-first events, or (None, reason) when the log cannot be read."""
    try:
        done = subprocess.run(
            ["wevtutil", "qe", log, f"/q:{xpath}", f"/c:{count}", "/rd:true", "/f:xml"],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("wevtutil failed for %s: %s", log, error)
        return None, f"could not run wevtutil: {error}"
    if done.returncode != 0:
        reason = (done.stderr or done.stdout).strip().splitlines()
        return None, f"{log}: {reason[0] if reason else 'access denied or log missing'}"
    try:
        return split_events(done.stdout), None
    except ET.ParseError as error:
        logger.warning("Unparseable wevtutil output for %s: %s", log, error)
        return None, f"{log}: output could not be parsed"


def read_fast_startup() -> Tuple[Optional[bool], Optional[bool]]:
    """(HiberbootEnabled, hiberfil present) -- None = could not read."""
    fast: Optional[bool] = None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager\Power") as key:
            fast = bool(winreg.QueryValueEx(key, "HiberbootEnabled")[0])
    except FileNotFoundError:
        fast = False          # the value is absent when the policy was never set
    except OSError as error:
        logger.warning("Could not read HiberbootEnabled: %s", error)
    hib: Optional[bool] = None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Power") as key:
            hib = bool(winreg.QueryValueEx(key, "HibernateEnabled")[0])
    except OSError as error:
        logger.debug("HibernateEnabled not readable: %s", error)
    return fast, hib


def uptime_seconds() -> float:
    return ctypes.windll.kernel32.GetTickCount64() / 1000.0


def read_firmware_type() -> Tuple[str, Optional[bool]]:
    """(firmware, secure_boot) -- readable with NO elevation at all.

    `bcdedit /enum firmware` needs administrator; refused, it exits 1 with
    empty stdout, which BootAnalyzerModule used to read as "BIOS/Legacy" on
    a real UEFI machine before that call was guarded, and still only reports
    "Unknown" once it was. `SecureBoot\\State` is a better source for the
    same fact and needs no elevation at all: the subkey is created only on
    UEFI firmware (confirmed unelevated on this machine: the key exists and
    `UEFISecureBootEnabled` reads back False), so its absence is a real
    Legacy BIOS signal, not a refusal -- the same technique
    `hardware_inventory/asset_reader.py` and
    `security_dashboard/security_reader.py` already use for this exact
    fact. Boot Analyzer had neither, so an ordinary user opening it saw
    "Unknown" for a reading two other tabs already give unelevated.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\SecureBoot\State") as key:
            try:
                secure_boot = bool(winreg.QueryValueEx(key, "UEFISecureBootEnabled")[0])
            except OSError as error:
                logger.debug("UEFISecureBootEnabled not readable: %s", error)
                secure_boot = None
            return "UEFI", secure_boot
    except FileNotFoundError:
        return "Legacy BIOS", None
    except OSError as error:
        logger.warning("Firmware type registry read refused: %s", error)
        return "Unknown", None


def read_boot_facts(boots: int = 20, slow_events: int = 300) -> BootFacts:
    facts = BootFacts()
    events, why = _query(PERF_LOG, "*[System[(EventID=100)]]", boots)
    if events is None:
        facts.problems.append(why or "boot log unreadable")
    else:
        facts.boots = [b for b in (parse_boot_event(e) for e in events) if b]
    events, why = _query(PERF_LOG, "*[System[(EventID=101 or EventID=102 or EventID=103)]]",
                         slow_events)
    if events is None:
        facts.problems.append(why or "slow-item log unreadable")
    else:
        facts.slow = [s for s in (parse_slow_event(e) for e in events) if s]
    kernel, why = _query("System", "*[System[Provider[@Name='Microsoft-Windows-Kernel-Boot']"
                         " and (EventID=27)]]", boots + 10)
    if kernel is None:
        facts.problems.append(why or "kernel boot log unreadable")
    else:
        match_boot_types(facts.boots, [k for k in (parse_kernel_boot(e) for e in kernel) if k])
    facts.fast_startup, facts.hibernate_file = read_fast_startup()
    facts.uptime_s = uptime_seconds()
    return facts


# ---- interpretation ----------------------------------------------------

def fmt_ms(ms: int) -> str:
    return f"{ms / 1000:.1f} s"


def uptime_note(facts: BootFacts) -> str:
    """Uptime against the last boot Windows logged, and what Fast Startup means for it."""
    if facts.uptime_s is None:
        return "Uptime could not be read."
    days = facts.uptime_s / 86400
    text = f"Uptime {days:.1f} days ({facts.uptime_s / 3600:.1f} h)."
    last = facts.boots[0] if facts.boots else None
    if last is not None:
        since = (datetime.now() - last.when).total_seconds()
        text += f" Last logged boot {since / 3600:.1f} h ago"
        if last.boot_type:
            text += f" ({last.boot_type})"
        text += "."
        if facts.fast_startup and facts.uptime_s - since > 3600:
            text += (" Fast Startup is on, so the kernel session outlived the shutdown: "
                     "uptime counts from an earlier full boot.")
    if facts.fast_startup is True:
        text += " Fast Startup is ON: Shut down is a hybrid hibernate; only Restart is a full boot."
    elif facts.fast_startup is False:
        text += " Fast Startup is off: every shutdown is a full boot."
    return text


def parse_bcd(text: str) -> Dict[str, Optional[int]]:
    """`{"timeout": seconds-or-None, "entries": count-or-None}` from `bcdedit /enum all`.

    Sections are separated by blank lines and start with a title line then a
    dashed rule. The boot MENU timeout is the "Windows Boot Manager" section's;
    the firmware boot manager has a `timeout` of its own (1 here) that is not
    what the user waits through. Entries are "Windows Boot Loader" sections --
    counting the substring "bootloader" also counts `{bootloadersettings}`.
    Both are None when the output is not BCD at all (a refusal).
    """
    sections = [s for s in text.replace("\r", "").split("\n\n") if s.strip()]
    titled = [s for s in sections if "\n---" in s]
    if not titled:
        return {"timeout": None, "entries": None}
    timeout = None
    entries = 0
    for section in titled:
        title = section.strip().splitlines()[0].strip().lower()
        if title == "windows boot manager":
            for line in section.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0].lower() == "timeout" and parts[1].isdigit():
                    timeout = int(parts[1])
        elif title == "windows boot loader":
            entries += 1
    return {"timeout": timeout, "entries": entries}


def trend_note(boots: List[BootRecord]) -> str:
    """Median boot time and the slowest boot, plain."""
    if not boots:
        return "No boot events recorded."
    times = sorted(b.boot_ms for b in boots)
    median = times[len(times) // 2]
    worst = max(boots, key=lambda b: b.boot_ms)
    return (f"{len(boots)} boots: median {fmt_ms(median)}, slowest {fmt_ms(worst.boot_ms)} "
            f"on {worst.when:%Y-%m-%d %H:%M}.")
