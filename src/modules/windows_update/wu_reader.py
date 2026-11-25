"""Windows Update reader and deep analytics engine. Qt-free.

Provides structured grouping by KB article, install duration extraction,
failure frequency analysis, and actionable remediation for Windows Update error codes.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.types import LogEntry
from core.windows_utils import system_root
from core.wu_error_codes import decode_wu_error
from modules.windows_update.wu_parser import WUParser

logger = logging.getLogger(__name__)

DEFAULT_WU_LOG_PATH = os.path.join(system_root(), "SoftwareDistribution", "ReportingEvents.log")

_KB_PATTERN = re.compile(r"\bKB\d{4,8}\b", re.IGNORECASE)
_NAMED_UPDATE = re.compile(
    r"following update(?: with error 0x[0-9a-fA-F]+)?:\s*(.+?)\.?\s*$",
    re.IGNORECASE,
)
_HRESULT_RE = re.compile(r"\b0[xX]([0-9a-fA-F]{8})\b")
_STORE_APP_PATTERN = re.compile(r"^9[A-Z0-9]{11}-")

#: Curated causes and actionable remediation for common Windows Update HRESULT codes.
WU_REMEDIATION_MAP: Dict[int, Tuple[str, str]] = {
    0x800F081F: (
        "Component store files are missing or corrupt (CBS_E_SOURCE_MISSING).",
        "Run 'DISM /Online /Cleanup-Image /RestoreHealth' followed by 'sfc /scannow'. If DISM fails, provide an official ISO/WIM image as the repair source.",
    ),
    0x800F0820: (
        "A servicing operation or prerequisite restart is pending (CBS_E_PENDING).",
        "Restart the computer to complete pending servicing transactions, then retry Windows Update.",
    ),
    0x800F0906: (
        "CBS source files could not be downloaded from Windows Update or WSUS (CBS_E_DOWNLOAD_FAILURE).",
        "Verify network connectivity and proxy settings. Ensure Windows Update service can contact Microsoft servers or use offline media.",
    ),
    0x800F0922: (
        "CBS installation failed, typically due to insufficient EFI/System Reserved partition space or VPN interference (CBS_E_INSTALLERS_FAILED).",
        "Verify that the System Reserved/EFI partition has at least 50 MB free. Disconnect VPNs or third-party firewalls and retry.",
    ),
    0x800F0988: (
        "Delta compression mismatch in servicing package (PSFX_E_INVALID_DELTA_COMBINATION).",
        "Run 'dism /online /cleanup-image /startcomponentcleanup', reboot, and retry.",
    ),
    0x800F0990: (
        "Reverse and forward delta application failed during package installation.",
        "Repair the component store using 'DISM /Online /Cleanup-Image /RestoreHealth' and reboot.",
    ),
    0x800F0991: (
        "Forward delta application failed; component store corrupted (PSFX_E_APPLY_FORWARD_DELTA_FAILED).",
        "Run 'DISM /Online /Cleanup-Image /RestoreHealth' and 'sfc /scannow' before retrying.",
    ),
    0x800F0992: (
        "Missing forward delta required for installation (PSFX_E_MISSING_FORWARD_DELTA).",
        "Download and manually install the full standalone cumulative update package (.msu) from Microsoft Update Catalog.",
    ),
    0x800F0993: (
        "Reverse delta application failed during servicing rollback (PSFX_E_APPLY_REVERSE_DELTA_FAILED).",
        "Check %windir%\\Logs\\CBS\\CBS.log for corrupted components and run 'sfc /scannow'.",
    ),
    0x80240016: (
        "Installation not allowed: another setup is running or a reboot is pending (WU_E_INSTALL_NOT_ALLOWED).",
        "Allow current installations to complete, or reboot the system before retrying.",
    ),
    0x80240017: (
        "The update is not applicable to this system architecture or OS build (WU_E_NOT_APPLICABLE).",
        "Confirm that this update matches your OS build and CPU architecture. Hide or decline if irrelevant.",
    ),
    0x80240020: (
        "Automatic updates cannot proceed without interactive user consent (WU_E_NO_INTERACTIVE_USER).",
        "Open Windows Update in Settings and click 'Install now'.",
    ),
    0x80240022: (
        "All updates in the batch failed to install (WU_E_ALL_UPDATES_FAILED).",
        "Review CBS.log. Clear SoftwareDistribution cache and retry installing updates individually.",
    ),
    0x8024002E: (
        "Access to Windows Update is disabled by Group Policy or registry policy (WU_E_WU_DISABLED).",
        "Verify Windows Update policies in gpedit.msc under Computer Configuration > Administrative Templates > Windows Components > Windows Update.",
    ),
    0x80240034: (
        "Download failed (WU_E_DOWNLOAD_FAILED).",
        "Restart BITS and Windows Update services ('net stop wuauserv && net start wuauserv'). Check internet and DNS.",
    ),
    0x80240438: (
        "Windows Update agent could not contact the update service.",
        "Check network connection, proxy configuration, and firewall rules.",
    ),
    0x8024200B: (
        "The update's internal installer executable failed (WU_E_UH_INSTALLERFAILURE).",
        "Check CBS.log and DISM.log for the sub-installer exit code; try installing standalone MSU package.",
    ),
    0x8024200D: (
        "The update handler requires an additional download (WU_E_UH_NEEDANOTHERDOWNLOAD).",
        "Restart the Windows Update client and ensure sufficient disk space on drive C:.",
    ),
    0x80244019: (
        "Update server returned HTTP 404 Not Found (WU_E_PT_HTTP_STATUS_NOT_FOUND).",
        "If using WSUS, synchronize update definitions. Otherwise, verify DNS and WSUS registry settings.",
    ),
    0x80244022: (
        "Update server returned HTTP 503 Service Temporarily Unavailable (WU_E_PT_HTTP_STATUS_SERVICE_UNAVAIL).",
        "Microsoft CDN or WSUS application pool is overloaded. Retry in a few hours.",
    ),
    0x8024402C: (
        "Server hostname could not be resolved (WU_E_PT_SOAPCLIENT_CONNECT).",
        "Check DNS servers, network adapter settings, and proxy configuration.",
    ),
    0x80248007: (
        "Local Windows Update datastore is corrupted (WU_E_DS_DATANOTFOUND).",
        "Reset Windows Update components: stop wuauserv and cryptSvc, rename SoftwareDistribution and Catroot2, then restart services.",
    ),
    0x80070005: (
        "Access denied while modifying files or registry keys (ERROR_ACCESS_DENIED).",
        "Ensure Windows Update runs under SYSTEM account; verify antivirus is not locking registry keys.",
    ),
    0x80070020: (
        "File is locked by another running process (ERROR_SHARING_VIOLATION).",
        "Close running applications, reboot into clean boot if necessary, and retry.",
    ),
    0x80070570: (
        "Downloaded update files or file system corrupted (ERROR_FILE_CORRUPT).",
        "Run 'chkdsk %SystemDrive% /f' and delete cached downloads in %windir%\\SoftwareDistribution\\Download.",
    ),
    0x800705B4: (
        "Installation operation timed out waiting for device or service response (ERROR_TIMEOUT).",
        "Disconnect extraneous USB devices and external peripherals; check driver installations.",
    ),
    0x80070643: (
        "Fatal installation error in MSI/MSU or Windows Recovery Environment (ERROR_INSTALL_FAILURE).",
        "If installing WinRE security update (e.g. KB5034441), expand Recovery Partition by at least 250 MB. Otherwise run .NET Repair Tool.",
    ),
    0x80073D02: (
        "The package cannot be installed because resources it modifies are in use (ERROR_PACKAGES_IN_USE).",
        "Close the target application (check Task Manager for background processes) and retry.",
    ),
    0x80D02002: (
        "Delivery Optimization download timed out (DO_E_DOWNLOAD_NO_PROGRESS).",
        "Temporarily disable Delivery Optimization peer-to-peer downloading in Settings > Windows Update > Advanced options.",
    ),
}

DEFAULT_REMEDIATION = (
    "Inspect %windir%\\Logs\\CBS\\CBS.log for details, or manually download and install the update from the Microsoft Update Catalog."
)


@dataclass
class KBUpdateGroup:
    """Group of update events associated with a KB article or standalone update."""
    kb: str                                      # e.g. "KB5025239" or "(No KB)"
    title: str                                   # Friendly title or identifier
    status: str                                  # "Installed", "Failed", "Pending"
    install_count: int = 0                       # Successful installations recorded
    failure_count: int = 0                       # Failed installations recorded
    durations: List[float] = field(default_factory=list)  # Durations in seconds
    avg_duration: Optional[float] = None         # Mean install duration in seconds
    last_error_code: Optional[int] = None        # Most recent error code if any
    error_cause: str = ""                        # Root cause description
    remediation: str = ""                        # Actionable fix advice
    update_ids: List[str] = field(default_factory=list)   # Unique update GUIDs seen
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    is_store_app: bool = False


def extract_kb(text: str) -> str:
    """Extract KB number (e.g. 'KB5025239') from text, normalized uppercase."""
    m = _KB_PATTERN.search(text or "")
    return m.group(0).upper() if m else ""


def extract_error_code(entry: LogEntry) -> Optional[int]:
    """Retrieve error HRESULT from parsed raw dictionary or message text."""
    raw = entry.raw if isinstance(entry.raw, dict) else {}
    code = raw.get("hresult")
    if isinstance(code, int) and code & 0x80000000:
        return code & 0xFFFFFFFF
    for m in _HRESULT_RE.finditer(entry.message or ""):
        val = int(m.group(1), 16)
        if val & 0x80000000:
            return val & 0xFFFFFFFF
    return None


def get_error_remediation(code: Optional[int]) -> Tuple[str, str]:
    """Return (cause, actionable_remediation) for a Windows Update error code."""
    if code is None:
        return "", ""
    code_norm = code & 0xFFFFFFFF
    if code_norm in WU_REMEDIATION_MAP:
        return WU_REMEDIATION_MAP[code_norm]

    # Check decoded meaning from core.wu_error_codes
    decoded = decode_wu_error(code_norm)
    if " - " in decoded:
        return decoded.split(" - ", 1)[1], DEFAULT_REMEDIATION
    return "", ""


def _entry_update_id(entry: LogEntry) -> str:
    raw = entry.raw if isinstance(entry.raw, dict) else {}
    return str(raw.get("update_id") or "").strip()


def _entry_title(entry: LogEntry) -> str:
    m = _NAMED_UPDATE.search(entry.message or "")
    if m:
        return m.group(1).strip()
    raw = entry.raw if isinstance(entry.raw, dict) else {}
    return str(raw.get("product") or "").strip()


def extract_install_durations(entries: List[LogEntry]) -> Dict[str, List[float]]:
    """Extract installation durations in seconds, grouped by update identifier/KB.

    Matches install start events with subsequent completion events (success/failure)
    for the same update_id or title.
    """
    sorted_entries = sorted(entries, key=lambda e: e.timestamp)
    # First pass: map uid to kb
    uid_to_kb: Dict[str, str] = {}
    for e in sorted_entries:
        uid = _entry_update_id(e)
        raw = e.raw if isinstance(e.raw, dict) else {}
        kb = extract_kb(e.message) or extract_kb(str(raw.get("product") or ""))
        if uid and kb and uid not in uid_to_kb:
            uid_to_kb[uid] = kb

    active_starts: Dict[str, datetime] = {}
    durations: Dict[str, List[float]] = {}

    for e in sorted_entries:
        raw = e.raw if isinstance(e.raw, dict) else {}
        event_name = str(raw.get("event_name") or "")
        event_id = raw.get("event_id")
        uid = _entry_update_id(e)
        kb = extract_kb(e.message) or extract_kb(str(raw.get("product") or "")) or uid_to_kb.get(uid, "")
        key = uid if uid else (kb if kb else _entry_title(e))
        if not key:
            continue

        is_start = (
            "INSTALLING_STARTED" in event_name
            or event_id == 181
            or "installation started" in (e.message or "").lower()
        )
        is_end = (
            "INSTALLING_SUCCEEDED" in event_name
            or "INSTALLING_FAILED" in event_name
            or event_id in (182, 183)
            or "installation successful" in (e.message or "").lower()
            or "installation failure" in (e.message or "").lower()
        )

        if is_start:
            active_starts[key] = e.timestamp
        elif is_end and key in active_starts:
            start_time = active_starts.pop(key)
            duration = (e.timestamp - start_time).total_seconds()
            if duration >= 0:
                d_val = round(duration, 1)
                durations.setdefault(key, []).append(d_val)
                if kb and kb != key:
                    durations.setdefault(kb, []).append(d_val)

    return durations


def extract_failure_frequencies(entries: List[LogEntry]) -> Dict[str, int]:
    """Calculate failure count per KB or update identifier."""
    counts: Dict[str, int] = {}
    for e in entries:
        if e.level == "Error" or (e.raw and str(e.raw.get("result", "")).lower() in ("failed", "failure")):
            kb = extract_kb(e.message) or extract_kb(str((e.raw or {}).get("product") or ""))
            uid = _entry_update_id(e)
            title = _entry_title(e)
            key = kb or title or (f"update {uid}" if uid else "Unknown Update")
            counts[key] = counts.get(key, 0) + 1
    return counts


def group_updates_by_kb(entries: List[LogEntry]) -> Dict[str, KBUpdateGroup]:
    """Group all update records by KB article (or fallback update name).

    Distinguishes successfully installed updates from failed or pending ones,
    extracts install durations, failure counts, and actionable remediations.
    """
    sorted_entries = sorted(entries, key=lambda e: e.timestamp)
    durations_by_key = extract_install_durations(entries)

    # First pass: map update_ids to titles & KBs
    id_to_kb: Dict[str, str] = {}
    id_to_title: Dict[str, str] = {}
    for e in sorted_entries:
        uid = _entry_update_id(e)
        title = _entry_title(e)
        kb = extract_kb(e.message) or extract_kb(title)
        if uid:
            if kb and uid not in id_to_kb:
                id_to_kb[uid] = kb
            if title and uid not in id_to_title:
                id_to_title[uid] = title

    groups: Dict[str, KBUpdateGroup] = {}

    for e in sorted_entries:
        uid = _entry_update_id(e)
        title = _entry_title(e) or id_to_title.get(uid, "")
        kb = extract_kb(e.message) or extract_kb(title) or id_to_kb.get(uid, "")

        # Grouping key: Prefer KB if available, else friendly title, else update ID
        group_key = kb if kb else (title if title else (f"Update {uid}" if uid else "Miscellaneous"))
        friendly_title = title if title else (group_key if group_key != kb else f"Update {kb}")

        if group_key not in groups:
            is_store = bool(_STORE_APP_PATTERN.match(friendly_title))
            groups[group_key] = KBUpdateGroup(
                kb=kb if kb else "(No KB)",
                title=friendly_title,
                status="Pending",
                is_store_app=is_store,
            )

        group = groups[group_key]
        if uid and uid not in group.update_ids:
            group.update_ids.append(uid)

        if group.first_seen is None or e.timestamp < group.first_seen:
            group.first_seen = e.timestamp
        if group.last_seen is None or e.timestamp > group.last_seen:
            group.last_seen = e.timestamp

        raw = e.raw if isinstance(e.raw, dict) else {}
        event_name = str(raw.get("event_name") or "")
        event_id = raw.get("event_id")
        result = str(raw.get("result") or "").lower()
        err_code = extract_error_code(e)

        is_success = (
            event_name == "AGENT_INSTALLING_SUCCEEDED"
            or event_id == 183
            or result in ("success", "succeeded")
            or "successfully installed" in (e.message or "").lower()
        )
        is_failure = (
            event_name in ("AGENT_INSTALLING_FAILED", "AGENT_DOWNLOAD_FAILED")
            or event_id == 182
            or result in ("failure", "failed")
            or e.level == "Error"
            or (err_code is not None and bool(err_code & 0x80000000))
        )

        if is_success:
            group.install_count += 1
            group.status = "Installed"
        elif is_failure:
            group.failure_count += 1
            if err_code:
                group.last_error_code = err_code
                cause, remediation = get_error_remediation(err_code)
                group.error_cause = cause
                group.remediation = remediation

            # Determine whether this failure indicates a pending reboot/servicing
            if err_code in (0x800F0820, 0x80240016) or "restart is pending" in (group.error_cause or "").lower():
                group.status = "Pending"
            elif group.status != "Installed":
                group.status = "Failed"

    # Attach durations and compute averages
    for key, group in groups.items():
        durations = durations_by_key.get(key) or durations_by_key.get(group.kb) or []
        if not durations and group.update_ids:
            for uid in group.update_ids:
                if uid in durations_by_key:
                    durations.extend(durations_by_key[uid])
        group.durations = durations
        if durations:
            group.avg_duration = round(sum(durations) / len(durations), 1)

    return groups


def read_wu_log(log_path: Optional[str] = None) -> List[LogEntry]:
    """Parse Windows Update ReportingEvents.log using WUParser."""
    path = log_path or DEFAULT_WU_LOG_PATH
    parser = WUParser(path)
    if not parser.file_exists():
        raise FileNotFoundError(f"{path} does not exist on this machine")
    return parser.parse()
