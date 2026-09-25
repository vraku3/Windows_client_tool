"""What the Overview's "Needs attention" panel is made of. No Qt in here.

Every check returns findings, and a check that could not look says so with an
"unknown" finding instead of returning nothing: an empty list must mean "looked,
found nothing", never "was refused" -- the rule Security Dashboard and the Tweak
System hold too.
"""
import logging
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional

logger = logging.getLogger(__name__)

CRITICAL, WARNING, INFO, UNKNOWN = "critical", "warning", "info", "unknown"
_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2, UNKNOWN: 3}

#: Days of uptime after which a reboot is worth suggesting.
LONG_UPTIME_DAYS = 14
#: Free space below which a volume is a warning / critical.
DISK_WARN_PCT, DISK_CRIT_PCT = 15.0, 5.0
MEMORY_WARN_PCT, MEMORY_CRIT_PCT = 85.0, 95.0


@dataclass(frozen=True)
class Finding:
    severity: str
    title: str
    detail: str = ""
    #: Sidebar / tab name to jump to for the fix, "" for none.
    action_module: str = ""
    action_label: str = ""


def sort_findings(findings: List[Finding]) -> List[Finding]:
    return sorted(findings, key=lambda f: _ORDER.get(f.severity, 9))


# ---- pure judgements (unit-testable with plain numbers) -------------------

def judge_disk(mount: str, free: int, total: int) -> Optional[Finding]:
    if total <= 0:
        return None
    pct = free * 100.0 / total
    if pct >= DISK_WARN_PCT:
        return None
    level = CRITICAL if pct < DISK_CRIT_PCT else WARNING
    return Finding(level, f"{mount} is nearly full",
                   f"{pct:.1f}% free ({format_size(free)} of {format_size(total)})",
                   "Cleanup", "Open Cleanup")


def judge_memory(percent: float, used: int, total: int) -> Optional[Finding]:
    if percent < MEMORY_WARN_PCT:
        return None
    level = CRITICAL if percent >= MEMORY_CRIT_PCT else WARNING
    return Finding(level, "Memory is under pressure",
                   f"{percent:.0f}% in use ({format_size(used)} of {format_size(total)})",
                   "Dashboard", "See top processes")


def judge_commit(percent: float, used: int, total: int) -> Optional[Finding]:
    """The page file is what a commit limit hit runs out of."""
    if total <= 0 or percent < MEMORY_WARN_PCT:
        return None
    return Finding(WARNING, "Commit charge is close to its limit",
                   f"{percent:.0f}% of RAM + page file committed "
                   f"({format_size(used)} of {format_size(total)}); "
                   "allocations start failing at 100%")


def judge_uptime(seconds: float) -> Optional[Finding]:
    days = seconds / 86400
    if days < LONG_UPTIME_DAYS:
        return None
    return Finding(INFO, f"Up for {days:.0f} days without a restart",
                   "Updates, drivers and leaked memory pile up over long uptimes")


def judge_shutdowns(count: Optional[int], days: int = 7) -> Optional[Finding]:
    if count is None:
        return Finding(UNKNOWN, "Could not read the System event log",
                       "Unexpected shutdowns and bugchecks were not checked")
    if count == 0:
        return None
    return Finding(WARNING if count < 3 else CRITICAL,
                   f"{count} unexpected shutdown/crash event(s) in {days} days",
                   "Kernel-Power 41, unclean shutdown 6008 or a bugcheck 1001",
                   "Diagnose", "Open Event Viewer")


def judge_drivers(count: Optional[int]) -> Optional[Finding]:
    if not count:      # None = not scanned yet: nothing to say, not "fine"
        return None
    return Finding(WARNING, f"{count} driver(s) need attention",
                   "Error code set, or not signed", "Driver Manager", "Open Drivers")


def format_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ---- readers ---------------------------------------------------------------

_REBOOT_KEYS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
     "servicing (CBS)"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
     "Windows Update"),
)


def pending_reboot_reasons() -> Optional[List[str]]:
    """Why Windows wants a restart, or None if that could not be read."""
    import winreg
    reasons: List[str] = []
    for path, label in _REBOOT_KEYS:
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
            reasons.append(label)
        except FileNotFoundError:
            logger.debug("no pending-reboot marker at %s", path)
            continue
        except OSError as e:
            logger.warning("pending-reboot key %s unreadable: %s", path, e)
            return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager") as k:
            if winreg.QueryValueEx(k, "PendingFileRenameOperations")[0]:
                reasons.append("queued file replacements")
    except FileNotFoundError:
        logger.debug("no PendingFileRenameOperations value")
    except OSError as e:
        logger.warning("PendingFileRenameOperations unreadable: %s", e)
        return None
    return reasons


def judge_reboot(reasons: Optional[List[str]]) -> Optional[Finding]:
    if reasons is None:
        return Finding(UNKNOWN, "Could not check for a pending restart")
    if not reasons:
        return None
    return Finding(WARNING, "A restart is pending",
                   "Waiting on: " + ", ".join(reasons))


def unexpected_shutdown_count(days: int = 7) -> Optional[int]:
    """Kernel-Power 41, EventLog 6008 and bugcheck 1001 in the last `days`."""
    ms = days * 86400 * 1000
    query = ("*[System[(EventID=41 or EventID=6008 or EventID=1001) and "
             f"TimeCreated[timediff(@SystemTime) <= {ms}]]]")
    try:
        done = subprocess.run(
            ["wevtutil", "qe", "System", f"/q:{query}", "/c:100", "/f:xml"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("wevtutil failed: %s", e)
        return None
    if done.returncode != 0:
        logger.warning("wevtutil rc=%s: %s", done.returncode, done.stderr.strip())
        return None
    return done.stdout.count("<Event ")


# ---- assembly ---------------------------------------------------------------

def collect_findings(driver_problems: Optional[int] = None,
                     shutdown_counter: Callable = unexpected_shutdown_count,
                     reboot_reader: Callable = pending_reboot_reasons) -> List[Finding]:
    """Everything that needs attention, worst first. Run on a worker thread."""
    import psutil
    found: List[Optional[Finding]] = []
    for part in psutil.disk_partitions(all=False):
        if not part.fstype or "cdrom" in part.opts:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError as e:
            logger.warning("disk_usage %s: %s", part.mountpoint, e)
            continue
        found.append(judge_disk(part.mountpoint.rstrip("\\"), u.free, u.total))
    vm = psutil.virtual_memory()
    found.append(judge_memory(vm.percent, vm.used, vm.total))
    sw = psutil.swap_memory()
    found.append(judge_commit(sw.percent, sw.used, sw.total))
    found.append(judge_uptime(time.time() - psutil.boot_time()))
    found.append(judge_reboot(reboot_reader()))
    found.append(judge_shutdowns(shutdown_counter()))
    found.append(judge_drivers(driver_problems))
    return sort_findings([f for f in found if f is not None])


def summary_text(host: str, os_name: str, cpu: str, uptime: str,
                 findings: List[Finding], lines: List[str]) -> str:
    """A plain-text snapshot for pasting into a ticket."""
    out = [f"{host} - {os_name}", f"CPU: {cpu}", f"Uptime: {uptime}", *lines, ""]
    if findings:
        out.append("Needs attention:")
        out += [f"  [{f.severity}] {f.title}" + (f" - {f.detail}" if f.detail else "")
                for f in findings]
    else:
        out.append("Nothing needs attention.")
    return "\n".join(out)


class History:
    """The last N readings of one metric, for a sparkline."""

    def __init__(self, size: int = 60) -> None:
        self._values: Deque[float] = deque(maxlen=size)

    def add(self, value: float) -> None:
        self._values.append(float(value))

    def values(self) -> List[float]:
        return list(self._values)


def heat_level(pct: float) -> str:
    """'ok' / 'warn' / 'hot' -- one place decides where the colours change."""
    if pct >= 90:
        return "hot"
    if pct >= 70:
        return "warn"
    return "ok"
