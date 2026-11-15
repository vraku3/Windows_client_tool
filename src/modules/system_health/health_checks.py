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
from typing import Any, Callable, Dict, List, Optional, Tuple

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


# ---- entry point -----------------------------------------------------------------

CHECKS: Tuple[Tuple[str, Callable[[], List[Finding]]], ...] = (
    ("pending reboot", check_pending_reboot),
    ("time sync", check_time_sync),
    ("physical disks", check_disks),
    ("WMI repository", check_wmi_repository),
    ("commit charge", check_commit),
    ("CBS log", check_cbs_log),
    ("problem devices", check_problem_devices),
    ("services", check_services),
    ("certificates", check_certificates),
    ("Windows Update failures", check_windows_update_failures),
    ("driver load failures", check_driver_load_failures),
    ("TPM attestation", check_tpm_attestation_events),
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
