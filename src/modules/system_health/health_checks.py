"""Computed System Health findings that read the live machine. No Qt, no writes.

Every check returns a list of `Finding`s. A read that fails or is refused is
reported as an "unknown" finding of its own (`id` ends in `:unknown`), never
as silence -- silence would read as "healthy".
"""
import ctypes
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from modules.system_health.findings import Finding
from core.windows_utils import system_root

logger = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _unknown(check: str, title: str, why: str, jump: str = "") -> Finding:
    return Finding(id=f"{check}:unknown", title=f"Could not check {title}",
                   detail="The read failed or was refused, so nothing can be said either way.",
                   severity="info", evidence=why, jump=jump)


def _run(cmd: List[str], timeout: int = 30) -> Tuple[Optional[int], str]:
    """(returncode, stdout+stderr), or (None, reason) when it could not run."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("%s could not run: %s", cmd[0], exc)
        return None, str(exc)
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def _ps_json(script: str, timeout: int = 60):
    """Run PowerShell that ends in ConvertTo-Json; (data, None) or (None, reason)."""
    rc, out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout)
    if rc != 0:
        return None, out[:300] or f"powershell exited {rc}"
    try:
        data = json.loads(out or "[]")
    except ValueError:
        return None, "PowerShell did not return JSON"
    return (data if isinstance(data, list) else [data]), None


# ---- pending reboot --------------------------------------------------------------

_REBOOT_KEYS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
     "Component servicing (CBS) needs a reboot"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
     "Windows Update needs a reboot"),
)


def _key_exists(path: str) -> Optional[bool]:
    import winreg
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("registry key %s unreadable: %s", path, exc)
        return None


def _pending_renames() -> Optional[List[str]]:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager") as key:
            value, _ = winreg.QueryValueEx(key, "PendingFileRenameOperations")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("PendingFileRenameOperations unreadable: %s", exc)
        return None
    # Raw, empty strings included: an empty destination is what marks a
    # DELETE. Dropping them (as this used to) shifts every later pair.
    return list(value) if not isinstance(value, str) else [value]


def pending_reboot_reasons(key_exists: Callable = _key_exists,
                           renames: Callable = _pending_renames) -> Tuple[List[str], List[str]]:
    """(reasons, unreadable) -- what asks for a reboot, and what could not be read."""
    reasons, unreadable = [], []
    for path, why in _REBOOT_KEYS:
        found = key_exists(path)
        if found:
            reasons.append(why)
        elif found is None:
            unreadable.append(path.rsplit("\\", 1)[-1])
    raw = renames()
    if raw is None:
        unreadable.append("PendingFileRenameOperations")
        return reasons, unreadable
    # Only REPLACEMENTS need the restart; deletions are updater leftovers
    # (OneDrive, Edge) that are queued again after every boot.
    from core.pending_reboot import REPLACE, parse_operations
    replaced = [op for op in parse_operations(raw) if op.kind == REPLACE]
    if replaced:
        owners = sorted({op.owner for op in replaced})
        reasons.append(f"{len(replaced)} file replacement(s) queued for the next boot by "
                       f"{', '.join(owners)} (first: {replaced[0].path[:90]})")
    return reasons, unreadable


def check_pending_reboot(reader: Callable = pending_reboot_reasons) -> List[Finding]:
    reasons, unreadable = reader()
    out: List[Finding] = []
    if reasons:
        out.append(Finding(
            id="pending_reboot", title="A restart is pending",
            detail="Windows or an installer has work that only completes on the next boot. "
                   "Servicing, driver and update operations may refuse to run until then.",
            severity="warning", evidence="\n".join(reasons), jump="Updates"))
    if unreadable:
        out.append(_unknown("pending_reboot", "every pending-restart marker", ", ".join(unreadable)))
    return out


# ---- time sync -------------------------------------------------------------------

def parse_w32tm_status(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def evaluate_time_sync(rc: Optional[int], text: str, now: Optional[datetime] = None) -> List[Finding]:
    now = now or datetime.now()
    lowered = text.lower()
    if rc is None:
        return [_unknown("time_sync", "time synchronisation", text, jump="Services")]
    if rc != 0 or "service has not been started" in lowered or "0x80070426" in lowered:
        return [Finding(
            id="time_sync", title="The Windows Time service is not answering",
            detail="Clock drift breaks Kerberos, TLS and log correlation. Start the Windows Time "
                   "service (W32Time), then run w32tm /resync.",
            severity="warning", evidence=text[:300], jump="Services")]
    fields = parse_w32tm_status(text)
    source = fields.get("Source", "")
    last = fields.get("Last Successful Sync Time", "")
    problems: List[str] = []
    if fields.get("Leap Indicator", "").startswith("3"):
        problems.append("Leap indicator 3: the clock is not synchronised")
    if "cmos" in source.lower() or "free-running" in source.lower():
        problems.append(f"Time source is '{source}', not a time server")
    if not last or last.lower() == "unspecified":
        problems.append("There has never been a successful sync this boot")
    else:
        stamp = _parse_when(last)
        if stamp and now - stamp > timedelta(days=7):
            problems.append(f"Last successful sync was {last} (over 7 days ago)")
    if not problems:
        return []
    return [Finding(
        id="time_sync", title="The clock is not being synchronised",
        detail="Clock drift breaks Kerberos, TLS and log correlation. Check the W32Time service "
               "and the configured peers (w32tm /query /peers), then w32tm /resync.",
        severity="warning", evidence="\n".join(problems + ["", *text.splitlines()[:8]]),
        jump="Services")]


def _parse_when(text: str) -> Optional[datetime]:
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            logger.debug("w32tm timestamp %r is not %s", text, fmt)
            continue
    logger.debug("w32tm timestamp not understood: %r", text)
    return None


def check_time_sync() -> List[Finding]:
    rc, text = _run(["w32tm", "/query", "/status"], 15)
    return evaluate_time_sync(rc, text)


# ---- disk health -----------------------------------------------------------------

def evaluate_physical_disks(disks: List[Dict]) -> List[Finding]:
    out = []
    for d in disks:
        health = str(d.get("HealthStatus") or "")
        op = str(d.get("OperationalStatus") or "")
        name = d.get("FriendlyName") or "disk"
        if health.lower() != "healthy" or op.lower() not in ("ok", ""):
            out.append(Finding(
                id=f"disk_health:{name}", title=f"{name} reports {health or 'an unknown health state'}",
                detail="Storage reports the drive as not healthy. Back up now, then check SMART and the "
                       "vendor's tool; do not run repair operations on a failing drive first.",
                severity="warning",
                evidence=f"HealthStatus={health}, OperationalStatus={op}, MediaType={d.get('MediaType')}",
                jump="Disk Health"))
    return out


def check_disks() -> List[Finding]:
    data, why = _ps_json("Get-PhysicalDisk | Select FriendlyName,HealthStatus,OperationalStatus,MediaType "
                         "| ConvertTo-Json -Compress")
    if data is None:
        return [_unknown("disk_health", "physical disk health", why or "", jump="Disk Health")]
    return evaluate_physical_disks(data)


# ---- WMI repository --------------------------------------------------------------

def check_wmi_repository() -> List[Finding]:
    rc, out = _run(["winmgmt", "/verifyrepository"], 60)
    if rc is None or "access is denied" in out.lower():
        return [_unknown("wmi_repository", "the WMI repository", out)]
    if "inconsistent" in out.lower():
        return [Finding(
            id="wmi_repository", title="The WMI repository is inconsistent",
            detail="Inventory, Task Manager details, many management tools and some installers depend on "
                   "WMI. From an elevated prompt: winmgmt /salvagerepository (then /resetrepository if that fails).",
            severity="warning", evidence=out[:300])]
    if "is consistent" in out.lower():
        return []
    return [_unknown("wmi_repository", "the WMI repository", out or "no output")]


# ---- commit charge ---------------------------------------------------------------

class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def commit_charge() -> Optional[Tuple[int, int]]:
    """(committed, limit) in bytes, or None if the call failed."""
    st = _MemStatus()
    st.dwLength = ctypes.sizeof(_MemStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return None
    return st.ullTotalPageFile - st.ullAvailPageFile, st.ullTotalPageFile


def evaluate_commit(reading: Optional[Tuple[int, int]]) -> List[Finding]:
    if reading is None:
        return [_unknown("commit", "commit charge", "GlobalMemoryStatusEx failed")]
    used, limit = reading
    if not limit:
        return [_unknown("commit", "commit charge", "the commit limit read as 0")]
    pct = used * 100 // limit
    if pct < 85:
        return []
    gb = 1024 ** 3
    return [Finding(
        id="commit", title=f"Commit charge is at {pct}% of the limit",
        detail="When commit reaches the limit, allocations fail and apps crash with out-of-memory errors "
               "even if RAM looks free. Close big processes or enlarge the page file.",
        severity="warning", evidence=f"{used / gb:.1f} GB committed of {limit / gb:.1f} GB", jump="Dashboard")]


def check_commit() -> List[Finding]:
    return evaluate_commit(commit_charge())


# ---- CBS corruption hints --------------------------------------------------------

_CBS_HINTS = re.compile(r"(\[SR\] Cannot repair member file|CSI Payload Corrupt|Cannot repair.*store|"
                        r"Component store is repairable|STATUS_SXS_COMPONENT_STORE_CORRUPT)", re.I)


def scan_cbs_text(text: str) -> List[str]:
    return [line.strip()[:200] for line in text.splitlines() if _CBS_HINTS.search(line)]


def check_cbs_log(tail_bytes: int = 8 * 1024 * 1024) -> List[Finding]:
    path = os.path.join(system_root(), "Logs", "CBS", "CBS.log")
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            text = fh.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return []   # Windows 11 may keep only CbsPersist cabs; the Diagnose tab handles those
    except OSError as exc:
        return [_unknown("cbs_log", "the CBS log", str(exc), jump="Diagnose")]
    hits = scan_cbs_text(text)
    if not hits:
        return []
    return [Finding(
        id="cbs_corruption", title=f"CBS log mentions component-store corruption ({len(hits)} line(s))",
        detail="Lines like these are what sfc/DISM write when a system file could not be repaired. "
               "Run DISM RestoreHealth, then sfc /scannow (Servicing tab).",
        severity="warning", evidence="\n".join(hits[-5:]) + f"\n(last {tail_bytes // (1024 * 1024)} MB of {path})",
        jump="Diagnose")]


# ---- services --------------------------------------------------------------------

def evaluate_services(rows: List[Dict], failures: Optional[Dict[str, int]]) -> List[Finding]:
    from modules.services_manager import service_audit
    out: List[Finding] = []
    unquoted = [r for r in rows if service_audit.is_unquoted_path(str(r.get("PathName") or ""))]
    if unquoted:
        evidence = "\n".join(f"{r.get('Name')}: {r.get('PathName')}" for r in unquoted[:10])
        out.append(Finding(
            id="unquoted_service_paths",
            title=f"{len(unquoted)} service(s) have an unquoted path with spaces",
            detail="Windows tries Program.exe at the drive root, then Program Files\\X.exe ... before the real file, so anyone "
                   "who can write to one of those locations gets code run as the service. Fix: put quotes "
                   "around the path in the service's ImagePath (HKLM\\SYSTEM\\CurrentControlSet\\Services\\<name>).",
            severity="warning", evidence=evidence, jump="Services"))
    if failures is None:
        out.append(_unknown("service_failures", "service crash history", "System log unreadable", jump="Services"))
        return out
    stopped = [r for r in rows
               if str(r.get("StartMode")).lower() == "auto" and str(r.get("State")).lower() == "stopped"
               and failures.get(str(r.get("DisplayName") or "").lower(), 0) > 0]
    if stopped:
        evidence = "\n".join(f"{r.get('DisplayName')} ({r.get('Name')}): "
                             f"{failures[str(r.get('DisplayName')).lower()]} crash event(s) in 30 days"
                             for r in stopped)
        out.append(Finding(
            id="failed_services", title=f"{len(stopped)} automatic service(s) crashed and are stopped",
            detail="Set to start automatically, not running, and the System log shows Service Control Manager "
                   "recording a crash. Check the recovery actions and the service's own log.",
            severity="warning", evidence=evidence, jump="Services"))
    return out


def check_services() -> List[Finding]:
    from modules.services_manager import service_audit
    rows, why = _ps_json("Get-CimInstance Win32_Service | Select Name,DisplayName,PathName,StartMode,State "
                         "| ConvertTo-Json -Compress", 90)
    if rows is None:
        return [_unknown("services", "services", why or "", jump="Services")]
    return evaluate_services(rows, service_audit.read_failure_counts())


# ---- entry point -----------------------------------------------------------------

CHECKS: Tuple[Tuple[str, Callable[[], List[Finding]]], ...] = (
    ("pending reboot", check_pending_reboot),
    ("time sync", check_time_sync),
    ("physical disks", check_disks),
    ("WMI repository", check_wmi_repository),
    ("commit charge", check_commit),
    ("CBS log", check_cbs_log),
    ("services", check_services),
)


def run_all(checks=CHECKS, is_cancelled: Callable[[], bool] = lambda: False) -> List[Finding]:
    """Every check, one after another; a check that raises becomes an unknown finding."""
    findings: List[Finding] = []
    for label, fn in checks:
        if is_cancelled():
            break
        try:
            findings.extend(fn())
        except Exception as exc:
            logger.warning("health check %s failed: %s", label, exc, exc_info=True)
            findings.append(_unknown(label.replace(" ", "_"), label, f"{type(exc).__name__}: {exc}"))
    return findings
