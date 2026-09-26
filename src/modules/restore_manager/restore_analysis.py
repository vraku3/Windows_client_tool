"""System Restore analysis (Qt-free): points with ages and gaps, shadow-copy
storage, per-volume protection, and the findings computed from them.

A read that fails is an exception or an explicit ``None``/error string, never
an empty list: "could not ask" and "there are no restore points" must stay
different sentences.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.parse
import winreg
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Set, Tuple


logger = logging.getLogger(__name__)

POINT_TYPES = {
    0: "Application install", 1: "Application uninstall", 6: "Restore",
    7: "Checkpoint", 10: "Device driver install", 11: "First run",
    12: "Modify settings", 13: "Cancelled operation", 14: "Backup recovery",
}
_TYPE_BY_NAME = {"APPLICATION_INSTALL": 0, "APPLICATION_UNINSTALL": 1,
                 "DEVICE_DRIVER_INSTALL": 10, "MODIFY_SETTINGS": 12,
                 "CANCELLED_OPERATION": 13}

GB = 1024 ** 3
# Windows creates an automatic point at most once per this many minutes unless
# the SystemRestorePointCreationFrequency value says otherwise.
DEFAULT_FREQUENCY_MINUTES = 1440


@dataclass
class PointInfo:
    sequence: Optional[int]
    description: str
    kind: str
    created: Optional[datetime]
    age_days: Optional[float] = None
    gap_days: Optional[float] = None       # since the previous point


@dataclass
class ShadowStorage:
    volume: str                # "C:" or the raw volume path
    used: Optional[int] = None
    allocated: Optional[int] = None
    maximum: Optional[int] = None          # None with unbounded=True means no limit
    unbounded: bool = False

    @property
    def used_of_max_percent(self) -> Optional[float]:
        if self.unbounded or not self.maximum or self.used is None:
            return None
        return 100.0 * self.used / self.maximum


@dataclass
class Protection:
    """Volumes with System Protection on, straight from the SPP registry key."""
    mounts: List[str] = field(default_factory=list)      # ["C:"]
    volume_ids: List[str] = field(default_factory=list)


@dataclass
class Finding:
    severity: str
    title: str
    detail: str


# --------------------------------------------------------------------------
# Restore points
# --------------------------------------------------------------------------

def _point_kind(raw) -> str:
    try:
        return POINT_TYPES.get(int(raw), f"Type {int(raw)}")
    except (TypeError, ValueError):
        text = str(raw or "").upper()
        if text in _TYPE_BY_NAME:
            return POINT_TYPES[_TYPE_BY_NAME[text]]
        return str(raw) if raw not in (None, "") else "Unknown"


_DMTF = re.compile(r"^(\d{14})(?:\.\d+)?([+-]\d{3})?$")


def dmtf_to_local(value) -> Optional[datetime]:
    """WMI datetime ('20260923113445.459238-000') -> naive LOCAL time.

    The trailing +/-UUU is the offset in minutes and is honoured.  Restore
    points are stamped in UTC ('-000'), so reading the digits as local time
    is wrong by the machine's UTC offset: measured here, three hours -- the
    point stamped 11:34:45 has a shadow copy created at 14:34:55 local.
    """
    m = _DMTF.match(str(value or "").strip())
    if not m:
        return None
    try:
        naive = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    offset = int(m.group(2)) if m.group(2) else 0
    utc = (naive - timedelta(minutes=offset)).replace(tzinfo=timezone.utc)
    return utc.astimezone().replace(tzinfo=None)


def analyze_points(raw_points: Sequence[dict], now: Optional[datetime] = None) -> List[PointInfo]:
    """Oldest first, each with its age and the gap since the point before it."""
    now = now or datetime.now()
    infos: List[PointInfo] = []
    for pt in raw_points:
        try:
            seq: Optional[int] = int(pt.get("SequenceNumber"))
        except (TypeError, ValueError):
            logger.debug("restore point without a usable sequence number: %r", pt)
            seq = None
        created = dmtf_to_local(pt.get("CreationTime"))
        infos.append(PointInfo(
            sequence=seq,
            description=str(pt.get("Description") or "Unnamed restore point"),
            kind=_point_kind(pt.get("RestorePointType", pt.get("EventType"))),
            created=created,
            age_days=None if created is None else max(0.0, (now - created).total_seconds() / 86400),
        ))
    infos.sort(key=lambda p: (p.created or datetime.min, p.sequence or 0))
    prev: Optional[PointInfo] = None
    for p in infos:
        if prev and prev.created and p.created:
            p.gap_days = (p.created - prev.created).total_seconds() / 86400
        prev = p
    return infos


def sequences_older_than(points: Sequence[PointInfo], days: float) -> List[int]:
    """Sequence numbers older than ``days``, ALWAYS sparing the newest point.

    The newest point is the one you would restore to; a rule that could take
    it (every point is old) would leave nothing to roll back to.
    """
    usable = [p for p in points if p.sequence is not None]
    if len(usable) < 2:
        return []
    newest = max(usable, key=lambda p: (p.created or datetime.min, p.sequence or 0))
    return [p.sequence for p in usable
            if p is not newest and p.age_days is not None and p.age_days > days]


def verify_deleted(requested: Sequence[int], remaining: Sequence[PointInfo]) -> Tuple[List[int], List[int]]:
    """(gone, still_there): read back what Windows now lists."""
    present: Set[int] = {p.sequence for p in remaining if p.sequence is not None}
    gone = [s for s in requested if s not in present]
    return gone, [s for s in requested if s in present]


# --------------------------------------------------------------------------
# Shadow storage (vssadmin)
# --------------------------------------------------------------------------

_SIZE = re.compile(r"([\d.,]+)\s*(bytes|KB|MB|GB|TB|PB)", re.IGNORECASE)
_UNITS = {"BYTES": 1, "KB": 1024, "MB": 1024 ** 2, "GB": GB, "TB": 1024 ** 4, "PB": 1024 ** 5}


def parse_size(text: str) -> Optional[int]:
    """'35.9 GB (1%)' -> bytes.  None when there is no size in the text."""
    m = _SIZE.search(text or "")
    if not m:
        return None
    number = m.group(1).replace(",", "")
    try:
        return int(float(number) * _UNITS[m.group(2).upper()])
    except ValueError:
        logger.debug("unparseable size %r", text)
        return None


_FOR_VOLUME = re.compile(r"For volume:\s*\(([A-Za-z]:)\)|For volume:\s*(\S+)")


def parse_shadowstorage(output: str) -> Optional[List[ShadowStorage]]:
    """One entry per association.  None when vssadmin refused or said nothing
    parseable; [] only when it answered and listed no association."""
    if not output or not output.strip():
        return None
    low = output.lower()
    if "for volume" not in low:
        if "no shadow copy storage" in low or "no items found" in low or "no shadow copies" in low:
            return []
        return None
    entries: List[ShadowStorage] = []
    current: Optional[ShadowStorage] = None
    for line in output.splitlines():
        m = _FOR_VOLUME.search(line)
        if m:
            current = ShadowStorage(volume=m.group(1) or m.group(2))
            entries.append(current)
            continue
        if current is None or ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip().lower()
        if label.startswith("used shadow copy storage"):
            current.used = parse_size(value)
        elif label.startswith("allocated shadow copy storage"):
            current.allocated = parse_size(value)
        elif label.startswith("maximum shadow copy storage"):
            if "unbounded" in value.lower() or "no limit" in value.lower():
                current.unbounded = True
            else:
                current.maximum = parse_size(value)
    return entries


def _run(args: Sequence[str], timeout: int) -> Tuple[int, str, str]:
    p = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return p.returncode, p.stdout, p.stderr


def read_shadow_storage() -> Tuple[Optional[List[ShadowStorage]], str]:
    """(entries, error).  entries None means we could not find out."""
    try:
        rc, out, err = _run(["vssadmin", "list", "shadowstorage"], 30)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("vssadmin failed: %s", e)
        return None, f"vssadmin could not be run ({e})."
    parsed = parse_shadowstorage(out)
    if parsed is None:
        reason = (out.strip() or err.strip() or f"exit code {rc}").splitlines()[-1][:160]
        return None, f"vssadmin refused: {reason}. Shadow storage needs administrator rights."
    return parsed, ""


# --------------------------------------------------------------------------
# Protection state and frequency (registry; readable unelevated)
# --------------------------------------------------------------------------

_SPP_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SPP\Clients"
_SPP_VALUE = "{09F7EDC5-294E-4180-AF6A-FB0E6A0E9513}"
_SR_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore"
_SR_POLICY = r"SOFTWARE\Policies\Microsoft\Windows NT\SystemRestore"


def parse_protection(values: Sequence[str]) -> Protection:
    r"""Entries look like ``\\?\Volume{guid}\:(C%3A)``; the parenthesised part is
    the URL-encoded list of mount points (may be empty for a letterless
    volume, or hold several separated by commas)."""
    prot = Protection()
    for v in values:
        m = re.match(r"^(\\\\\?\\Volume\{[0-9a-fA-F-]+\}\\):\((.*)\)$", v.strip())
        if not m:
            logger.debug("unrecognised SPP entry %r", v)
            continue
        prot.volume_ids.append(m.group(1))
        for mount in urllib.parse.unquote(m.group(2)).split(","):
            mount = mount.strip().rstrip("\\")
            if mount:
                prot.mounts.append(mount.upper())
    return prot


def read_protection() -> Tuple[Optional[Protection], str]:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _SPP_KEY) as k:
            raw = winreg.QueryValueEx(k, _SPP_VALUE)[0]
    except FileNotFoundError:
        return Protection(), ""            # key absent: nothing is protected
    except OSError as e:
        logger.warning("SPP clients key unreadable: %s", e)
        return None, f"Could not read System Protection state ({e})."
    values = [raw] if isinstance(raw, str) else list(raw or [])
    return parse_protection(values), ""


def read_policy_disabled() -> Optional[bool]:
    """True when Group Policy switches System Restore off (DisableSR = 1)."""
    for key in (_SR_POLICY, _SR_KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                if int(winreg.QueryValueEx(k, "DisableSR")[0]) == 1:
                    return True
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            logger.warning("DisableSR unreadable under %s: %s", key, e)
            return None
    return False


def read_frequency_minutes() -> int:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _SR_KEY) as k:
            return int(winreg.QueryValueEx(k, "SystemRestorePointCreationFrequency")[0])
    except (OSError, ValueError):
        logger.debug("SystemRestorePointCreationFrequency absent; default applies")
        return DEFAULT_FREQUENCY_MINUTES


class RestoreReadError(RuntimeError):
    pass


def read_restore_points() -> List[dict]:
    """Raw points from Get-ComputerRestorePoint.  Raises when the read failed,
    so an empty list means Windows really listed none."""
    script = ("$ErrorActionPreference='Stop'; "
              "$p = @(Get-ComputerRestorePoint); "
              "$p | Select-Object SequenceNumber, Description, RestorePointType, CreationTime | "
              "ConvertTo-Json -Compress")
    try:
        rc, out, err = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 60)
    except (OSError, subprocess.SubprocessError) as e:
        raise RestoreReadError(f"PowerShell could not be run: {e}") from e
    if rc != 0:
        raise RestoreReadError((err.strip() or f"exit code {rc}").splitlines()[0][:200])
    if not out.strip():
        return []
    try:
        data = json.loads(out)
    except ValueError as e:
        raise RestoreReadError(f"unreadable restore point list: {e}") from e
    return [data] if isinstance(data, dict) else list(data)


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

def _system_mount() -> str:
    import os
    return os.environ.get("SystemDrive", "C:").upper()


def restore_findings(points: Sequence[PointInfo], protection: Optional[Protection],
                     storage: Optional[List[ShadowStorage]], storage_error: str,
                     policy_disabled: Optional[bool], frequency_minutes: int,
                     system_mount: Optional[str] = None) -> List[Finding]:
    out: List[Finding] = []
    system_mount = (system_mount or _system_mount()).upper()
    if policy_disabled:
        out.append(Finding("error", "System Restore is disabled by policy",
                           "DisableSR = 1: no restore points can be created."))
    if protection is None:
        out.append(Finding("info", "System Protection state could not be read", ""))
    elif system_mount not in protection.mounts:
        out.append(Finding("error", f"System Protection is OFF for {system_mount}",
                           "No restore points can be created for the system drive. "
                           "Turn it on in System Properties."))
    if not points:
        out.append(Finding("warning", "No restore points exist",
                           "Create one before risky changes (drivers, tweaks, registry edits)."))
    else:
        newest = max((p for p in points if p.created), key=lambda p: p.created, default=None)
        if newest and newest.age_days is not None and newest.age_days > 30:
            out.append(Finding("warning", f"Newest restore point is {newest.age_days:.0f} days old",
                               f"\"{newest.description}\"; nothing recent to roll back to."))
        big = [p for p in points if p.gap_days and p.gap_days > 60]
        if big:
            gap = max(big, key=lambda p: p.gap_days)
            out.append(Finding("info", f"Longest gap between points: {gap.gap_days:.0f} days",
                               f"Before \"{gap.description}\" on {gap.created:%Y-%m-%d}."))
    if frequency_minutes not in (0, DEFAULT_FREQUENCY_MINUTES) and frequency_minutes > 0:
        out.append(Finding("info", f"Points are throttled to one per {frequency_minutes} min",
                           "SystemRestorePointCreationFrequency is set; a second point inside that window is skipped."))
    elif frequency_minutes == DEFAULT_FREQUENCY_MINUTES:
        out.append(Finding("info", "Windows limits new points to one per 24 hours",
                           "A point created by a script inside that window is silently skipped "
                           "unless SystemRestorePointCreationFrequency is set to 0."))
    if storage is None:
        out.append(Finding("info", "Shadow storage could not be read", storage_error))
    else:
        for s in storage:
            pct = s.used_of_max_percent
            if pct is not None and pct >= 90:
                out.append(Finding("warning", f"Shadow storage on {s.volume} is {pct:.0f}% full",
                                   "Windows will delete the oldest points to make room."))
            if s.unbounded:
                out.append(Finding("info", f"Shadow storage on {s.volume} has no size limit",
                                   "Restore points can grow until the volume is full."))
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda f: order.get(f.severity, 3))


@dataclass
class VolumeProtection:
    mount: str
    protected: bool
    storage: Optional[ShadowStorage] = None


def protection_rows(mounts: Sequence[str], protection: Optional[Protection],
                    storage: Optional[List[ShadowStorage]]) -> List[VolumeProtection]:
    """One row per fixed drive: is System Protection on, and how much shadow
    storage it uses.  ``protected`` is False only when we READ the state;
    with ``protection`` None the caller shows "unknown" instead."""
    by_mount = {s.volume.upper(): s for s in (storage or [])}
    on = set(protection.mounts) if protection else set()
    return [VolumeProtection(m.upper(), m.upper() in on, by_mount.get(m.upper())) for m in mounts]


def format_bytes(n: Optional[int]) -> str:
    if n is None:
        return "n/a"
    return f"{n / GB:.1f} GB" if n >= GB else f"{n / 1024 ** 2:.0f} MB"


def points_to_markdown(points: Sequence[PointInfo], storage: Optional[List[ShadowStorage]],
                       findings: Sequence[Finding], host: str = "") -> str:
    lines = [f"### System Restore{': ' + host if host else ''}", "",
             "| Created | Type | Description | Age | Gap |", "|---|---|---|---|---|"]
    for p in reversed(points):
        created = f"{p.created:%Y-%m-%d %H:%M}" if p.created else "unknown"
        age = "n/a" if p.age_days is None else f"{p.age_days:.0f} d"
        gap = "-" if p.gap_days is None else f"{p.gap_days:.1f} d"
        lines.append(f"| {created} | {p.kind} | {p.description.replace('|', '/')} | {age} | {gap} |")
    if storage:
        lines += ["", "| Volume | Used | Allocated | Maximum |", "|---|---|---|---|"]
        for s in storage:
            mx = "unbounded" if s.unbounded else format_bytes(s.maximum)
            lines.append(f"| {s.volume} | {format_bytes(s.used)} | {format_bytes(s.allocated)} | {mx} |")
    lines += ["", "**Findings**", ""]
    lines += [f"- [{f.severity}] {f.title}. {f.detail}".rstrip() for f in findings] or ["- None."]
    return "\n".join(lines) + "\n"


def summary_counts(findings: Sequence[Finding]) -> Dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    return counts
