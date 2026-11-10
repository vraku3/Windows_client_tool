"""Read-only System Health findings. No Qt, no writes -- every function
here is safe to call unelevated and safe to call from --unattended
--stages health.
"""
from core.system_paths import powershell_exe
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from core.windows_utils import system_root

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    id: str
    title: str
    detail: str
    severity: str  # "info" | "warning" -- no "danger": nothing here deletes anything
    evidence: str = ""   # the raw facts the finding rests on, shown and copied with it
    jump: str = ""       # name of the module/tab that can act on it ("" = none)

    def copy_text(self) -> str:
        parts = [f"[{self.severity}] {self.title}", self.detail]
        if self.evidence:
            parts.append("Evidence:\n" + self.evidence)
        return "\n".join(p for p in parts if p)


def check_pending_servicing() -> Optional[Finding]:
    """WinSxS\\pending.xml existing means a servicing transaction is
    mid-flight (usually resolved by the next reboot). Detecting it is
    exactly what it sounds like -- a file-existence check -- but it
    answers a real question ("why won't DISM let me run ResetBase right
    now") that this module's own ResetBase gate (2.3) needs to ask too."""
    path = os.path.join(system_root(), "WinSxS", "pending.xml")
    if not os.path.exists(path):
        return None
    return Finding(
        id="pending_servicing",
        title="A servicing transaction is pending",
        detail=(
            f"{path} exists, meaning Windows has an in-progress "
            "component update. This usually clears on the next reboot. "
            "DISM component-store operations may refuse to run until it does."
        ),
        severity="warning",
    )


def check_orphaned_scheduled_tasks() -> List[Finding]:
    """A scheduled task whose Action names a program path that no longer
    exists on disk. Uses schtasks /query /xml (per-task), which -- like
    every other schtasks call in this codebase -- can be read
    unelevated; a refused read is reported as a Finding of its own
    rather than silently producing zero results (a refusal is never
    reported as "nothing found" -- CLAUDE.md's own recurring rule)."""
    findings: List[Finding] = []
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=30,
            creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [Finding(
            id="orphaned_tasks_refused",
            title="Could not enumerate scheduled tasks",
            detail=str(e), severity="warning")]
    if result.returncode != 0:
        return [Finding(
            id="orphaned_tasks_refused",
            title="Could not enumerate scheduled tasks",
            detail=(result.stderr or result.stdout or "schtasks refused").strip(),
            severity="warning")]

    import csv
    import io
    task_names = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if row and row[0].strip():
            task_names.append(row[0])
    # schtasks lists a task once per trigger, so one with several triggers
    # (USO_UxBroker) came back -- and was reported -- more than once.
    task_names = list(dict.fromkeys(task_names))

    for name in task_names:
        from_file = _task_xml_from_file(name)
        if from_file is not None:
            program = _extract_command_path(from_file)
            if program and not _program_exists(program):
                findings.append(_orphan_finding(name, program))
            continue
        try:
            xml_result = subprocess.run(
                ["schtasks", "/query", "/tn", name, "/xml"],
                capture_output=True, text=True, timeout=15,
                creationflags=0x08000000)
        except (OSError, subprocess.TimeoutExpired) as e:
            findings.append(Finding(
                id=f"orphaned_task_check_refused:{name}",
                title=f"Could not check scheduled task {name!r}",
                detail=str(e), severity="warning"))
            continue
        if xml_result.returncode != 0:
            findings.append(Finding(
                id=f"orphaned_task_check_refused:{name}",
                title=f"Could not check scheduled task {name!r}",
                detail=(xml_result.stderr or xml_result.stdout
                        or "schtasks refused").strip(),
                severity="warning"))
            continue
        program = _extract_command_path(xml_result.stdout)
        if program and not _program_exists(program):
            findings.append(_orphan_finding(name, program))
    return findings


def _orphan_finding(name: str, program: str) -> Finding:
    # Plain quotes, not repr(): repr doubled every backslash in the path.
    return Finding(
        id=f"orphaned_task:{name}",
        title="Scheduled task points at a missing program",
        detail=f"Task '{name}' runs '{program}', which does not exist.",
        severity="info",
    )


def _task_xml_from_file(name: str) -> Optional[str]:
    """The task's definition read straight from the System32 Tasks folder, or None.

    `schtasks /query /xml` writes through the console's legacy codepage, so a
    character it cannot represent came back as `?` ("Aplica?ii" for "Aplicatii"
    with a comma-below t) -- and the mangled path does not exist, so a task that
    points at a real program was reported as pointing at a missing one. The
    files Windows keeps are UTF-16 and lose nothing. Unreadable (they are
    admin-only) is simply None, and the caller falls back to schtasks."""
    root = os.path.join(system_root(), "System32", "Tasks")
    parts = [part for part in name.split("\\") if part]
    if not parts:
        return None
    try:
        with open(os.path.join(root, *parts), "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        logger.debug("Task file %s not readable (%s); using schtasks", name, exc)
        return None
    for encoding in ("utf-16", "utf-8-sig"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            logger.debug("Task file %s is not %s", name, encoding)
    return None


def _extract_command_path(task_xml: str) -> Optional[str]:
    import re
    match = re.search(r"<Command>(.*?)</Command>", task_xml, re.IGNORECASE)
    if not match:
        return None
    return os.path.expandvars(match.group(1).strip().strip('"'))


def _program_exists(path: str) -> bool:
    if os.path.isabs(path) and os.path.exists(path):
        return True
    # A bare exe name (e.g. "notepad.exe") resolves via PATH -- shutil.which
    # is the correct check for that shape, os.path.exists alone would
    # always say "missing" for anything not given as a full path.
    import shutil
    return shutil.which(path) is not None


_NT_PREFIX = "\\??\\"
_EXE_OR_SYS = re.compile(r"^(.*?\.(exe|sys))(?=\s|$)", re.IGNORECASE)
_START_TYPE_NAMES = {0: "Boot", 1: "System", 2: "Automatic", 3: "Manual", 4: "Disabled"}


def check_orphaned_services() -> List[Finding]:
    """A service or driver whose ImagePath names a program that no longer
    exists on disk -- the other half of `check_orphaned_scheduled_tasks`,
    deferred out of the original System Health design because
    `HKLM\\SYSTEM\\CurrentControlSet\\Services` is a much larger, more
    security-sensitive registry area. Reading it is no more dangerous than
    reading anything else here; only writing to it would be.

    Pure registry, like `service_audit.read_startup_flags` -- one open of
    the Services key, then one per-service subkey. A single unreadable
    service is skipped, not fatal; the whole key failing to open is
    reported as a refusal, never as "nothing found".

    A relative ImagePath (nearly every driver: `System32\\drivers\\ACPI.sys`)
    resolves against %SystemRoot%, not the working directory -- getting that
    wrong turned 736 perfectly healthy drivers on this real machine into 201
    false "missing" results before this was fixed. Confirmed against the
    real machine: 2 genuine orphans out of 831 services, both explicable
    (HWiNFO64's kernel driver extracts to %TEMP% and is expected to vanish
    between runs; a leftover WinSetupMon.sys boot-start driver entry).
    """
    import winreg
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services",
                              0, winreg.KEY_READ)
    except OSError as exc:
        return [Finding(
            id="orphaned_services_refused",
            title="Could not enumerate services",
            detail=str(exc), severity="warning")]
    findings: List[Finding] = []
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError as exc:
                logger.debug("service enumeration ended at index %d: %s", index, exc)
                break
            index += 1
            try:
                with winreg.OpenKey(root, name, 0, winreg.KEY_READ) as key:
                    try:
                        image_path = winreg.QueryValueEx(key, "ImagePath")[0]
                    except FileNotFoundError:
                        logger.debug("service %s has no ImagePath (a pseudo-service, e.g. a driver group)", name)
                        continue
                    try:
                        start = winreg.QueryValueEx(key, "Start")[0]
                    except FileNotFoundError:
                        start = None
            except OSError as exc:
                logger.debug("service key %s unreadable: %s", name, exc)
                continue
            program = _service_program_path(image_path)
            if program and not _service_program_exists(program):
                findings.append(_orphan_service_finding(name, image_path, program, start))
    return findings


def _service_program_path(image_path: str) -> Optional[str]:
    """The .exe/.sys an ImagePath value names, or None for the rare value
    that names neither (a pseudo-service with no real backing file)."""
    text = os.path.expandvars((image_path or "").strip())
    if text.lower().startswith(r"\systemroot"):
        text = system_root() + text[len(r"\systemroot"):]
    if text.startswith(_NT_PREFIX):
        text = text[len(_NT_PREFIX):]
    if text.startswith('"'):
        end = text.find('"', 1)
        program = text[1:end] if end > 0 else text[1:]
    else:
        match = _EXE_OR_SYS.match(text)
        program = match.group(1) if match else text
    if not program.lower().endswith((".exe", ".sys")):
        return None
    return program


def _service_program_exists(program: str) -> bool:
    if not os.path.isabs(program):
        program = os.path.join(system_root(), program)
    return os.path.exists(program)


def _orphan_service_finding(name: str, image_path: str, program: str, start: Optional[int]) -> Finding:
    start_name = _START_TYPE_NAMES.get(start, "unknown")
    boot_critical = start in (0, 1)
    return Finding(
        id=f"orphaned_service:{name}",
        title=f"Service '{name}' points at a missing file",
        detail=(
            f"ImagePath '{image_path}' resolves to '{program}', which does not exist. "
            f"Start type: {start_name}."
            + (" A boot/system-start driver missing its file can surface as a boot "
               "warning even though nothing currently depends on it."
               if boot_critical else
               " Usually a leftover registry entry from an uninstalled program or "
               "driver -- harmless unless something still tries to start it.")
        ),
        severity="warning" if boot_critical else "info",
        jump="Services",
    )


def check_hardware_errors(days: int = 30) -> List[Finding]:
    """Microsoft-Windows-WHEA-Logger events -- Windows' own standard channel
    for corrected and uncorrected hardware errors (failing RAM, CPU, PCIe
    links), the same source `mcelog`/`rasdaemon` read on Linux. Reported as
    a summary (count, level, most recent message) rather than the raw WHEA
    payload: parsing a memory address or error-source ID out of that binary
    payload without a real one to test against would be guessing at its
    shape, and this machine currently logs zero WHEA events to verify
    against -- confirmed clean, not merely untested.
    """
    script = (
        "$ErrorActionPreference='Stop';"
        f"$f=@{{LogName='System';ProviderName='Microsoft-Windows-WHEA-Logger';"
        f"StartTime=(Get-Date).AddDays(-{int(days)})}};"
        "try{$e=Get-WinEvent -FilterHashtable $f -MaxEvents 500}"
        "catch{if($_.FullyQualifiedErrorId -like '*NoMatchingEventsFound*'){'[]';exit 0}else{throw}};"
        "@($e|%{[pscustomobject]@{Level=$_.LevelDisplayName;"
        "Time=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss');"
        "Message=(($_.Message -split \"`n\")[0])}})|ConvertTo-Json -Compress")
    try:
        result = subprocess.run(
            [powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        return [Finding(id="hardware_errors_refused", title="Could not read hardware error events",
                        detail=str(e), severity="warning")]
    if result.returncode != 0:
        return [Finding(id="hardware_errors_refused", title="Could not read hardware error events",
                        detail=(result.stderr or result.stdout or "refused").strip()[:200],
                        severity="warning")]
    import json
    try:
        data = json.loads(result.stdout.strip() or "[]")
    except ValueError:
        return [Finding(id="hardware_errors_refused", title="Could not read hardware error events",
                        detail="Response was not JSON.", severity="warning")]
    if isinstance(data, dict):
        data = [data]
    if not data:
        return []
    fatal = [r for r in data if (r.get("Level") or "").lower() in ("error", "critical")]
    latest = max(data, key=lambda r: r.get("Time") or "")
    return [Finding(
        id="hardware_errors",
        title=f"{len(data)} hardware error event(s) in the last {days} days",
        detail=("Windows' own WHEA hardware-error log (corrected and uncorrected CPU/RAM/PCIe "
                f"errors). Most recent ({latest.get('Level', '?')}, {latest.get('Time', '?')}): "
                f"{(latest.get('Message') or '').strip()}"),
        severity="warning" if fatal else "info",
    )]


def check_upgrade_headroom() -> Finding:
    """Free space on the system drive against a practical minimum for a
    feature update -- NOT an official Microsoft figure (Microsoft
    publishes a 64 GB *install* minimum for Windows 11 itself, not a
    per-upgrade free-space number), stated as an approximation in the
    UI text itself so nobody mistakes this for an authoritative number."""
    import shutil as _shutil
    system_drive = os.environ.get("SystemDrive", "C:") + "\\"
    total, used, free = _shutil.disk_usage(system_drive)
    free_gb = free / (1024 ** 3)
    threshold_gb = 20  # a commonly-cited practical minimum, not an official one
    if free_gb < threshold_gb:
        return Finding(
            id="upgrade_headroom",
            title=f"Only {free_gb:.1f} GB free on {system_drive}",
            detail=(
                f"Feature updates commonly need roughly {threshold_gb} GB "
                "of free space to install (a practical rule of thumb, not "
                "an official Microsoft minimum) -- you're below that."
            ),
            severity="warning",
        )
    return Finding(
        id="upgrade_headroom",
        title=f"{free_gb:.1f} GB free on {system_drive}",
        detail=f"Above the ~{threshold_gb} GB practical minimum for a feature update.",
        severity="info",
    )


def check_crash_control() -> List[Finding]:
    """Audit Windows BSOD / crash dump configuration in CrashControl.

    A machine where CrashDumpEnabled is 0 produces no crash dumps on BSOD,
    preventing root-cause analysis. If AutoReboot is 0, the system hangs
    indefinitely on the bugcheck screen until manually power-cycled.
    """
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\CrashControl",
            0,
            winreg.KEY_READ,
        ) as k:
            try:
                dump_type = winreg.QueryValueEx(k, "CrashDumpEnabled")[0]
            except FileNotFoundError:
                dump_type = None
            try:
                auto_reboot = winreg.QueryValueEx(k, "AutoReboot")[0]
            except FileNotFoundError:
                auto_reboot = None
            try:
                log_event = winreg.QueryValueEx(k, "LogEvent")[0]
            except FileNotFoundError:
                log_event = None
    except OSError as exc:
        logger.debug("CrashControl unreadable: %s", exc)
        return [Finding(
            id="crash_control_refused",
            title="Could not read CrashControl configuration",
            detail=str(exc),
            severity="info",
            jump="Diagnose",
        )]

    findings: List[Finding] = []
    dump_names = {
        0: "None (Disabled)",
        1: "Complete memory dump",
        2: "Kernel memory dump",
        3: "Small memory dump (Minidump)",
        7: "Automatic memory dump",
    }

    if dump_type == 0:
        findings.append(Finding(
            id="crash_dumps_disabled",
            title="Crash dumps are disabled",
            detail="Windows is configured not to write a memory dump when a Blue Screen (BSOD) occurs. Without memory dumps, diagnosing system crashes or hardware faults is impossible.",
            severity="warning",
            evidence="CrashDumpEnabled=0 in HKLM\\SYSTEM\\CurrentControlSet\\Control\\CrashControl",
            jump="Diagnose",
        ))
    elif dump_type is not None:
        findings.append(Finding(
            id="crash_dump_config",
            title=f"Crash dumps configured ({dump_names.get(dump_type, f'Type {dump_type}')})",
            detail=f"Windows will save a {dump_names.get(dump_type, 'dump')} on system failure.",
            severity="info",
            evidence=f"CrashDumpEnabled={dump_type}, AutoReboot={auto_reboot}, LogEvent={log_event}",
            jump="Diagnose",
        ))

    if auto_reboot == 0:
        findings.append(Finding(
            id="auto_reboot_disabled",
            title="Automatic reboot on system failure is disabled",
            detail="When a BSOD occurs, the system will remain frozen on the bluescreen indefinitely instead of restarting automatically.",
            severity="warning",
            evidence="AutoReboot=0 in HKLM\\SYSTEM\\CurrentControlSet\\Control\\CrashControl",
            jump="Diagnose",
        ))

    return findings


def check_print_queue() -> List[Finding]:
    """Check for stuck print job files in %SystemRoot%\\System32\\spool\\PRINTERS.

    Orphaned spool files (.SHD/.SPL) left by aborted print jobs or stalled printers
    cause spoolsv.exe high CPU, spooler crashes, or block all subsequent print jobs.
    """
    spool_dir = os.path.join(system_root(), "System32", "spool", "PRINTERS")
    if not os.path.exists(spool_dir):
        return []
    try:
        entries = os.listdir(spool_dir)
        spool_files = [f for f in entries if f.lower().endswith((".shd", ".spl"))]
    except OSError as exc:
        logger.debug("spool\\PRINTERS unreadable: %s", exc)
        return []

    if not spool_files:
        return []

    return [Finding(
        id="stuck_print_jobs",
        title=f"{len(spool_files)} stuck print spool file(s) in queue",
        detail=(
            f"Found {len(spool_files)} orphaned .SHD/.SPL file(s) in {spool_dir}. "
            "Stuck spool files can stall the Print Spooler or block printing until cleared."
        ),
        severity="warning",
        evidence=", ".join(spool_files[:10]) + (f" (+{len(spool_files)-10} more)" if len(spool_files) > 10 else ""),
        jump="Quick Fix",
    )]


def check_driver_blocklist() -> Finding:
    """Check Microsoft Vulnerable Driver Blocklist (WDAC / HVCI driver blocklist).

    When enabled, Windows blocks known vulnerable third-party drivers with known
    security flaws from loading, mitigating Bring Your Own Vulnerable Driver (BYOVD)
    privilege escalation attacks.
    """
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\CI\Config",
            0,
            winreg.KEY_READ,
        ) as k:
            val, _ = winreg.QueryValueEx(k, "VulnerableDriverBlocklistEnable")
            enabled = bool(val)
    except FileNotFoundError:
        enabled = False
    except OSError as exc:
        logger.debug("VulnerableDriverBlocklist unreadable: %s", exc)
        return Finding(
            id="driver_blocklist_unknown",
            title="Could not check Vulnerable Driver Blocklist",
            detail=str(exc),
            severity="info",
            jump="Security Dashboard",
        )

    if not enabled:
        return Finding(
            id="vulnerable_driver_blocklist_disabled",
            title="Microsoft Vulnerable Driver Blocklist is disabled",
            detail="The kernel does not enforce Microsoft's list of blocked drivers with known security vulnerabilities, leaving the system exposed to Bring Your Own Vulnerable Driver (BYOVD) attacks.",
            severity="warning",
            evidence="VulnerableDriverBlocklistEnable=0 or missing under HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Config",
            jump="Security Dashboard",
        )
    return Finding(
        id="vulnerable_driver_blocklist_enabled",
        title="Microsoft Vulnerable Driver Blocklist is active",
        detail="The kernel blocks drivers with known security vulnerabilities from loading.",
        severity="info",
        evidence="VulnerableDriverBlocklistEnable=1 under HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Config",
        jump="Security Dashboard",
    )


def check_vss_services() -> List[Finding]:
    """Check if Volume Shadow Copy (VSS) or Software Shadow Copy Provider (swprv) are disabled.

    Both services must not be disabled for restore points, shadow copies, and servicing rollbacks.
    """
    import winreg
    findings = []
    for svc_name, display_name in (
        ("VSS", "Volume Shadow Copy service"),
        ("swprv", "Microsoft Software Shadow Copy Provider"),
    ):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                rf"SYSTEM\CurrentControlSet\Services\{svc_name}",
                0,
                winreg.KEY_READ,
            ) as k:
                val, _ = winreg.QueryValueEx(k, "Start")
                if val == 4:  # Disabled
                    findings.append(Finding(
                        id=f"{svc_name.lower()}_service_disabled",
                        title=f"{display_name} is disabled",
                        detail=f"The {display_name} ({svc_name}) is configured as Disabled. System Restore point creation and servicing rollback will fail.",
                        severity="warning",
                        evidence=f"Start=4 under HKLM\\SYSTEM\\CurrentControlSet\\Services\\{svc_name}",
                        jump="Services",
                    ))
        except FileNotFoundError:
            logger.debug("VSS service %s key not found", svc_name)
        except OSError as exc:
            logger.debug("Could not read %s service key: %s", svc_name, exc)
    return findings


def check_fast_startup_uptime() -> Optional[Finding]:
    """Audit kernel uptime against Fast Startup hybrid shutdown state.

    Fast Startup retains kernel memory across shutdowns. If the machine
    has not undergone a full reboot in over 14 days, pending file renames, servicing transactions,
    and driver memory can cause latency or instability.
    """
    import winreg
    import ctypes

    try:
        fast_startup_enabled = True
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Power",
                0,
                winreg.KEY_READ,
            ) as k:
                val, _ = winreg.QueryValueEx(k, "HiberbootEnabled")
                fast_startup_enabled = bool(val)
        except FileNotFoundError:
            logger.debug("HiberbootEnabled registry value not found, defaulting to True")

        if not fast_startup_enabled:
            return None

        # Uptime in milliseconds
        tick_ms = ctypes.windll.kernel32.GetTickCount64()
        days = tick_ms / (1000 * 60 * 60 * 24)
        if days >= 14.0:
            return Finding(
                id="fast_startup_prolonged_uptime",
                title=f"System has not had a full restart in {int(days)} days (Fast Startup active)",
                detail=(
                    "Windows Fast Startup saves the kernel session to disk during shutdown rather "
                    "than terminating it. A full restart is recommended to flush locked resources, "
                    "reset kernel pools, and apply pending driver/servicing updates."
                ),
                severity="info" if days < 30.0 else "warning",
                evidence=f"Kernel uptime: {days:.1f} days, HiberbootEnabled=1",
                jump="System Health",
            )
    except Exception as exc:
        logger.debug("Could not evaluate uptime or fast startup: %s", exc)
    return None


def check_scheduled_chkdsk() -> Optional[Finding]:
    """Check if a boot-time autochk (chkdsk) is queued in BootExecute."""
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager",
            0,
            winreg.KEY_READ,
        ) as k:
            val, _ = winreg.QueryValueEx(k, "BootExecute")
            entries = val if isinstance(val, list) else [val]
            for entry in entries:
                if entry and isinstance(entry, str) and ("autochk /p" in entry or "\\??\\" in entry):
                    return Finding(
                        id="scheduled_chkdsk_pending",
                        title="File system check (chkdsk) is queued for next reboot",
                        detail=f"Windows will perform an autochk disk scan on next boot: '{entry.strip()}'.",
                        severity="warning",
                        evidence=f"BootExecute={val}",
                        jump="Disk Health",
                    )
    except FileNotFoundError:
        logger.debug("BootExecute registry value not found")
    except OSError as exc:
        logger.debug("Could not read BootExecute: %s", exc)
    return None


def check_wer_queue(max_reports: int = 50, max_mb: float = 500.0) -> Optional[Finding]:
    """Check for crash report and minidump buildup in Windows Error Reporting queue."""
    from core.windows_utils import program_data
    queue_path = os.path.join(program_data(), "Microsoft", "Windows", "WER", "ReportQueue")
    if not os.path.isdir(queue_path):
        return None
    try:
        entries = os.listdir(queue_path)
    except OSError as exc:
        logger.debug("WER ReportQueue unreadable: %s", exc)
        return None
    if not entries:
        return None
    total_bytes = 0
    try:
        for root, _dirs, files in os.walk(queue_path):
            for f in files:
                try:
                    total_bytes += os.path.getsize(os.path.join(root, f))
                except OSError as exc:
                    logger.debug("WER file size read failed for %s: %s", f, exc)
                    continue
    except OSError as exc:
        logger.debug("WER tree walk failed: %s", exc)
    size_mb = total_bytes / (1024 * 1024)
    count = len(entries)
    if count >= max_reports or size_mb >= max_mb:
        return Finding(
            id="wer_queue_buildup",
            title=f"Windows Error Reporting queue holds {count} crash report(s) ({size_mb:.1f} MB)",
            detail=(
                "Crash packages and minidumps are accumulating in the WER ReportQueue without "
                "being cleared. This indicates recurring application crashes or disabled telemetry "
                "upload services."
            ),
            severity="warning",
            evidence=f"{count} report folders in {queue_path}, total size {size_mb:.1f} MB",
            jump="Quick Fix",
        )
    return Finding(
        id="wer_queue_buildup",
        title=f"{count} crash report(s) in Windows Error Reporting queue ({size_mb:.1f} MB)",
        detail="Pending crash reports awaiting upload or cleanup.",
        severity="info",
        evidence=f"{count} report folders in {queue_path}",
        jump="Quick Fix",
    )


def check_safeboot_active() -> Optional[Finding]:
    """Check if Windows is currently operating in Safe Mode."""
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\SafeBoot\Option",
            0,
            winreg.KEY_READ,
        ) as k:
            val, _ = winreg.QueryValueEx(k, "OptionValue")
            mode_map = {1: "Minimal", 2: "Network", 3: "Directory Services Repair"}
            mode_name = mode_map.get(val, f"Mode {val}")
            return Finding(
                id="safeboot_active",
                title=f"Windows is running in Safe Mode ({mode_name})",
                detail=(
                    "The system was booted into Safe Mode. Many drivers, services, and hardware "
                    "acceleration features are disabled until a normal reboot."
                ),
                severity="warning",
                evidence=f"OptionValue={val} under HKLM\\SYSTEM\\CurrentControlSet\\Control\\SafeBoot\\Option",
                jump="Startup & Boot",
            )
    except (FileNotFoundError, OSError):
        return None


def all_findings() -> List[Finding]:
    findings = []
    pending = check_pending_servicing()
    if pending:
        findings.append(pending)
    safeboot = check_safeboot_active()
    if safeboot:
        findings.append(safeboot)
    findings.extend(check_orphaned_scheduled_tasks())
    findings.extend(check_orphaned_services())
    findings.extend(check_hardware_errors())
    findings.extend(check_crash_control())
    findings.extend(check_print_queue())
    findings.extend(check_vss_services())
    uptime_finding = check_fast_startup_uptime()
    if uptime_finding:
        findings.append(uptime_finding)
    chkdsk_finding = check_scheduled_chkdsk()
    if chkdsk_finding:
        findings.append(chkdsk_finding)
    wer_finding = check_wer_queue()
    if wer_finding:
        findings.append(wer_finding)
    findings.append(check_driver_blocklist())
    findings.append(check_upgrade_headroom())
    return findings


def full_findings(is_cancelled=lambda: False) -> List[Finding]:
    """The quick set plus every live-machine check (slower: PowerShell, w32tm,
    the System log). Used by the System Health pane; the unattended stage keeps
    `all_findings()`."""
    from modules.system_health import health_checks
    findings = all_findings()
    findings.extend(health_checks.run_all(is_cancelled=is_cancelled))
    findings.sort(key=lambda f: 0 if f.severity == "warning" else 1)
    return findings
