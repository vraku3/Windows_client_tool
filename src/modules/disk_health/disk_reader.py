"""Disk health engine: physical disks, reliability counters, volumes, TRIM,
partition alignment, BitLocker state, and the findings computed from them.

Qt-free.  The one I/O function is ``read_disk_report``; everything else takes
the parsed report so it tests with no machine attached.

A counter the drive or driver did not report is ``None`` and stays ``None``.
It is never shown as 0: on this machine's NVMe drives ``PowerOnHours`` comes
back empty unelevated, and "0 hours" would read as a brand-new disk.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from modules.disk_health import disk_events, physical_disks

logger = logging.getLogger(__name__)

# 4 KiB is the largest physical sector in common use; anything a filesystem
# lives on should start on a multiple of it (Windows uses 1 MiB by default).
ALIGNMENT = 4096

_NO_FILESYSTEM_TYPES = {"Reserved"}      # the 16 MB MSR legitimately sits at 17 KiB


@dataclass
class PhysicalDiskInfo:
    device_id: str
    name: str
    serial: str = ""
    media: str = ""             # SSD | HDD | Unspecified
    bus: str = ""               # NVMe | SATA | USB | File Backed Virtual ...
    health: str = ""            # Healthy | Warning | Unhealthy | ""
    operational: str = ""
    size_bytes: int = 0
    firmware: str = ""
    temperature: Optional[int] = None
    temperature_max: Optional[int] = None
    wear_percent: Optional[int] = None      # percentage of rated life USED
    power_on_hours: Optional[int] = None
    read_errors_uncorrected: Optional[int] = None
    write_errors_uncorrected: Optional[int] = None
    has_reliability: bool = False
    smart_attrs: list = field(default_factory=list)   # admin-only ATA table
    predicted_failure: Optional[bool] = None          # admin-only

    @property
    def is_virtual(self) -> bool:
        blob = f"{self.bus} {self.name}".lower()
        return "virtual" in blob

    @property
    def is_ssd(self) -> bool:
        return self.media.upper() == "SSD" and not self.is_virtual

    @property
    def size_text(self) -> str:
        return format_size(self.size_bytes)


@dataclass
class VolumeInfo:
    letter: str
    label: str = ""
    filesystem: str = ""
    health: str = ""
    size_bytes: int = 0
    free_bytes: int = 0
    bitlocker: Optional[str] = None      # "On" | "Off" | "Suspended" ... None = unread

    @property
    def free_percent(self) -> Optional[float]:
        if not self.size_bytes:
            return None
        return 100.0 * self.free_bytes / self.size_bytes

    @property
    def display(self) -> str:
        return f"{self.letter}:" if self.letter else (self.label or "(no letter)")


@dataclass
class PartitionInfo:
    disk: int
    number: int
    offset: int
    size: int
    kind: str = ""
    letter: str = ""

    @property
    def aligned(self) -> bool:
        return self.offset % ALIGNMENT == 0


@dataclass
class DiskReport:
    disks: List[PhysicalDiskInfo] = field(default_factory=list)
    volumes: List[VolumeInfo] = field(default_factory=list)
    partitions: List[PartitionInfo] = field(default_factory=list)
    trim_enabled: Dict[str, Optional[bool]] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)   # section -> reason
    elevated: bool = False
    events: List[disk_events.DiskEvent] = field(default_factory=list)
    hidden_pool_disks: List[physical_disks.PhysicalDiskInfo] = field(default_factory=list)


@dataclass
class Finding:
    severity: str        # "error" | "warning" | "info"
    subject: str         # what it is about ("Samsung SSD 990 PRO 2TB", "C:")
    title: str
    detail: str


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def format_size(n: Optional[int]) -> str:
    """Decimal (drive-label) units: a '2 TB' disk reads 2.0 TB, not 1.8 TiB."""
    if not n:
        return "-"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} PB"


def power_on_text(hours: Optional[int]) -> str:
    if hours is None:
        return "not reported"
    days = hours / 24
    if days >= 365:
        return f"{hours:,} h ({days / 365:.1f} years)"
    return f"{hours:,} h ({days:.0f} days)"


def power_on_hours_short(hours: Optional[int]) -> str:
    """Table form of power_on_text: None is 'n/a', never '0 h'."""
    if hours is None:
        return "n/a"
    return f"{hours / 8766:.1f} y" if hours >= 8766 else f"{hours:,} h"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

_BITLOCKER = {1: "On", 2: "Off", 3: "Unknown"}


def _opt_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.debug("not an integer: %r", value)
        return None


def _clean(value) -> str:
    """PowerShell renders a missing drive letter as a NUL character."""
    return str(value or "").replace("\x00", "").strip()


def parse_disk_json(text: str) -> DiskReport:
    data = json.loads(text)
    rep = DiskReport()
    for key in ("disks", "volumes", "partitions"):
        if f"{key}_error" in data:
            rep.errors[key] = str(data[f"{key}_error"])
    for d in data.get("disks") or []:
        rep.disks.append(PhysicalDiskInfo(
            device_id=_clean(d.get("DeviceId")),
            name=_clean(d.get("Name")) or "Unknown drive",
            serial=_clean(d.get("Serial")).rstrip("."),
            media=_clean(d.get("Media")),
            bus=_clean(d.get("Bus")),
            health=_clean(d.get("Health")),
            operational=_clean(d.get("Op")),
            size_bytes=_opt_int(d.get("Size")) or 0,
            firmware=_clean(d.get("Firmware")),
            temperature=_opt_int(d.get("Temp")),
            temperature_max=_opt_int(d.get("TempMax")) or None,
            wear_percent=_opt_int(d.get("Wear")),
            power_on_hours=_opt_int(d.get("Poh")),
            read_errors_uncorrected=_opt_int(d.get("ReadUnc")),
            write_errors_uncorrected=_opt_int(d.get("WriteUnc")),
            has_reliability=bool(d.get("HasRel")),
        ))
    for v in data.get("volumes") or []:
        bl = _opt_int(v.get("BitLocker"))
        rep.volumes.append(VolumeInfo(
            letter=_clean(v.get("Letter")),
            label=_clean(v.get("Label")),
            filesystem=_clean(v.get("Fs")),
            health=_clean(v.get("Health")),
            size_bytes=_opt_int(v.get("Size")) or 0,
            free_bytes=_opt_int(v.get("Free")) or 0,
            bitlocker=None if bl is None else _BITLOCKER.get(bl, f"state {bl}"),
        ))
    for p in data.get("partitions") or []:
        rep.partitions.append(PartitionInfo(
            disk=_opt_int(p.get("Disk")) or 0,
            number=_opt_int(p.get("Part")) or 0,
            offset=_opt_int(p.get("Offset")) or 0,
            size=_opt_int(p.get("Size")) or 0,
            kind=_clean(p.get("Type")),
            letter=_clean(p.get("Letter")),
        ))
    return rep


_TRIM_LINE = re.compile(r"^\s*(\w+)\s+DisableDeleteNotify\s*=\s*(\d)", re.MULTILINE)


def parse_trim(output: str) -> Dict[str, Optional[bool]]:
    """{'NTFS': True, 'ReFS': True}: True when TRIM is sent to the device.

    ``DisableDeleteNotify = 0`` means TRIM is ENABLED, which reads backwards.
    Nothing parseable -> empty dict (unknown), not "disabled".
    """
    return {fs: value == "0" for fs, value in _TRIM_LINE.findall(output or "")}


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$out = [ordered]@{}
try {
  $out.disks = @(Get-PhysicalDisk | ForEach-Object {
    $r = $null
    try { $r = $_ | Get-StorageReliabilityCounter } catch { $r = $null }
    [pscustomobject]@{
      DeviceId=$_.DeviceId; Name=$_.FriendlyName; Serial=$_.SerialNumber; Media=[string]$_.MediaType
      Bus=[string]$_.BusType; Health=[string]$_.HealthStatus; Op=(($_.OperationalStatus) -join ',')
      Size=[int64]$_.Size; Firmware=$_.FirmwareVersion
      Temp=$r.Temperature; TempMax=$r.TemperatureMax; Wear=$r.Wear; Poh=$r.PowerOnHours
      ReadUnc=$r.ReadErrorsUncorrected; WriteUnc=$r.WriteErrorsUncorrected
      HasRel=[bool]$r
    } })
} catch { $out.disks_error = $_.Exception.Message }
try {
  $sh = New-Object -ComObject Shell.Application
  $out.volumes = @(Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' } | ForEach-Object {
    $bl = $null
    if ($_.DriveLetter) {
      try { $bl = $sh.NameSpace("$($_.DriveLetter):").Self.ExtendedProperty('System.Volume.BitLockerProtection') } catch { $bl = $null }
    }
    [pscustomobject]@{ Letter=[string]$_.DriveLetter; Label=$_.FileSystemLabel; Fs=$_.FileSystem
      Health=[string]$_.HealthStatus; Size=[int64]$_.Size; Free=[int64]$_.SizeRemaining; BitLocker=$bl }
  })
} catch { $out.volumes_error = $_.Exception.Message }
try {
  $out.partitions = @(Get-Partition | ForEach-Object { [pscustomobject]@{
    Disk=$_.DiskNumber; Part=$_.PartitionNumber; Offset=[int64]$_.Offset; Size=[int64]$_.Size
    Letter=[string]$_.DriveLetter; Type=[string]$_.Type } })
} catch { $out.partitions_error = $_.Exception.Message }
$out | ConvertTo-Json -Depth 5 -Compress
"""


class DiskReadError(RuntimeError):
    """The whole read failed (PowerShell missing, timed out, unparseable)."""


def _run(args: Sequence[str], timeout: int) -> Tuple[int, str, str]:
    proc = subprocess.run(
        list(args), capture_output=True, text=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return proc.returncode, proc.stdout, proc.stderr


def read_disk_report(elevated: bool = False, timeout: int = 60) -> DiskReport:
    try:
        rc, out, err = _run(["powershell", "-NoProfile", "-NonInteractive",
                             "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT], timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise DiskReadError(f"PowerShell could not be run: {e}") from e
    if not out.strip():
        raise DiskReadError(f"PowerShell returned nothing (exit {rc}): {err.strip()[:200]}")
    try:
        rep = parse_disk_json(out)
    except (ValueError, TypeError) as e:
        raise DiskReadError(f"Unreadable disk data: {e}") from e
    rep.elevated = elevated
    try:
        _rc, fs_out, _e = _run(["fsutil", "behavior", "query", "DisableDeleteNotify"], 15)
        rep.trim_enabled = parse_trim(fs_out)
        if not rep.trim_enabled:
            rep.errors["trim"] = "fsutil gave no TRIM answer."
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("fsutil TRIM query failed: %s", e)
        rep.errors["trim"] = f"Could not query TRIM ({e})."
    if elevated:
        _attach_smart(rep)
    events = disk_events.read_disk_events()
    if events is None:
        rep.errors["events"] = "Could not read the System log for disk-related events."
    else:
        rep.events = events
    _attach_storage_spaces_crossref(rep)
    return rep


def _attach_storage_spaces_crossref(rep: DiskReport) -> None:
    """Physical disks Storage Spaces pools away from Win32_DiskDrive.

    A Storage Spaces pool member never appears in the SMART scan above --
    see physical_disks.py for the measured evidence -- so without this a
    failing pool member is invisible to this tab for as long as it stays
    pooled. A refused read is its own state (``rep.errors``), never
    collapsed into "no hidden disks".
    """
    try:
        scan = physical_disks.list_physical_disks()
    except Exception as e:  # subprocess/parsing can fail in many ways
        logger.warning("Storage Spaces cross-reference failed: %s", e)
        rep.errors["storage_spaces"] = f"Could not check for hidden pool members ({e})."
        return
    if not scan.available:
        rep.errors["storage_spaces"] = scan.reason
        return
    rep.hidden_pool_disks = physical_disks.hidden_from_smart(
        scan.disks, [d.serial for d in rep.disks])


def _attach_smart(rep: DiskReport) -> None:
    """ATA SMART tables and the failure-prediction flag (administrator only)."""
    from modules.disk_health import smart_reader
    try:
        smart = smart_reader._query_disks()
    except Exception as e:  # PowerShell / WMI can fail in many ways
        logger.warning("SMART attribute read failed: %s", e)
        rep.errors["smart"] = f"S.M.A.R.T. attribute read failed ({e})."
        return
    for s in smart:
        for d in rep.disks:
            if d.device_id == str(s.index):
                d.smart_attrs = list(s.smart_attrs)
                d.predicted_failure = s.status == "PREDICTED FAILURE"


# --------------------------------------------------------------------------
# Verdicts and findings
# --------------------------------------------------------------------------

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def temperature_limits(disk: PhysicalDiskInfo) -> Tuple[int, int]:
    """(warn, critical) in Celsius.  The drive's own maximum wins when given."""
    if disk.media.upper() == "HDD":
        warn, crit = 50, 60
    else:
        warn, crit = 70, 80
    if disk.temperature_max and disk.temperature_max > 40:
        crit = min(crit, disk.temperature_max)
        warn = min(warn, crit - 10)
    return warn, crit


def disk_findings(disk: PhysicalDiskInfo) -> List[Finding]:
    if disk.is_virtual:
        return []
    out: List[Finding] = []
    s = disk.name
    if disk.health and disk.health.lower() not in ("healthy", "unknown"):
        out.append(Finding("error", s, f"Windows reports the drive as {disk.health}",
                           "Back up its data now; replace the drive if it stays that way."))
    if disk.predicted_failure:
        out.append(Finding("error", s, "S.M.A.R.T. predicts failure",
                           "The drive's own firmware expects it to fail soon. Back up and replace."))
    if disk.operational and disk.operational.upper() not in ("OK", ""):
        out.append(Finding("warning", s, f"Operational status: {disk.operational}", ""))
    if disk.wear_percent is not None:
        if disk.wear_percent >= 90:
            out.append(Finding("error", s, f"{disk.wear_percent}% of rated life used",
                               "Near the end of its write endurance; plan a replacement."))
        elif disk.wear_percent >= 70:
            out.append(Finding("warning", s, f"{disk.wear_percent}% of rated life used",
                               "Getting worn; watch it and keep backups current."))
    if disk.temperature is not None and disk.temperature > 0:
        warn, crit = temperature_limits(disk)
        if disk.temperature >= crit:
            out.append(Finding("error", s, f"Running at {disk.temperature} C",
                               f"At or above the {crit} C critical level; check airflow and heatsink."))
        elif disk.temperature >= warn:
            out.append(Finding("warning", s, f"Running hot: {disk.temperature} C",
                               f"Above {warn} C; the drive may throttle under sustained load."))
    for label, val in (("read", disk.read_errors_uncorrected),
                       ("write", disk.write_errors_uncorrected)):
        if val:
            out.append(Finding("warning", s, f"{val} uncorrected {label} error(s)",
                               "The drive gave up on data it could not correct."))
    for a in disk.smart_attrs:
        if a.failing:
            out.append(Finding("error", s, f"S.M.A.R.T. attribute {a.name} is at its threshold",
                               f"Value {a.value} against threshold {a.threshold}."))
        elif a.id in (5, 197, 198) and str(a.raw).isdigit() and int(a.raw) > 0:
            out.append(Finding("warning", s, f"{a.name}: {a.raw}",
                               "Non-zero raw count; a rising number is the classic sign of a dying disk."))
    if disk.power_on_hours and disk.media.upper() == "HDD" and disk.power_on_hours > 43_800:
        out.append(Finding("info", s, f"{disk.power_on_hours / 8766:.1f} years powered on",
                           "Spinning disks past five years of use fail more often."))
    return out


def volume_findings(vol: VolumeInfo) -> List[Finding]:
    out: List[Finding] = []
    if vol.health and vol.health.lower() != "healthy":
        out.append(Finding("error", vol.display, f"Volume health: {vol.health}",
                           "Run chkdsk (read-only first) and check the disk's own health."))
    pct = vol.free_percent
    if pct is not None and vol.letter:
        if pct < 5:
            out.append(Finding("error", vol.display, f"Only {pct:.1f}% free",
                               f"{format_size(vol.free_bytes)} left; Windows and SSD wear both suffer."))
        elif pct < 10:
            out.append(Finding("warning", vol.display, f"{pct:.1f}% free",
                               f"{format_size(vol.free_bytes)} left."))
    return out


def alignment_findings(report: DiskReport) -> List[Finding]:
    virtual = {d.device_id for d in report.disks if d.is_virtual}
    names = {d.device_id: d.name for d in report.disks}
    out: List[Finding] = []
    for p in report.partitions:
        if p.kind in _NO_FILESYSTEM_TYPES or str(p.disk) in virtual or p.aligned:
            continue
        out.append(Finding("warning", names.get(str(p.disk), f"Disk {p.disk}"),
                           f"Partition {p.number} starts at byte {p.offset}",
                           "Not a multiple of 4096: every write may straddle two physical sectors."))
    return out


def system_findings(report: DiskReport) -> List[Finding]:
    out: List[Finding] = []
    has_ssd = any(d.is_ssd for d in report.disks)
    if has_ssd:
        for fs, on in report.trim_enabled.items():
            if on is False:
                out.append(Finding("warning", fs, "TRIM is disabled",
                                   "SSDs slow down without it: fsutil behavior set DisableDeleteNotify 0"))
    for vol in report.volumes:
        if vol.letter and vol.bitlocker == "Off" and _is_system_drive(vol):
            out.append(Finding("info", vol.display, "System drive is not BitLocker-protected",
                               "A lost or stolen machine exposes everything on it."))
    for section, reason in report.errors.items():
        out.append(Finding("info", section, f"Could not read {section}", reason))
    return out


def _is_system_drive(vol: VolumeInfo) -> bool:
    import os
    return vol.letter.upper() == os.environ.get("SystemDrive", "C:")[:1].upper()


def event_findings(report: DiskReport) -> List[Finding]:
    """One finding per (event id, disk) group, not per raw occurrence --
    a recurring warning can fire dozens of times in the read window, and a
    finding per occurrence would bury everything else on the pane."""
    out: List[Finding] = []
    for group in disk_events.group_events(report.events):
        subject = f"Disk {group.disk}" if group.disk else "System log"
        title = f"Event {group.event_id}" + (f" x{group.count}" if group.count > 1 else "")
        detail = group.meaning + f" Most recent: {group.latest}."
        out.append(Finding("error" if group.is_error else "warning", subject, title, detail))
    return out


def storage_spaces_findings(report: DiskReport) -> List[Finding]:
    out: List[Finding] = []
    for d in report.hidden_pool_disks:
        out.append(Finding(
            "warning", d.friendly_name,
            f'Not shown above -- pooled in "{d.pool_name}"',
            f"{d.size_gb:.0f} GB, health {d.health_status}. Storage Spaces pool "
            "members are not enumerated by the drives table's own scan, so "
            "this disk's health is only visible here."))
    return out


def all_findings(report: DiskReport) -> List[Finding]:
    found: List[Finding] = []
    for d in report.disks:
        found += disk_findings(d)
    for v in report.volumes:
        found += volume_findings(v)
    found += alignment_findings(report)
    found += storage_spaces_findings(report)
    found += system_findings(report)
    found += event_findings(report)
    return sorted(found, key=lambda f: _SEVERITY_ORDER.get(f.severity, 3))


def disk_verdict(disk: PhysicalDiskInfo) -> Tuple[str, str]:
    """(severity or "ok"/"unknown", one plain sentence)."""
    if disk.is_virtual:
        return "info", "Virtual disk; no physical health to report."
    problems = disk_findings(disk)
    if problems:
        top = min(problems, key=lambda f: _SEVERITY_ORDER.get(f.severity, 3))
        return top.severity, top.title
    if not disk.health:
        return "unknown", "Windows did not report a health status."
    if not disk.has_reliability:
        return "ok", "Healthy per Windows; no reliability counters reported."
    return "ok", "Healthy; nothing concerning in the counters the drive reports."


def report_to_markdown(report: DiskReport, host: str = "") -> str:
    lines = [f"### Disk health{': ' + host if host else ''}", ""]
    lines += ["| Drive | Bus | Size | Health | Temp | Life used | Power-on | Verdict |",
              "|---|---|---|---|---|---|---|---|"]
    for d in report.disks:
        if d.is_virtual:
            continue
        _sev, verdict = disk_verdict(d)
        temp = f"{d.temperature} C" if d.temperature else "n/a"
        wear = f"{d.wear_percent}%" if d.wear_percent is not None else "n/a"
        lines.append(f"| {d.name} | {d.bus} | {d.size_text} | {d.health or '?'} | {temp} | "
                     f"{wear} | {power_on_text(d.power_on_hours)} | {verdict} |")
    lines += ["", "| Volume | FS | Size | Free | BitLocker |", "|---|---|---|---|---|"]
    for v in report.volumes:
        if not v.letter:
            continue
        pct = v.free_percent
        lines.append(f"| {v.display} {v.label} | {v.filesystem} | {format_size(v.size_bytes)} | "
                     f"{format_size(v.free_bytes)} ({pct:.0f}%) | {v.bitlocker or 'unread'} |")
    findings = all_findings(report)
    lines += ["", "**Findings**", ""]
    lines += ([f"- [{f.severity}] {f.subject}: {f.title}. {f.detail}".rstrip() for f in findings]
              or ["- None."])
    return "\n".join(lines) + "\n"
