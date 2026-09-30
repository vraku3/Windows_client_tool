# src/modules/disk_health/physical_disks.py
"""Physical-disk inventory, cross-referenced against what the SMART scan saw.

`disk_health_module.py`'s SMART reader enumerates `Win32_DiskDrive`. Measured
live on this machine (2026-09-30): that enumeration lists indexes 1, 3, 4, 5, 6
-- five entries, two of them the same USB card reader reporting twice under
different indexes -- and never mentions the Samsung SSD 990 PRO 4TB or the
ADATA LEGEND 960 at all. Both are real NVMe drives physically installed;
both are members of a Storage Spaces pool, and `Win32_DiskDrive` omits every
pool member. `Get-PhysicalDisk` is the API that lists all four real disks
(device ids 0, 1, 2, 6) -- confirmed live, unelevated, no admin needed to see
the gap. Without this cross-reference, a failing pool member is invisible to
this tab for as long as it stays pooled: the SMART scan would report "3
drives, all healthy" while two more physical disks -- one of them 4TB of real
data -- go completely unmentioned.

`Get-StoragePool` is what decides whether a disk is genuinely pooled, not
merely whether it appears under `Get-PhysicalDisk`: EVERY physical disk on
Windows belongs to the implicit "Primordial" pool (Storage Spaces' reservoir
of not-yet-allocated disks), so a naive "has any StoragePool" check would
flag every disk in the machine as pooled. Real membership means a
non-Primordial pool name -- confirmed live for exactly two of the four disks
here (the two `Win32_DiskDrive` cannot see), the other two (the boot NVMe and
a virtual disk) show only "Primordial".

A refused read is never collapsed into "no disks": `Get-PhysicalDisk` needing
the Storage cmdlets refused, or the subprocess itself failing to run, both
report `available=False` with the reason, distinct from a clean read that
legitimately found nothing.
"""
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class PhysicalDiskInfo:
    device_id: str
    friendly_name: str
    serial: str
    media_type: str
    bus_type: str
    health_status: str
    operational_status: str
    size_gb: float
    pool_name: Optional[str]   # non-Primordial pool this disk is a real member of, or None


@dataclass
class PhysicalDiskScan:
    """The result of trying to read the physical-disk inventory.

    `available=False` means the read was refused or could not run at all --
    never collapsed into an empty `disks` list, which would read as "this
    machine has no physical disks."
    """
    available: bool
    reason: str
    disks: List[PhysicalDiskInfo] = field(default_factory=list)


_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-PhysicalDisk | ForEach-Object {
    $d = $_
    Write-Output "PDISK_START"
    Write-Output "DeviceId=$($d.DeviceId)"
    Write-Output "FriendlyName=$($d.FriendlyName)"
    Write-Output "SerialNumber=$($d.SerialNumber)"
    Write-Output "MediaType=$($d.MediaType)"
    Write-Output "BusType=$($d.BusType)"
    Write-Output "HealthStatus=$($d.HealthStatus)"
    Write-Output "OperationalStatus=$($d.OperationalStatus)"
    Write-Output "SizeBytes=$($d.Size)"
    try {
        $pools = $d | Get-StoragePool -ErrorAction Stop | Select-Object -ExpandProperty FriendlyName
        Write-Output "Pools=$([string]::Join('|', $pools))"
    } catch {
        Write-Output "Pools="
    }
    Write-Output "PDISK_END"
}
"""


def list_physical_disks() -> PhysicalDiskScan:
    """Run `Get-PhysicalDisk` and report every physical disk Windows knows
    about, including Storage Spaces pool members `Win32_DiskDrive` omits."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        logger.error("Get-PhysicalDisk could not be run: %s", e)
        return PhysicalDiskScan(available=False, reason=f"could not run PowerShell: {e}")

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        logger.warning("Get-PhysicalDisk refused: rc=%s stderr=%s", result.returncode, stderr)
        return PhysicalDiskScan(
            available=False,
            reason=f"Get-PhysicalDisk exited {result.returncode}"
                   + (f": {stderr[:200]}" if stderr else ""))

    disks = _parse_physical_disks(result.stdout)
    return PhysicalDiskScan(available=True, reason="", disks=disks)


def _parse_physical_disks(output: str) -> List[PhysicalDiskInfo]:
    disks: List[PhysicalDiskInfo] = []
    current: Optional[dict] = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line == "PDISK_START":
            current = {}
        elif line == "PDISK_END":
            if current is not None:
                disks.append(_build_physical_disk(current))
            current = None
        elif current is not None and "=" in line:
            key, _, val = line.partition("=")
            current[key.strip()] = val.strip()
    return disks


def _build_physical_disk(d: dict) -> PhysicalDiskInfo:
    pools = [p for p in d.get("Pools", "").split("|") if p]
    real_pools = [p for p in pools if p != "Primordial"]
    return PhysicalDiskInfo(
        device_id=d.get("DeviceId", ""),
        friendly_name=d.get("FriendlyName", "Unknown"),
        serial=d.get("SerialNumber", ""),
        media_type=d.get("MediaType", "Unknown"),
        bus_type=d.get("BusType", "Unknown"),
        health_status=d.get("HealthStatus", "Unknown"),
        operational_status=d.get("OperationalStatus", "Unknown"),
        size_gb=_bytes_to_gb(d.get("SizeBytes", "0")),
        pool_name=real_pools[0] if real_pools else None,
    )


def _bytes_to_gb(v: str) -> float:
    try:
        return round(int(v) / (1024 ** 3), 1)
    except (ValueError, TypeError):
        return 0.0


def hidden_from_smart(physical: Iterable[PhysicalDiskInfo],
                       smart_serials: Iterable[str]) -> List[PhysicalDiskInfo]:
    """Physical disks the Win32_DiskDrive-based SMART scan never mentioned.

    Matched by serial number, the one identifier both APIs report in the
    same format (confirmed live: `Win32_DiskDrive.SerialNumber` and
    `Get-PhysicalDisk.SerialNumber` are byte-for-byte identical strings on
    this machine, trailing dot included) -- never by index or name, which
    the two APIs number and title differently for the same physical disk.
    """
    known: Set[str] = {
        s.strip() for s in smart_serials
        if s and s.strip() and s.strip() != "—"
    }
    return [d for d in physical if d.serial.strip() not in known]
