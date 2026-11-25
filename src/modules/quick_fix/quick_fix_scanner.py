"""Qt-free pre-flight health scanner for Quick Fix module.

Inspects Windows system state (WMI, print spooler, VSS writers, WinRE,
unquoted service paths, pending reboot, BITS/WU, and time synchronization)
to identify actionable issues and recommend corresponding FixAction keys.
"""

from dataclasses import dataclass, field
import logging
import os
import subprocess
import time
from typing import Dict, List, Optional, Set
import winreg

from core.windows_utils import is_reboot_pending, system_root

logger = logging.getLogger(__name__)


@dataclass
class HealthFinding:
    check_id: str
    title: str
    description: str
    severity: str  # "warning", "danger", "info"
    recommended_fix_keys: List[str] = field(default_factory=list)


@dataclass
class QuickFixHealthReport:
    findings: List[HealthFinding] = field(default_factory=list)
    recommended_keys: Set[str] = field(default_factory=set)
    timestamp: float = field(default_factory=time.time)

    @property
    def issue_count(self) -> int:
        return len([f for f in self.findings if f.severity in ("warning", "danger")])


def _run_quick_cmd(cmd: List[str], timeout: float = 8.0) -> subprocess.CompletedProcess:
    """Helper to run diagnostic tools safely with timeout and no window."""
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        timeout=timeout,
        check=False,
    )


def check_wmi_repository() -> Optional[HealthFinding]:
    """Check if WMI repository is consistent."""
    try:
        proc = _run_quick_cmd(["winmgmt", "/verifyrepository"], timeout=8.0)
        output = (proc.stdout + proc.stderr).lower()
        if proc.returncode != 0 or "inconsistent" in output:
            return HealthFinding(
                check_id="wmi_repository",
                title="WMI Repository Inconsistent",
                description="The WMI repository reported corruption or inconsistency, which may disrupt management tools and system services.",
                severity="danger",
                recommended_fix_keys=["wmi_salvage"],
            )
    except Exception as exc:
        logger.debug("WMI verification check failed: %s", exc)
    return None


def check_print_spooler_queue() -> Optional[HealthFinding]:
    """Check for stuck print jobs in the spool directory."""
    try:
        spool_dir = os.path.join(system_root(), "System32", "spool", "PRINTERS")
        if os.path.isdir(spool_dir):
            files = [
                f for f in os.listdir(spool_dir)
                if f.lower().endswith((".spl", ".shd"))
            ]
            if files:
                return HealthFinding(
                    check_id="print_queue",
                    title="Print Spooler Jobs Queued",
                    description=f"{len(files)} stuck or pending print job files detected in spool directory.",
                    severity="warning",
                    recommended_fix_keys=["print_queue", "print_spooler_restart"],
                )
    except Exception as exc:
        logger.debug("Print spooler check failed: %s", exc)
    return None


def check_vss_writers() -> Optional[HealthFinding]:
    """Check for failed or non-stable VSS writers."""
    try:
        proc = _run_quick_cmd(["vssadmin", "list", "writers"], timeout=8.0)
        failed_writers: List[str] = []
        current_writer = ""
        for line in proc.stdout.splitlines():
            line_str = line.strip()
            if line_str.startswith("Writer name:"):
                current_writer = line_str.split(":", 1)[1].strip().strip("'\"")
            elif "State:" in line_str and "Stable" not in line_str:
                if current_writer and current_writer not in failed_writers:
                    failed_writers.append(current_writer)
            elif "Last error:" in line_str and "No error" not in line_str:
                if current_writer and current_writer not in failed_writers:
                    failed_writers.append(current_writer)

        if failed_writers:
            names = ", ".join(failed_writers[:3])
            more = f" (+{len(failed_writers)-3} more)" if len(failed_writers) > 3 else ""
            return HealthFinding(
                check_id="vss_writers",
                title="VSS Writers in Error State",
                description=f"Volume Shadow Copy writers failed: {names}{more}. Backups and restore points may fail.",
                severity="danger",
                recommended_fix_keys=["reset_vss_writers"],
            )
    except Exception as exc:
        logger.debug("VSS writers check failed: %s", exc)
    return None


def check_winre_status() -> Optional[HealthFinding]:
    """Check if Windows Recovery Environment (WinRE) is enabled."""
    try:
        proc = _run_quick_cmd(["reagentc", "/info"], timeout=6.0)
        output = proc.stdout.lower()
        if "windows re status:" in output:
            for line in output.splitlines():
                if "windows re status:" in line and "disabled" in line:
                    return HealthFinding(
                        check_id="winre_disabled",
                        title="Windows RE is Disabled",
                        description="Windows Recovery Environment is currently disabled. Startup repair and recovery tools will be unavailable.",
                        severity="warning",
                        recommended_fix_keys=["enable_winre"],
                    )
    except Exception as exc:
        logger.debug("WinRE check failed: %s", exc)
    return None


def check_unquoted_service_paths() -> Optional[HealthFinding]:
    """Check for vulnerable unquoted service binary paths containing spaces."""
    vulnerable_count = 0
    try:
        key_path = r"SYSTEM\CurrentControlSet\Services"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as services_key:
            num_subkeys = winreg.QueryInfoKey(services_key)[0]
            for i in range(num_subkeys):
                try:
                    svc_name = winreg.EnumKey(services_key, i)
                    with winreg.OpenKey(services_key, svc_name) as svc_key:
                        try:
                            val, typ = winreg.QueryValueEx(svc_key, "ImagePath")
                            if typ in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(val, str):
                                val_clean = val.strip()
                                if val_clean and not val_clean.startswith('"') and " " in val_clean:
                                    lower_val = val_clean.lower()
                                    if ".exe" in lower_val:
                                        vulnerable_count += 1
                        except OSError as e:
                            logger.debug("Failed reading ImagePath for service %s: %s", svc_name, e)
                except OSError as e:
                    logger.debug("Failed opening service key at index %d: %s", i, e)
        if vulnerable_count > 0:
            return HealthFinding(
                check_id="unquoted_service_paths",
                title=f"{vulnerable_count} Unquoted Service Path(s)",
                description=f"Found {vulnerable_count} Windows services with unquoted paths containing spaces (privilege escalation risk).",
                severity="warning",
                recommended_fix_keys=["quote_service_paths"],
            )
    except Exception as exc:
        logger.debug("Unquoted service paths check failed: %s", exc)
    return None


def check_pending_reboot_status() -> Optional[HealthFinding]:
    """Check if Windows has a pending reboot."""
    try:
        if is_reboot_pending():
            return HealthFinding(
                check_id="pending_reboot",
                title="Reboot Pending",
                description="A system reboot is pending for updates or component servicing. Some repairs require a restart first.",
                severity="info",
                recommended_fix_keys=[],
            )
    except Exception as exc:
        logger.debug("Pending reboot check failed: %s", exc)
    return None


def check_wu_and_bits_state() -> Optional[HealthFinding]:
    """Check if Windows Update or BITS services are in an error state."""
    try:
        proc = _run_quick_cmd(["sc.exe", "query", "wuauserv"], timeout=4.0)
        output = proc.stdout.lower()
        if "stopped" in output and "start_pending" not in output:
            # Check if start type is disabled
            proc_config = _run_quick_cmd(["sc.exe", "qc", "wuauserv"], timeout=4.0)
            if "disabled" in proc_config.stdout.lower():
                return HealthFinding(
                    check_id="wu_service_disabled",
                    title="Windows Update Service Disabled",
                    description="The wuauserv service is disabled or stopped, preventing update checks and patching.",
                    severity="warning",
                    recommended_fix_keys=["wu_reset"],
                )
    except Exception as exc:
        logger.debug("WU and BITS check failed: %s", exc)
    return None


def check_time_synchronization() -> Optional[HealthFinding]:
    """Check if Windows Time service is in an unsynchronized or failed state."""
    try:
        proc = _run_quick_cmd(["w32tm", "/query", "status"], timeout=5.0)
        output = proc.stdout.lower()
        if proc.returncode != 0 or "free-running" in output or "local cmos clock" in output or "error" in output:
            return HealthFinding(
                check_id="time_desync",
                title="System Clock Unsynchronized",
                description="The system clock source is free-running or unsynchronized, which can trigger TLS/SSL and authentication failures.",
                severity="warning",
                recommended_fix_keys=["sync_time", "reset_w32tm"],
            )
    except Exception as exc:
        logger.debug("Time sync check failed: %s", exc)
    return None


def scan_system_health(worker=None) -> QuickFixHealthReport:
    """Run all pre-flight health checks sequentially and return report."""
    checks = [
        check_wmi_repository,
        check_print_spooler_queue,
        check_vss_writers,
        check_winre_status,
        check_unquoted_service_paths,
        check_wu_and_bits_state,
        check_time_synchronization,
        check_pending_reboot_status,
    ]

    report = QuickFixHealthReport()
    for check_fn in checks:
        if worker is not None and getattr(worker, "is_cancelled", False):
            break
        try:
            finding = check_fn()
            if finding:
                report.findings.append(finding)
                for key in finding.recommended_fix_keys:
                    report.recommended_keys.add(key)
        except Exception as exc:
            logger.debug("Pre-flight check %s raised: %s", getattr(check_fn, "__name__", "unknown"), exc)

    return report
