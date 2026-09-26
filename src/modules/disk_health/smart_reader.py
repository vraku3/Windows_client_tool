"""ATA S.M.A.R.T. attribute reader (needs administrator; Qt-free).

Moved out of the pane unchanged so the health engine can reuse it.
"""
import logging
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SmartAttribute:
    id: int
    name: str
    value: int       # normalised 0-255
    worst: int
    threshold: int
    raw: str
    failing: bool    # value <= threshold


@dataclass
class DiskInfo:
    index: int
    model: str
    serial: str
    size_gb: float
    interface: str   # e.g. SATA, NVMe, USB
    status: str      # OK / Pred. Fail / Unknown
    temperature: Optional[int]       # °C, may be None
    power_on_hours: Optional[int]
    reallocated_sectors: Optional[int]
    pending_sectors: Optional[int]
    smart_attrs: List[SmartAttribute] = field(default_factory=list)
    raw_output: str = ""


# ---------------------------------------------------------------------------
# WMI / PowerShell SMART reader
# ---------------------------------------------------------------------------

_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$disks = Get-WmiObject -Class Win32_DiskDrive
foreach ($d in $disks) {
    $smart = Get-WmiObject -Namespace root\wmi -Class MSStorageDriver_FailurePredictStatus `
             | Where-Object { $_.InstanceName -like "*$($d.PNPDeviceID.Replace('\','_'))*" }
    $data  = Get-WmiObject -Namespace root\wmi -Class MSStorageDriver_FailurePredictData `
             | Where-Object { $_.InstanceName -like "*$($d.PNPDeviceID.Replace('\','_'))*" }
    $thresh= Get-WmiObject -Namespace root\wmi -Class MSStorageDriver_FailurePredictThresholds `
             | Where-Object { $_.InstanceName -like "*$($d.PNPDeviceID.Replace('\','_'))*" }
    $predFail = if ($smart) { $smart.PredictFailure } else { $false }
    $sizeGB = [math]::Round($d.Size / 1GB, 1)
    Write-Output "DISK_START"
    Write-Output "Index=$($d.Index)"
    Write-Output "Model=$($d.Model)"
    Write-Output "Serial=$($d.SerialNumber)"
    Write-Output "SizeGB=$sizeGB"
    Write-Output "Interface=$($d.InterfaceType)"
    Write-Output "Status=$($d.Status)"
    Write-Output "PredFail=$predFail"
    if ($data -and $thresh) {
        $rawBytes   = $data.VendorSpecific
        $threshBytes= $thresh.VendorSpecific
        $offset = 2
        for ($i = 0; $i -lt 30; $i++) {
            $base = $offset + $i * 12
            if ($base + 12 -gt $rawBytes.Length) { break }
            $attrId = $rawBytes[$base]
            if ($attrId -eq 0) { continue }
            $val    = $rawBytes[$base + 3]
            $worst  = $rawBytes[$base + 4]
            $thr    = $threshBytes[$base + 1]
            $raw5   = [uint64]0
            for ($b = 5; $b -le 10; $b++) { $raw5 = $raw5 + ([uint64]$rawBytes[$base + $b] -shl (($b-5)*8)) }
            Write-Output "ATTR=$attrId,$val,$worst,$thr,$raw5"
        }
    }
    Write-Output "DISK_END"
}
"""

# Well-known SMART attribute names
_ATTR_NAMES = {
    1:   "Read Error Rate",
    3:   "Spin Up Time",
    4:   "Start/Stop Count",
    5:   "Reallocated Sectors",
    7:   "Seek Error Rate",
    9:   "Power-On Hours",
    10:  "Spin Retry Count",
    12:  "Power Cycle Count",
    177: "Wear Leveling Count",
    179: "Used Reserved Block Count",
    181: "Program Fail Count",
    182: "Erase Fail Count",
    183: "Runtime Bad Block",
    187: "Uncorrectable Error Count",
    190: "Airflow Temperature",
    194: "Temperature",
    195: "Hardware ECC Recovered",
    196: "Reallocation Event Count",
    197: "Pending Sector Count",
    198: "Uncorrectable Sector Count",
    199: "UltraDMA CRC Error Count",
    200: "Write Error Rate",
    231: "SSD Life Left",
    232: "Endurance Remaining",
    233: "Media Wearout Indicator",
    240: "Head Flying Hours",
    241: "Total LBAs Written",
    242: "Total LBAs Read",
}


def _query_disks() -> List[DiskInfo]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout
    except Exception as e:
        logger.error("SMART query failed: %s", e)
        return []

    disks: List[DiskInfo] = []
    current: Optional[dict] = None

    for line in output.splitlines():
        line = line.strip()
        if line == "DISK_START":
            current = {"attrs": [], "raw_lines": []}
        elif line == "DISK_END":
            if current is not None:
                disks.append(_build_disk(current))
            current = None
        elif current is not None:
            current["raw_lines"].append(line)
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if key == "ATTR":
                    parts = val.split(",")
                    if len(parts) == 5:
                        try:
                            attr_id = int(parts[0])
                            attr_val = int(parts[1])
                            attr_worst = int(parts[2])
                            attr_thr = int(parts[3])
                            attr_raw = str(int(parts[4]))
                            current["attrs"].append(SmartAttribute(
                                id=attr_id,
                                name=_ATTR_NAMES.get(attr_id, f"Attr {attr_id}"),
                                value=attr_val,
                                worst=attr_worst,
                                threshold=attr_thr,
                                raw=attr_raw,
                                failing=attr_val <= attr_thr and attr_thr > 0,
                            ))
                        except ValueError:
                            logger.debug("Ignored ValueError", exc_info=True)
                else:
                    current[key] = val

    return disks


def _build_disk(d: dict) -> DiskInfo:
    attrs: List[SmartAttribute] = d.get("attrs", [])
    temp = next((a.raw for a in attrs if a.id == 194), None)
    if temp is None:
        temp = next((a.raw for a in attrs if a.id == 190), None)
    poh = next((a.raw for a in attrs if a.id == 9), None)
    reallocated = next((a.raw for a in attrs if a.id == 5), None)
    pending = next((a.raw for a in attrs if a.id == 197), None)

    pred_fail = d.get("PredFail", "False").lower() == "true"
    status = d.get("Status", "Unknown")
    if pred_fail:
        status = "PREDICTED FAILURE"
    elif status.upper() == "OK":
        status = "Healthy"

    return DiskInfo(
        index=_int(d.get("Index", "0")),
        model=d.get("Model", "Unknown").strip(),
        serial=d.get("Serial", "—").strip(),
        size_gb=_float(d.get("SizeGB", "0")),
        interface=d.get("Interface", "Unknown"),
        status=status,
        temperature=_int(temp) if temp is not None else None,
        power_on_hours=_int(poh) if poh is not None else None,
        reallocated_sectors=_int(reallocated) if reallocated is not None else None,
        pending_sectors=_int(pending) if pending is not None else None,
        smart_attrs=attrs,
        raw_output="\n".join(d.get("raw_lines", [])),
    )


def _int(v) -> int:
    try:
        return int(str(v).split(".")[0])
    except (ValueError, TypeError):
        return 0


def _float(v) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0
