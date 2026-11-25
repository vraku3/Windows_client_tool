"""Computed System Health findings that read the live machine. No Qt, no writes.

Every check returns a list of `Finding`s. A read that fails or is refused is
reported as an "unknown" finding of its own (`id` ends in `:unknown`), never
as silence -- silence would read as "healthy".
"""
from core.system_paths import powershell_exe
import ctypes
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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
    rc, out = _run([powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script], timeout)
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
        return [_unknown("time_sync", "time synchronisation", text, jump="Quick Fix")]
    if rc != 0 or "service has not been started" in lowered or "0x80070426" in lowered:
        return [Finding(
            id="time_sync", title="The Windows Time service is not answering",
            detail="Clock drift breaks Kerberos, TLS and log correlation. Start the Windows Time "
                   "service (W32Time), then run w32tm /resync.",
            severity="warning", evidence=text[:300], jump="Quick Fix")]
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
        jump="Quick Fix")]


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


def evaluate_storage_events(groups: Sequence[Any]) -> List[Finding]:
    """Classify grouped disk/storage events into actionable findings.

    Real-machine evidence: Disk 5 and Disk 6 repeatedly log Event 158
    ("The disk has duplicate disk identifiers") on this PC -- completely invisible
    to Get-PhysicalDisk which continues reporting 'Healthy'.
    """
    out: List[Finding] = []
    for g in groups:
        event_id = getattr(g, "event_id", None)
        if event_id is None and isinstance(g, dict):
            event_id = g.get("event_id")
        disk = getattr(g, "disk", None)
        if disk is None and isinstance(g, dict):
            disk = g.get("disk")
        count = getattr(g, "count", 1)
        if isinstance(g, dict):
            count = g.get("count", 1)
        latest = getattr(g, "latest", "")
        if isinstance(g, dict):
            latest = g.get("latest", "")
        meaning = getattr(g, "meaning", "")
        if isinstance(g, dict):
            meaning = g.get("meaning", "")
        is_error = getattr(g, "is_error", False)
        if isinstance(g, dict):
            is_error = g.get("is_error", False)

        if event_id in (98, 130):
            continue

        disk_label = f"Disk {disk}" if disk else "Storage subsystem"
        severity = "danger" if (is_error or event_id in (50, 55, 144, 154)) else "warning"

        if event_id in (58, 158):
            title = f"{disk_label} has duplicate disk identifier collision (Event {event_id})"
            detail = (
                "Windows detected the same disk identifier on more than one disk (KB2983588). "
                "This typically happens after cloning a disk or attaching duplicate VHD/USB drives. "
                "The disk may be marked offline or become unaddressable by VSS."
            )
        elif event_id == 154:
            title = f"{disk_label} hardware I/O error (Event 154)"
            detail = (
                "An I/O operation failed due to a hardware error. This indicates a failing drive, "
                "faulty cable, or controller malfunction. Back up critical data immediately."
            )
        elif event_id in (55, 144):
            title = f"{disk_label} file system corruption (Event {event_id})"
            detail = (
                "The file system structure is corrupted and unusable. Run chkdsk to repair."
            )
        elif event_id == 50:
            title = f"{disk_label} delayed-write data loss (Event 50)"
            detail = (
                "Windows failed to write cached data from memory to disk. Data loss has occurred. "
                "Check disk connectivity and controller stability."
            )
        elif event_id == 7:
            title = f"{disk_label} bad block remapped (Event 7)"
            detail = (
                "The drive detected a bad block and remapped a sector. Drive degradation is occurring."
            )
        elif event_id in (9, 11, 129):
            title = f"{disk_label} controller timeout / reset (Event {event_id})"
            detail = (
                "The storage adapter or device timed out and was reset. "
                "Check storage drivers, NVMe/SATA cables, and power delivery."
            )
        elif event_id == 157:
            title = f"{disk_label} surprise removal (Event 157)"
            detail = (
                "The disk was disconnected or surprise-removed without warning."
            )
        else:
            title = f"{disk_label} error (Event {event_id})"
            detail = meaning or "A storage error was recorded in the System log."

        evidence = f"{count} event(s) in last 14 days, latest at {latest}" if latest else f"{count} event(s)"
        finding_id = f"storage_event:{event_id}:{disk or 'all'}"
        out.append(Finding(
            id=finding_id,
            title=title,
            detail=detail,
            severity=severity,
            evidence=evidence,
            jump="Disk Health",
        ))
    return out


def check_storage_events() -> List[Finding]:
    try:
        from modules.disk_health import disk_events
        events = disk_events.read_disk_events(days=14)
    except Exception as exc:
        logger.warning("Could not read storage events: %s", exc)
        return [_unknown("storage_events", "storage and disk event history", str(exc), jump="Disk Health")]
    if events is None:
        return [_unknown("storage_events", "storage and disk event history",
                         "Failed to read disk events from System log", jump="Disk Health")]
    groups = disk_events.group_events(events)
    return evaluate_storage_events(groups)


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
            severity="warning", evidence=out[:300], jump="Quick Fix")]
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

_CBS_HINTS = re.compile(
    r"(\[SR\] Cannot repair member file|CSI Payload Corrupt|Cannot repair.*store|"
    r"Component store is repairable|STATUS_SXS_COMPONENT_STORE_CORRUPT)",
    re.I,
)
_CBS_CLEAN_REPAIR = re.compile(
    r"(\[SR\] Verify and Repair Transaction completed|"
    r"The restore operation completed successfully|"
    r"Component store was repaired|"
    r"Verification 100% complete\. Result: No integrity violations were found)",
    re.I,
)


def scan_cbs_text(text: str) -> List[str]:
    return [line.strip()[:200] for line in text.splitlines() if _CBS_HINTS.search(line)]


def evaluate_cbs_text(text: str, tail_bytes_mb: int = 8, path: str = "") -> List[Finding]:
    lines = text.splitlines()
    corrupt_indices: List[Tuple[int, str]] = []
    repair_indices: List[Tuple[int, str]] = []

    for idx, line in enumerate(lines):
        clean = line.strip()[:200]
        if _CBS_HINTS.search(clean):
            corrupt_indices.append((idx, clean))
        elif _CBS_CLEAN_REPAIR.search(clean):
            repair_indices.append((idx, clean))

    if not corrupt_indices:
        return []

    last_corrupt_idx, last_corrupt_line = corrupt_indices[-1]
    last_repair_idx = repair_indices[-1][0] if repair_indices else -1

    evidence_suffix = f"\n(last {tail_bytes_mb} MB of {path})" if path else ""

    if last_repair_idx > last_corrupt_idx:
        recent_repair = repair_indices[-1][1]
        return [Finding(
            id="cbs_resolved",
            title="CBS log: component-store repairs succeeded",
            detail="Component store corruptions were previously logged, but subsequent servicing operations "
                   "reported clean verification or repair.",
            severity="info",
            evidence=f"Last corruption: {last_corrupt_line}\nSubsequent repair: {recent_repair}" + evidence_suffix,
            jump="Diagnose",
        )]

    unresolved = [line for idx, line in corrupt_indices if idx > last_repair_idx]
    return [Finding(
        id="cbs_corruption",
        title=f"CBS log mentions unresolved component-store corruption ({len(unresolved)} line(s))",
        detail="Lines like these are what sfc/DISM write when a system file could not be repaired. "
               "Run DISM RestoreHealth, then sfc /scannow (Servicing tab).",
        severity="warning",
        evidence="\n".join(unresolved[-5:]) + evidence_suffix,
        jump="Diagnose",
    )]


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
    return evaluate_cbs_text(text, tail_bytes // (1024 * 1024), path)


# ---- services --------------------------------------------------------------------

def evaluate_services(
    rows: List[Dict],
    failures: Optional[Dict[str, int]],
    blocked_deps: Optional[List[Tuple[str, List[str]]]] = None,
    missing_binaries: Optional[List[Tuple[str, str]]] = None,
) -> List[Finding]:
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

    if blocked_deps:
        evidence = "\n".join(f"• {svc}: depends on disabled service(s) [{', '.join(deps)}]" for svc, deps in blocked_deps[:10])
        if len(blocked_deps) > 10:
            evidence += f"\n... and {len(blocked_deps) - 10} more"
        out.append(Finding(
            id="services:blocked_dependencies",
            title=f"{len(blocked_deps)} service(s) cannot start due to disabled dependencies",
            detail="Windows Service Control Manager will refuse to start a service when any of its required dependency services is set to Disabled (ERROR_SERVICE_DEPENDENCY_DELETED or ERROR_SERVICE_DEPENDENCY_FAIL).",
            severity="warning",
            evidence=evidence,
            jump="Services",
        ))

    if missing_binaries:
        evidence = "\n".join(f"• {svc}: missing file {path}" for svc, path in missing_binaries[:10])
        if len(missing_binaries) > 10:
            evidence += f"\n... and {len(missing_binaries) - 10} more"
        out.append(Finding(
            id="services:missing_binary",
            title=f"{len(missing_binaries)} service(s) point to non-existent executables",
            detail="The service is registered in Windows to start automatically or manually, but its ImagePath executable file was not found on disk.",
            severity="warning",
            evidence=evidence,
            jump="Services",
        ))

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
    try:
        blocked, missing = service_audit.audit_service_registry()
    except Exception as exc:
        logger.debug("Failed auditing service registry: %s", exc)
        blocked, missing = [], []
    return evaluate_services(rows, service_audit.read_failure_counts(), blocked, missing)


# ---- problem devices (yellow bangs) ---------------------------------------------

_PROBLEM_CODE_DESCRIPTIONS: Dict[int, str] = {
    1: "Device not configured correctly",
    3: "Driver may be corrupted",
    10: "Device cannot start",
    14: "Reboot required to function",
    18: "Reinstall drivers for this device",
    19: "Configuration information incomplete or damaged",
    21: "Device is being removed",
    22: "Device is disabled",
    24: "Device not present or drivers not installed",
    28: "Drivers for this device are not installed",
    31: "Device not working: Windows cannot load driver",
    32: "Driver for this device was disabled",
    37: "Driver initialization failed",
    39: "Driver corrupted or missing",
    43: "Stopped because it reported problems",
}


def evaluate_problem_devices(rows: List[Dict[str, Any]]) -> List[Finding]:
    """Evaluate Win32_PnPEntity devices reporting ConfigManagerErrorCode != 0."""
    valid_rows = [r for r in rows if isinstance(r, dict) and (r.get("ConfigManagerErrorCode") or 0) != 0]
    if not valid_rows:
        return []

    lines = []
    all_disabled = True
    for r in valid_rows:
        code = int(r.get("ConfigManagerErrorCode") or 0)
        name = r.get("Name") or r.get("DeviceID") or "Unknown device"
        desc = _PROBLEM_CODE_DESCRIPTIONS.get(code, f"Error code {code}")
        lines.append(f"- {name}: Code {code} ({desc})")
        if code != 22:
            all_disabled = False

    severity = "info" if all_disabled else "warning"
    count = len(valid_rows)
    if count == 1:
        single_name = valid_rows[0].get("Name") or valid_rows[0].get("DeviceID") or "device"
        single_code = valid_rows[0].get("ConfigManagerErrorCode")
        title = f"Problem device: {single_name} (Code {single_code})"
    else:
        title = f"{count} devices reporting hardware/driver errors"

    evidence = "\n".join(lines[:15])
    if len(lines) > 15:
        evidence += f"\n... and {len(lines) - 15} more device(s)"

    detail = (
        "Windows Device Manager reports error codes for these devices. They may fail to "
        "function, crash on use, or lack necessary drivers. View and troubleshoot in Driver Manager."
    )
    if all_disabled:
        detail = "These devices are currently disabled by the user or Windows policy."

    return [Finding(
        id="problem_devices",
        title=title,
        detail=detail,
        severity=severity,
        evidence=evidence,
        jump="Driver Manager",
    )]


def check_problem_devices() -> List[Finding]:
    """Query Win32_PnPEntity for devices with ConfigManagerErrorCode != 0."""
    rows, why = _ps_json(
        "Get-CimInstance Win32_PnPEntity -Filter 'ConfigManagerErrorCode != 0' "
        "| Select-Object Name, DeviceID, ConfigManagerErrorCode | ConvertTo-Json -Compress",
        30,
    )
    if rows is None:
        return [_unknown("problem_devices", "problem devices", why or "", jump="Driver Manager")]
    return evaluate_problem_devices(rows)


# ---- certificates ----------------------------------------------------------------

def evaluate_certificates(rows: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Finding]:
    """Evaluate Personal (MY) user and computer certificates for expiration or near-expiry (<14 days)."""
    out: List[Finding] = []
    if now is None:
        now = datetime.now()

    for r in rows:
        if not isinstance(r, dict):
            continue
        subject = str(r.get("Subject") or r.get("Thumbprint") or "Unknown certificate").strip()
        store = str(r.get("Store") or "Personal").strip()
        not_after_str = str(r.get("NotAfter") or "").strip()
        if not not_after_str:
            continue
        clean_str = not_after_str.split(".")[0].rstrip("Z")
        expiry = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                expiry = datetime.strptime(clean_str, fmt)
                break
            except ValueError as exc:
                logger.debug("Certificate date format mismatch for %s (%s): %s", clean_str, fmt, exc)
        if expiry is None:
            continue

        days_left = (expiry - now).total_seconds() / 86400.0
        thumb = str(r.get("Thumbprint") or "")
        short_sub = subject[:50] + ("..." if len(subject) > 50 else "")
        evidence = f"Subject: {subject}\nStore: {store}\nExpiry: {expiry.strftime('%Y-%m-%d %H:%M')}\nThumbprint: {thumb}"

        if days_left < 0:
            days_ago = int(abs(days_left))
            out.append(Finding(
                id=f"cert_expired_{thumb[:12].lower() or 'unknown'}",
                title=f"Expired certificate in {store}: {short_sub}",
                detail=f"This certificate expired {days_ago} day(s) ago. Logins, VPN, or network auth relying on it will fail.",
                severity="warning",
                evidence=evidence,
                jump="Certificates",
            ))
        elif days_left < 14:
            days_int = max(0, int(days_left))
            out.append(Finding(
                id=f"cert_expiring_{thumb[:12].lower() or 'unknown'}",
                title=f"Certificate expiring in {days_int} day(s): {short_sub}",
                detail=f"This certificate in {store} expires on {expiry.strftime('%Y-%m-%d')}. Renew before it lapses.",
                severity="warning",
                evidence=evidence,
                jump="Certificates",
            ))

    return out


def check_certificates() -> List[Finding]:
    """Check Personal (My) certificates in LocalMachine and CurrentUser for expiry."""
    script = (
        "$certs = @(Get-ChildItem Cert:\\LocalMachine\\My, Cert:\\CurrentUser\\My -ErrorAction SilentlyContinue); "
        "if (-not $certs) { '[]'; exit 0 }; "
        "@($certs | Select-Object @{n='Subject';e={$_.Subject}}, @{n='Thumbprint';e={$_.Thumbprint}}, "
        "@{n='NotAfter';e={$_.NotAfter.ToString('yyyy-MM-ddTHH:mm:ss')}}, "
        "@{n='Store';e={$_.PSParentPath.Split('::')[-1]}}) | ConvertTo-Json -Compress"
    )
    rows, why = _ps_json(script, 30)
    if rows is None:
        return [_unknown("certificates", "personal certificates", why or "", jump="Certificates")]
    return evaluate_certificates(rows)


# ---- Windows Update failures -----------------------------------------------------

_WU_FAIL_RE = re.compile(
    r"following update(?: with error (0x[0-9a-fA-F]+))?:\s*(.+?)\.?$", re.IGNORECASE
)
_KB_RE = re.compile(r"\bKB\d{6,8}\b", re.IGNORECASE)
_STORE_APP_PREFIX = re.compile(r"^9[A-Z0-9]{11}-")
_HRESULT_RE = re.compile(r"\b0[xX]([0-9a-fA-F]{8})\b")


def _decode_wu_err(err_str: str) -> str:
    if not err_str:
        return ""
    try:
        from core.wu_error_codes import decode_wu_error
        val = int(err_str, 16)
        text = decode_wu_error(val)
        if " - " in text:
            return text.split(" - ", 1)[1]
        return text
    except (ValueError, TypeError):
        return err_str


def evaluate_windows_update_failures(rows: List[Dict[str, Any]]) -> List[Finding]:
    """Evaluate System log event 20 records from Microsoft-Windows-WindowsUpdateClient."""
    out: List[Finding] = []
    os_failures: Dict[str, List[Tuple[str, str, str]]] = {}
    store_failures: List[Tuple[str, str, str, str]] = []

    for r in rows:
        msg = str(r.get("Message") or "").strip()
        if not msg:
            continue
        time_str = str(r.get("Time") or r.get("TimeCreated") or "").strip()
        m = _WU_FAIL_RE.search(msg)
        if m:
            err_str = m.group(1) or ""
            target = m.group(2).strip()
        else:
            code_m = _HRESULT_RE.search(msg)
            err_str = code_m.group(0) if code_m else ""
            target = msg

        expl = _decode_wu_err(err_str)
        if _STORE_APP_PREFIX.match(target):
            store_failures.append((time_str, target, err_str, expl))
        else:
            os_failures.setdefault(target, []).append((time_str, err_str, expl))

    for target, recs in os_failures.items():
        count = len(recs)
        latest_time, latest_err, latest_expl = recs[0]
        kb_m = _KB_RE.search(target)
        kb_tag = f"_{kb_m.group(0).lower()}" if kb_m else f"_{re.sub(r'[^a-zA-Z0-9_]', '_', target).lower()[:24]}"
        err_hint = f" {latest_err}" if latest_err else ""
        if latest_expl:
            err_hint += f" ({latest_expl})"

        title = (
            f"Windows Update failed {count} time(s) installing {target[:55]}"
            if count > 1 else f"Windows Update failed installing {target[:60]}"
        )
        detail = (
            f"Windows Update failed to install {target}. Latest error:{err_hint}. "
            "A servicing transaction conflict, corrupt component store, or cached download may cause this. "
            "Resetting Windows Update components in Quick Fix or running DISM ScanHealth can repair it."
        )
        lines = [f"- {t}: error {e} ({x})" if x else f"- {t}: error {e}" for t, e, x in recs]
        out.append(Finding(
            id=f"wu_failed{kb_tag}",
            title=title,
            detail=detail,
            severity="warning",
            evidence="\n".join(lines[:10]),
            jump="Updates",
        ))

    if store_failures:
        distinct = sorted({r[1] for r in store_failures})
        apps = [a.split("-", 1)[-1] for a in distinct]
        app_summary = ", ".join(apps[:3])
        if len(apps) > 3:
            app_summary += f" and {len(apps) - 3} more"
        lines = [f"- {t}: {a.split('-', 1)[-1]} (error {e})" for t, a, e, _ in store_failures]
        out.append(Finding(
            id="wu_failed_store_apps",
            title=f"{len(distinct)} Store package update(s) failed in the background",
            detail=f"Background package updates failed for {app_summary}. This frequently occurs when packages are in use during update.",
            severity="info",
            evidence="\n".join(lines[:10]),
            jump="Updates",
        ))

    return out


def check_windows_update_failures(days: int = 14) -> List[Finding]:
    """Check the System log for WindowsUpdateClient installation failures (event 20)."""
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$f = @{{LogName='System'; ProviderName='Microsoft-Windows-WindowsUpdateClient'; Id=20; "
        f"StartTime=(Get-Date).AddDays(-{int(days)})}}; "
        "try { $events = Get-WinEvent -FilterHashtable $f -MaxEvents 50 } "
        "catch { if ($_.FullyQualifiedErrorId -like '*NoMatchingEventsFound*') { '[]'; exit 0 } else { throw } }; "
        "@($events | Select-Object Id, "
        "@{n='Time';e={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')}}, "
        "Message) | ConvertTo-Json -Compress"
    )
    rows, why = _ps_json(script, 30)
    if rows is None:
        return [_unknown("windows_update_failures", "Windows Update installation history", why or "", jump="Updates")]
    return evaluate_windows_update_failures(rows)


# ---- Driver load failures --------------------------------------------------------

def evaluate_driver_load_failures(rows: List[Dict[str, Any]]) -> List[Finding]:
    """Evaluate System log event 219 records from Microsoft-Windows-Kernel-PnP."""
    from modules.driver_manager.driver_diagnostics import parse_driver_load_failures
    failures = parse_driver_load_failures(rows)
    if not failures:
        return []

    wudfrd_start = None
    if any("wudfrd" in f.driver.lower() for f in failures):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\WudfRd") as k:
                wudfrd_start, _ = winreg.QueryValueEx(k, "Start")
        except OSError as exc:
            logger.debug("WudfRd service key unreadable: %s", exc)

    out: List[Finding] = []
    for f in failures:
        clean_driver = f.driver.lstrip("\\").replace("Driver\\", "")
        short_dev = f.device_id.split("\\", 1)[-1] if "\\" in f.device_id else f.device_id
        if len(short_dev) > 40:
            short_dev = short_dev[:37] + "..."

        remedy_hint = ""
        if "wudfrd" in clean_driver.lower() and "365" in f.status:
            if wudfrd_start == 3:
                remedy_hint = (
                    " The WudfRd service (User-mode Driver Framework Reflector) is set to Manual (3) "
                    "startup: switching it to Automatic (2) prevents devices from timing out at early boot."
                )
            else:
                remedy_hint = (
                    " The User-mode Driver Framework Reflector (WudfRd) failed to initialize in time "
                    "for this device at early boot."
                )

        title = f"Driver {clean_driver} failed to load for {short_dev} ({f.count} time(s))"
        detail = (
            f"Kernel-PnP logged event 219 indicating Windows failed to load {f.driver} for device "
            f"'{f.device_id}'. Latest status: {f.status} ({f.status_meaning}).{remedy_hint}"
        )
        safe_dev = re.sub(r"[^a-zA-Z0-9_]", "_", short_dev).lower()[:24]
        safe_drv = re.sub(r"[^a-zA-Z0-9_]", "_", clean_driver).lower()[:16]
        evidence_lines = [f"- {t}: Status {f.status} ({f.status_meaning})" for t in f.sample_times]
        if wudfrd_start is not None and "wudfrd" in clean_driver.lower():
            evidence_lines.append(f"WudfRd service Start={wudfrd_start}")

        out.append(Finding(
            id=f"driver_load_failed_{safe_drv}_{safe_dev}",
            title=title,
            detail=detail,
            severity="warning",
            evidence="\n".join(evidence_lines),
            jump="Driver Manager",
        ))

    return out


def check_driver_load_failures(days: int = 14) -> List[Finding]:
    """Check the System log for Kernel-PnP driver load failures (event 219)."""
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$f = @{{LogName='System'; ProviderName='Microsoft-Windows-Kernel-PnP'; Id=219; "
        f"StartTime=(Get-Date).AddDays(-{int(days)})}}; "
        "try { $events = Get-WinEvent -FilterHashtable $f -MaxEvents 50 } "
        "catch { if ($_.FullyQualifiedErrorId -like '*NoMatchingEventsFound*') { '[]'; exit 0 } else { throw } }; "
        "@($events | Select-Object Id, "
        "@{n='Time';e={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')}}, "
        "Message) | ConvertTo-Json -Compress"
    )
    rows, why = _ps_json(script, 30)
    if rows is None:
        return [_unknown("driver_load_failures", "driver load failure events", why or "", jump="Driver Manager")]
    return evaluate_driver_load_failures(rows)


# ---- TPM attestation events ------------------------------------------------------

def evaluate_tpm_attestation_events(rows: List[Dict[str, Any]]) -> List[Finding]:
    """Evaluate System log event 1040 from Microsoft-Windows-TPM-WMI."""
    if not rows:
        return []
    count = len(rows)
    evidence_lines = [
        f"- {r.get('Time') or r.get('TimeCreated')}: Event 1040 (Pre-attestation health check failed)"
        for r in rows[:5]
    ]
    return [Finding(
        id="tpm_wmi_attestation_failed",
        title=f"TPM pre-attestation check failed ({count} time(s))",
        detail=(
            "Microsoft-Windows-TPM-WMI recorded event 1040 indicating that pre-attestation health "
            "checks failed on this device. Device health attestation (Intune, conditional access) "
            "or BitLocker attestation may fail."
        ),
        severity="warning",
        evidence="\n".join(evidence_lines),
        jump="Security Dashboard",
    )]


def check_tpm_attestation_events(days: int = 14) -> List[Finding]:
    """Check the System log for Microsoft-Windows-TPM-WMI event 1040."""
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$f = @{{LogName='System'; ProviderName='Microsoft-Windows-TPM-WMI'; Id=1040; "
        f"StartTime=(Get-Date).AddDays(-{int(days)})}}; "
        "try { $events = Get-WinEvent -FilterHashtable $f -MaxEvents 50 } "
        "catch { if ($_.FullyQualifiedErrorId -like '*NoMatchingEventsFound*') { '[]'; exit 0 } else { throw } }; "
        "@($events | Select-Object Id, "
        "@{n='Time';e={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')}}, "
        "Message) | ConvertTo-Json -Compress"
    )
    rows, why = _ps_json(script, 30)
    if rows is None:
        return [_unknown("tpm_attestation_events", "TPM attestation events", why or "", jump="Security Dashboard")]
    return evaluate_tpm_attestation_events(rows)


# ---- Firewall Exposure -----------------------------------------------------------

def evaluate_firewall_exposure(snapshot: Any) -> List[Finding]:
    """Turn a firewall_audit Snapshot into actionable System Health findings.

    Only flags genuine exposure:
    - Profile disabled (severity="warning")
    - Profile allows inbound by default (severity="warning")
    - Inbound allow rules with no program, service, or port limit on Public profile (warning)
    - Enabled rules pointing to deleted/missing executables (severity="info")
    """
    if getattr(snapshot, "error", ""):
        return [_unknown("firewall_exposure", "firewall policy", snapshot.error, jump="Firewall Rules")]

    out: List[Finding] = []
    for f in getattr(snapshot, "findings", []):
        if f.key == "profile_off":
            out.append(Finding(
                id=f"firewall:{f.key}",
                title=f.title,
                detail=f.detail + (" " + f.fix if getattr(f, "fix", "") else ""),
                severity="warning",
                evidence="\n".join(f.rule_names) if f.rule_names else f.title,
                jump="Firewall Rules",
            ))
        elif f.key == "default_allow":
            out.append(Finding(
                id=f"firewall:{f.key}",
                title=f.title,
                detail=f.detail,
                severity="warning",
                evidence=f.title,
                jump="Firewall Rules",
            ))
        elif f.key == "open_public":
            out.append(Finding(
                id=f"firewall:{f.key}",
                title=f.title,
                detail=f.detail,
                severity="warning",
                evidence="\n".join(f.rule_names),
                jump="Firewall Rules",
            ))
        elif f.key == "prog_missing":
            out.append(Finding(
                id=f"firewall:{f.key}",
                title=f.title,
                detail=f.detail,
                severity="info",
                evidence="\n".join(f.rule_names),
                jump="Firewall Rules",
            ))
    return out


def check_firewall_exposure() -> List[Finding]:
    """Audit Windows Firewall profiles and rule exposure via registry."""
    from modules.firewall_rules.firewall_audit import take_snapshot
    try:
        snap = take_snapshot()
    except Exception as exc:
        logger.warning("firewall snapshot failed: %s", exc)
        return [_unknown("firewall_exposure", "firewall policy", str(exc), jump="Firewall Rules")]
    return evaluate_firewall_exposure(snap)


# ---- Scheduled task failures -----------------------------------------------------

def evaluate_scheduled_task_failures(tasks: List[Any]) -> List[Finding]:
    """Turn failed scheduled task instances into actionable findings."""
    from modules.scheduled_tasks.task_view import failed, describe_result, is_microsoft

    failed_tasks = [t for t in tasks if getattr(t, "enabled", False) and failed(getattr(t, "last_result_code", None))]
    if not failed_tasks:
        return []

    third_party = [t for t in failed_tasks if not is_microsoft(t)]
    windows = [t for t in failed_tasks if is_microsoft(t)]

    out: List[Finding] = []
    if third_party:
        lines = [f"{t.path}: {describe_result(t.last_result_code)}" for t in third_party]
        out.append(Finding(
            id="scheduled_tasks:failed_third_party",
            title=f"{len(third_party)} third-party scheduled task(s) failed on last run",
            detail="Enabled third-party tasks reported an abnormal exit code or error.",
            severity="warning",
            evidence="\n".join(lines[:10]),
            jump="Scheduled Tasks",
        ))

    if windows:
        lines = [f"{t.path}: {describe_result(t.last_result_code)}" for t in windows]
        out.append(Finding(
            id="scheduled_tasks:failed_windows",
            title=f"{len(windows)} Windows scheduled task(s) failed on last run",
            detail="Enabled Windows background tasks reported a non-zero exit code or error.",
            severity="info",
            evidence="\n".join(lines[:10]),
            jump="Scheduled Tasks",
        ))

    return out


def check_scheduled_task_failures() -> List[Finding]:
    """Audit enabled scheduled tasks for last run execution failures."""
    try:
        import ctypes
        from modules.scheduled_tasks.tasks_reader import get_tasks_in_folder, ALL_FOLDERS
        hr = ctypes.windll.ole32.CoInitialize(None)
        try:
            tasks = get_tasks_in_folder(ALL_FOLDERS)
        finally:
            if hr >= 0:
                ctypes.windll.ole32.CoUninitialize()
    except Exception as exc:
        logger.warning("scheduled task failure check failed: %s", exc)
        return [_unknown("scheduled_tasks_failure", "scheduled tasks execution status", str(exc), jump="Scheduled Tasks")]

    return evaluate_scheduled_task_failures(tasks)


# ---- Local accounts & security policy --------------------------------------------

def evaluate_local_accounts(account_findings: List[Any]) -> List[Finding]:
    """Turn accounts.findings into System Health findings."""
    out: List[Finding] = []
    for f in account_findings:
        sev = "warning" if getattr(f, "severity", "") == "warning" else "info"
        f_id = f"local_accounts:{getattr(f, 'account', 'policy')}".replace(" ", "_").lower()
        msg = getattr(f, "message", "")
        acct = getattr(f, "account", "")
        short_title = msg if len(msg) <= 70 else (msg[:67] + "...")
        out.append(Finding(
            id=f_id,
            title=f"{acct}: {short_title}" if acct else short_title,
            detail=msg,
            severity=sev,
            evidence=f"{acct}\n{msg}",
            jump="Local Users & Groups",
        ))
    return out


def check_local_accounts() -> List[Finding]:
    """Audit local accounts, administrators group, and local password policy."""
    try:
        from modules.local_users import accounts
        snap = accounts.read_snapshot()
        f_list = accounts.findings(snap)
    except Exception as exc:
        logger.warning("local accounts check failed: %s", exc)
        return [_unknown("local_accounts", "local accounts policy", str(exc), jump="Local Users & Groups")]
    return evaluate_local_accounts(f_list)


# ---- PATH environment ------------------------------------------------------------

def evaluate_path_environment(missing: List[str], writable: List[str]) -> List[Finding]:
    """Turn missing PATH entries and writable folders into actionable findings."""
    out: List[Finding] = []
    if writable:
        out.append(Finding(
            id="path:writable",
            title=f"{len(writable)} user-writable directory(ies) in System PATH",
            detail="Standard users have write permissions to a directory in System PATH, presenting a binary planting risk (CWE-426).",
            severity="warning",
            evidence="\n".join(writable),
            jump="Environment Variables",
        ))
    if missing:
        out.append(Finding(
            id="path:missing",
            title=f"{len(missing)} missing directory(ies) in PATH environment",
            detail="Directories listed in PATH do not exist on disk, causing wasted disk lookups during command resolution.",
            severity="info",
            evidence="\n".join(missing),
            jump="Environment Variables",
        ))
    return out


def check_path_environment() -> List[Finding]:
    """Audit System and User PATH for non-existent directories and writable planting risks."""
    import winreg
    from modules.env_vars import path_analysis

    missing: List[str] = []
    writable: List[str] = []
    for is_sys, hive, path_key in (
        (True, winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (False, winreg.HKEY_CURRENT_USER, r"Environment"),
    ):
        try:
            with winreg.OpenKey(hive, path_key) as k:
                val, _ = winreg.QueryValueEx(k, "Path")
            rep = path_analysis.analyse(val, is_system=is_sys)
            for f in rep.findings:
                if f.kind == "missing":
                    missing.append(f.message)
                elif f.kind == "writable":
                    writable.append(f.message)
        except FileNotFoundError:
            logger.debug("PATH registry key not found: %s", path_key)
        except OSError as exc:
            logger.debug("Could not read PATH key %s: %s", path_key, exc)

    return evaluate_path_environment(missing, writable)


# ---- Software inventory health ---------------------------------------------------

def evaluate_software_health(software_findings: List[Any]) -> List[Finding]:
    """Turn software_analysis.software_findings into System Health findings."""
    out: List[Finding] = []
    for f in software_findings:
        if getattr(f, "severity", "") == "warning":
            f_id = "software:" + ("broken_uninstaller" if "uninstall" in getattr(f, "title", "").lower() else "eol")
            out.append(Finding(
                id=f_id,
                title=getattr(f, "title", ""),
                detail=getattr(f, "detail", ""),
                severity="warning",
                evidence=getattr(f, "detail", ""),
                jump="Software Inventory",
            ))
    return out


def check_software_health() -> List[Finding]:
    """Audit installed software for broken uninstallers and EOL packages."""
    try:
        from modules.software_inventory import software_reader as sr
        from modules.software_inventory import software_analysis as sa
        entries = sr.fetch_software_all()
        rows = sa.analyze(entries)
        f_list = sa.software_findings(rows)
    except Exception as exc:
        logger.warning("software health check failed: %s", exc)
        return [_unknown("software_health", "installed software inventory", str(exc), jump="Software Inventory")]
    return evaluate_software_health(f_list)


# ---- Firmware & Secure Boot ------------------------------------------------------

def evaluate_firmware_security(mode: Optional[str], secure_boot: Optional[bool], sb_reason: str) -> List[Finding]:
    """Turn firmware mode and Secure Boot state into actionable health findings."""
    out: List[Finding] = []
    if mode == "Legacy BIOS":
        out.append(Finding(
            id="firmware:legacy_bios",
            title="Machine boots in Legacy BIOS mode instead of UEFI",
            detail="Windows 11 and modern platform security features (Secure Boot, TPM 2.0 measurement, Credential Guard) require UEFI boot mode.",
            severity="warning",
            evidence="Firmware mode: Legacy BIOS",
            jump="Hardware Info",
        ))
    if secure_boot is False:
        out.append(Finding(
            id="firmware:secure_boot_disabled",
            title="Secure Boot is disabled in firmware",
            detail="Secure Boot is disabled. The system is vulnerable to bootkits and unsigned bootloaders executing before Windows loads.",
            severity="warning",
            evidence=f"Secure Boot: False\n{sb_reason or 'UEFISecureBootEnabled=0'}",
            jump="Security Dashboard",
        ))
    elif secure_boot is None:
        out.append(_unknown("firmware_secure_boot", "Secure Boot state", sb_reason or "Read was refused", jump="Security Dashboard"))
    return out


def check_firmware_security() -> List[Finding]:
    """Audit firmware boot mode (UEFI vs BIOS) and Secure Boot status via winreg/kernel32."""
    from modules.hardware_inventory import asset_reader as ar
    try:
        mode = ar._read_firmware_mode()
        sb, reason = ar._read_secure_boot(mode)
    except Exception as exc:
        logger.warning("firmware security check failed: %s", exc)
        return [_unknown("firmware_security", "firmware security state", str(exc), jump="Security Dashboard")]
    return evaluate_firmware_security(mode, sb, reason)


# ---- Crash Dumps & BugCheck History -----------------------------------------------

def evaluate_crash_history(
    dump_enabled: Optional[int],
    bsod_incidents: List[dict],
    whea_incidents: List[dict],
    live_incidents: List[dict],
    power_cut_count: int = 0,
    days: int = 30,
) -> List[Finding]:
    """Turn crash dump policy and correlated incident history into actionable health findings."""
    out: List[Finding] = []
    if dump_enabled == 0:
        out.append(Finding(
            id="crash:dumps_disabled",
            title="Crash dump generation is disabled in Windows",
            detail=(
                "CrashDumpEnabled is set to 0 in HKLM\\SYSTEM\\CurrentControlSet\\Control\\CrashControl. "
                "If the machine experiences a blue screen (BSOD), no diagnostic dump file will be created "
                "to identify the root cause."
            ),
            severity="warning",
            evidence="HKLM\\SYSTEM\\CurrentControlSet\\Control\\CrashControl\\CrashDumpEnabled = 0",
            jump="Diagnose",
        ))

    if bsod_incidents:
        n = len(bsod_incidents)
        lines = []
        for inc in bsod_incidents[:5]:
            ts = inc.get("timestamp", "")
            lbl = inc.get("bugcheck_label") or "BugCheck"
            code = inc.get("bugcheck_code")
            code_str = f" (0x{code:X})" if isinstance(code, int) else (f" ({code})" if code else "")
            mod = inc.get("faulting_module")
            mod_str = f" [module: {mod}]" if mod else ""
            lines.append(f"• {ts}: {lbl}{code_str}{mod_str}")
        if n > 5:
            lines.append(f"... and {n - 5} more crash event{'s' if n - 5 > 1 else ''}")
        out.append(Finding(
            id="crash:recent_bsod",
            title=f"{n} system crash{'es' if n > 1 else ''} (BSOD) detected in the last {days} days",
            detail=(
                "Windows recorded unhandled kernel bugcheck(s) or abnormal crash restarts. "
                "Examine the dump files and bugcheck parameters to identify the failing driver or hardware subsystem."
            ),
            severity="error",
            evidence="\n".join(lines),
            jump="Diagnose",
        ))

    if whea_incidents:
        n = len(whea_incidents)
        lines = []
        for inc in whea_incidents[:5]:
            ts = inc.get("timestamp", "")
            title = inc.get("title") or "WHEA Hardware Error"
            apic = inc.get("apic_id")
            apic_str = f" [APIC ID {apic}]" if apic is not None else ""
            lines.append(f"• {ts}: {title}{apic_str}")
        if n > 5:
            lines.append(f"... and {n - 5} more hardware error{'s' if n - 5 > 1 else ''}")
        out.append(Finding(
            id="crash:hardware_whea",
            title=f"{n} hardware error (WHEA) event{'s' if n > 1 else ''} in the last {days} days",
            detail=(
                "Windows Hardware Error Architecture (WHEA) recorded hardware errors from CPU cores, cache, memory, or PCIe bus. "
                "Clustered errors often point to unstable core voltages, PCIe link degradation, or failing RAM."
            ),
            severity="warning",
            evidence="\n".join(lines),
            jump="Diagnose",
        ))

    if live_incidents:
        n = len(live_incidents)
        lines = [f"• {inc.get('timestamp', '')}: {inc.get('title', 'Watchdog timeout')}" for inc in live_incidents[:5]]
        if n > 5:
            lines.append(f"... and {n - 5} more live dump{'s' if n - 5 > 1 else ''}")
        out.append(Finding(
            id="crash:live_kernel_dumps",
            title=f"{n} live kernel watchdog/timeout event{'s' if n > 1 else ''} in the last {days} days",
            detail=(
                "Windows captured live kernel dumps while running because a driver subsystem (e.g. graphics driver TDR, USB, Wi-Fi) "
                "reported an unresponsive timeout. The system recovered without a blue screen, but repeated events indicate driver instability."
            ),
            severity="warning",
            evidence="\n".join(lines),
            jump="Diagnose",
        ))

    if power_cut_count >= 2:
        out.append(Finding(
            id="crash:power_cut",
            title=f"{power_cut_count} dirty shutdown(s) / sudden power cuts in the last {days} days",
            detail=(
                "The system rebooted without cleanly shutting down first. This can indicate power supply instability, "
                "thermal cut-off, or physical resets."
            ),
            severity="warning",
            evidence=f"Kernel-Power Event 41 power-cut events in 30 days: {power_cut_count}",
            jump="Diagnose",
        ))

    return out


def check_crash_history() -> List[Finding]:
    """Audit Windows crash dump configuration and recent BSOD / WHEA / live kernel incidents."""
    import winreg
    from modules.crash_dumps import crash_dump_reader as cdr
    from modules.crash_dumps.rca_engine import correlate_incidents

    dump_enabled = None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CrashControl") as key:
            val, _ = winreg.QueryValueEx(key, "CrashDumpEnabled")
            dump_enabled = int(val)
    except OSError as exc:
        logger.debug("Could not read CrashDumpEnabled from registry: %s", exc)

    entries = []
    # 1. Minidumps
    try:
        mds, _ = cdr._scan_dir(cdr.MINIDUMP_DIR, "Minidump")
        entries.extend(mds)
    except Exception as exc:
        logger.debug("Failed scanning minidumps: %s", exc)

    # 2. BugCheck 1001 events
    try:
        bcs, _ = cdr._bugcheck_events(days=30)
        entries.extend(bcs)
    except Exception as exc:
        logger.debug("Failed querying bugcheck events: %s", exc)

    # 3. Kernel-Power 41 events
    try:
        kps, _ = cdr._kernel_power_events(days=30)
        entries.extend(kps)
    except Exception as exc:
        logger.debug("Failed querying kernel power events: %s", exc)

    # 4. WHEA events
    try:
        whea, _ = cdr._whea_events(days=30)
        entries.extend(whea)
    except Exception as exc:
        logger.debug("Failed querying WHEA events: %s", exc)

    # 5. Live kernel reports
    try:
        live, _ = cdr._scan_live_reports(cdr.LIVE_REPORTS_DIR)
        entries.extend(live)
    except Exception as exc:
        logger.debug("Failed scanning live kernel reports: %s", exc)

    try:
        overview = correlate_incidents(entries)
    except Exception as exc:
        logger.warning("Failed correlating crash incidents: %s", exc)
        return evaluate_crash_history(dump_enabled, [], [], [], 0)

    bsod_dicts = [
        {
            "timestamp": str(inc.timestamp),
            "bugcheck_label": inc.bugcheck_label,
            "bugcheck_code": inc.bugcheck_code,
            "faulting_module": inc.faulting_module,
        }
        for inc in overview.incidents if inc.category == "bsod"
    ]
    whea_dicts = [
        {
            "timestamp": str(inc.timestamp),
            "title": inc.title,
            "apic_id": inc.apic_id,
        }
        for inc in overview.incidents if inc.category == "hardware_whea"
    ]
    live_dicts = [
        {
            "timestamp": str(inc.timestamp),
            "title": inc.title,
        }
        for inc in overview.incidents if inc.category == "live_hang"
    ]

    return evaluate_crash_history(
        dump_enabled,
        bsod_dicts,
        whea_dicts,
        live_dicts,
        power_cut_count=overview.power_cut_count,
    )


# ---- Antivirus & Threat Protection ------------------------------------------------

def evaluate_antivirus_health(
    products: List[dict],
    threat_events: List[dict],
    days: int = 30,
) -> List[Finding]:
    """Turn antivirus scanner state, signature freshness, and recent threat events into health findings."""
    out: List[Finding] = []

    active = [p for p in products if p.get("scanner_on")]
    active_names = [p.get("name", "Antivirus") for p in active]

    if not active:
        out.append(Finding(
            id="antivirus:protection_missing",
            title="No active antivirus real-time protection detected",
            detail=(
                "Windows Security Center reports no active real-time antivirus scanner running. "
                "The machine is unprotected against malicious downloads, malware, and ransomware."
            ),
            severity="error",
            evidence="No registered antivirus product has real-time scanning enabled in SecurityCenter2",
            jump="Security Dashboard",
        ))
    elif len(active) > 1:
        out.append(Finding(
            id="antivirus:multiple_active",
            title=f"Multiple active antivirus scanners ({', '.join(active_names)})",
            detail=(
                "Multiple real-time antivirus engines are actively scanning files simultaneously. "
                "This typically causes high CPU/disk usage, severe file lock contention, and system sluggishness."
            ),
            severity="warning",
            evidence="\n".join(f"• {p.get('name', 'AV')}: state 0x{p.get('state', 0):X}" for p in active),
            jump="Security Dashboard",
        ))

    stale = [p for p in active if not p.get("defs_updated")]
    if stale:
        stale_names = ", ".join(p.get("name", "Antivirus") for p in stale)
        out.append(Finding(
            id="antivirus:definitions_outdated",
            title=f"Antivirus signatures are outdated ({stale_names})",
            detail=(
                "Security Center reports that virus definitions/signatures are out of date for an active scanner. "
                "Update virus definitions to ensure protection against newly discovered threats."
            ),
            severity="warning",
            evidence="\n".join(f"• {p.get('name')}: state 0x{p.get('state', 0):X}" for p in stale),
            jump="Security Dashboard",
        ))

    if threat_events:
        failed_actions = [e for e in threat_events if e.get("event_id") == 1008]
        sev = "error" if failed_actions else "warning"
        n = len(threat_events)
        lines = []
        for e in threat_events[:5]:
            ts = e.get("timestamp", "")
            name = e.get("threat_name") or "Threat"
            path = e.get("path", "")
            act = e.get("action", "")
            path_str = f" -- {path}" if path else ""
            act_str = f" [{act}]" if act else ""
            lines.append(f"• {ts}: {name}{path_str}{act_str}")
        if n > 5:
            lines.append(f"... and {n - 5} more threat event{'s' if n - 5 > 1 else ''}")
        out.append(Finding(
            id="antivirus:threats_detected",
            title=f"{n} malware threat{'s' if n > 1 else ''} detected in the last {days} days",
            detail=(
                "Microsoft Defender recorded malware threat detections. "
                "Check the threat details, affected file paths, and quarantine status in Security Dashboard."
            ),
            severity=sev,
            evidence="\n".join(lines),
            jump="Security Dashboard",
        ))

    return out


def check_antivirus_health() -> List[Finding]:
    """Audit Windows Security Center for registered antivirus scanners, status, and recent threat events."""
    import ctypes
    from modules.event_viewer import event_query

    products = []
    hr = -1
    try:
        hr = ctypes.windll.ole32.CoInitialize(None)
        try:
            import win32com.client
            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\SecurityCenter2")
            for p in wmi.ExecQuery("SELECT * FROM AntiVirusProduct"):
                try:
                    st = int(p.productState)
                    products.append({
                        "name": str(p.displayName),
                        "state": st,
                        "scanner_on": ((st >> 8) & 0xFF) in (0x10, 0x11),
                        "defs_updated": (st & 0xF0) == 0,
                    })
                except (ValueError, TypeError, AttributeError) as exc:
                    logger.debug("Failed parsing AntiVirusProduct %s: %s", getattr(p, "displayName", "unknown"), exc)
        finally:
            if hr >= 0:
                ctypes.windll.ole32.CoUninitialize()
    except Exception as exc:
        logger.debug("Could not query root\\SecurityCenter2 AntiVirusProduct: %s", exc)

    threats = []
    try:
        xpath = "*[System[(EventID=1116 or EventID=1117 or EventID=1006 or EventID=1008) and TimeCreated[timediff(@SystemTime) <= 2592000000]]]"
        res = event_query.query_log("Microsoft-Windows-Windows Defender/Operational", None, 50, xpath=xpath)
        if res.error is None:
            for e in res.entries:
                raw_data = e.data or {}
                threats.append({
                    "timestamp": str(e.timestamp),
                    "event_id": e.event_id,
                    "threat_name": raw_data.get("Threat Name", ""),
                    "path": raw_data.get("Path", ""),
                    "action": raw_data.get("Action Name", ""),
                })
        else:
            logger.debug("Defender Operational log query error: %s", res.error)
    except Exception as exc:
        logger.debug("Could not query Defender threat events: %s", exc)

    return evaluate_antivirus_health(products, threats)


# ---- entry point -----------------------------------------------------------------

CHECKS: Tuple[Tuple[str, Callable[[], List[Finding]]], ...] = (
    ("pending reboot", check_pending_reboot),
    ("time sync", check_time_sync),
    ("physical disks", check_disks),
    ("storage errors", check_storage_events),
    ("WMI repository", check_wmi_repository),
    ("commit charge", check_commit),
    ("CBS log", check_cbs_log),
    ("problem devices", check_problem_devices),
    ("services", check_services),
    ("certificates", check_certificates),
    ("Windows Update failures", check_windows_update_failures),
    ("driver load failures", check_driver_load_failures),
    ("TPM attestation", check_tpm_attestation_events),
    ("firewall exposure", check_firewall_exposure),
    ("scheduled task failures", check_scheduled_task_failures),
    ("local accounts", check_local_accounts),
    ("PATH environment", check_path_environment),
    ("software inventory", check_software_health),
    ("firmware security", check_firmware_security),
    ("crash history", check_crash_history),
    ("antivirus health", check_antivirus_health),
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
