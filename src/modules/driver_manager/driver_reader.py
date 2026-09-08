import datetime
import json
import subprocess
from dataclasses import dataclass
from typing import List
import logging
logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

_PS_CMD = r"""
$drivers = Get-CimInstance -ClassName Win32_PnPSignedDriver |
    Where-Object { $_.DeviceName -ne $null -and $_.DeviceName -ne '' }
$result = foreach ($d in $drivers) {
    $dateStr = ''
    if ($d.DriverDate -and $d.DriverDate.Length -ge 8) {
        $dateStr = $d.DriverDate.Substring(0,8)
    }
    [PSCustomObject]@{
        Name       = [string]$d.DeviceName
        Class      = [string]$d.DeviceClass
        Version    = [string]$d.DriverVersion
        Date       = $dateStr
        Publisher  = [string]$d.Manufacturer
        IsSigned   = [bool]$d.IsSigned
        ErrorCode  = [int]($d.ConfigManagerErrorCode -as [int])
    }
}
$result | ConvertTo-Json -Compress -Depth 2
"""

_PS_CMD_DRIVERLESS = r"""
$devices = Get-CimInstance -ClassName Win32_PnPEntity |
    Where-Object { $_.ConfigManagerErrorCode -ne 0 -and $_.Name }
$devices | Select-Object Name, ConfigManagerErrorCode, PNPClass |
    ConvertTo-Json -Compress -Depth 2
"""


class DriverReadError(RuntimeError):
    """The PowerShell driver query returned something that could not be
    parsed -- distinct from an empty result, which is a real "no drivers"
    answer."""


@dataclass
class DriverInfo:
    device_name: str
    driver_class: str
    version: str
    date: str          # "YYYY-MM-DD" or ""
    publisher: str
    signed: bool
    error_code: int    # 0 = OK
    flags: str         # status flags string


_ERROR_CODE_MEANINGS = {
    1: "This device is not configured correctly",
    3: "The driver may be corrupted, or the system may be low on memory",
    10: "This device cannot start",
    18: "Reinstall the drivers for this device",
    19: "Registry may be corrupted",
    21: "Windows is removing this device",
    22: "This device is disabled",
    24: "This device is not present, not working properly, or does not have all its drivers installed",
    28: "The drivers for this device are not installed",
    31: "This device is not working properly because Windows cannot load the drivers required",
    32: "A driver for this device was disabled",
    37: "Windows cannot initialize the device driver for this hardware",
    39: "Windows cannot load the device driver for this hardware — the driver may be corrupted or missing",
    43: "Windows has stopped this device because it has reported problems",
}


_PSEUDO_CLASSES = {"SoftwareComponent", "SoftwareDevice", "PrintQueue"}


def decode_error_code(code: int) -> str:
    if not code:
        return ""
    return _ERROR_CODE_MEANINGS.get(
        code, f"unrecognized ConfigManagerErrorCode {code}")


def classify_provider(publisher: str) -> str:
    """"Microsoft" only for Microsoft's own strings, not anything that
    merely contains the word -- "Microsoft-compatible XYZ Corp" is a
    real OEM naming pattern and is third-party."""
    normalized = (publisher or "").strip().lower()
    return "Microsoft" if normalized in ("microsoft", "microsoft corporation") \
        else "Third-Party"


def _build_driver_info(d: dict, old_threshold_days: int = 730) -> DriverInfo:
    two_years_ago = datetime.datetime.now() - datetime.timedelta(days=old_threshold_days)

    name = d.get("Name") or ""
    cls = d.get("Class") or ""
    version = d.get("Version") or ""
    raw_date = d.get("Date") or ""
    publisher = d.get("Publisher") or ""
    signed = bool(d.get("IsSigned", True))
    try:
        error_code = int(d.get("ErrorCode") or 0)
    except (ValueError, TypeError):
        error_code = 0

    date_str = ""
    date_obj = None
    date_unreadable = False
    if raw_date and len(raw_date) >= 8:
        try:
            date_obj = datetime.datetime.strptime(raw_date[:8], "%Y%m%d")
            date_str = date_obj.strftime("%Y-%m-%d")
        except ValueError:
            date_unreadable = True
            logger.debug("Could not parse driver date %r", raw_date, exc_info=True)
    elif raw_date:
        date_unreadable = True

    flags = []
    if not signed:
        flags.append("🔴 Unsigned (as reported by Windows)")
    if error_code != 0:
        flags.append(f"🔴 Error({error_code}): {decode_error_code(error_code)}")
    if date_obj and date_obj < two_years_ago:
        flags.append("🟠 Old")
    if date_unreadable:
        flags.append("⚪ date unreadable")

    return DriverInfo(
        device_name=name, driver_class=cls, version=version,
        date=date_str, publisher=publisher, signed=signed,
        error_code=error_code, flags=" ".join(flags),
    )


def _merge_driverless_devices(drivers: List[DriverInfo],
                              driverless_raw: str) -> List[DriverInfo]:
    """Win32_PnPSignedDriver only lists devices that HAVE a driver. A
    device Windows could not find one for at all -- the yellow-bang case
    -- needs a second query, or this "driver manager" never shows the
    machine's actual problem devices."""
    have_names = {d.device_name for d in drivers}
    if not driverless_raw.strip():
        return drivers
    try:
        raw_devices = json.loads(driverless_raw)
    except json.JSONDecodeError:
        return drivers
    if isinstance(raw_devices, dict):
        raw_devices = [raw_devices]
    extra = []
    for dev in raw_devices:
        name = dev.get("Name") or ""
        if not name or name in have_names:
            continue
        code = int(dev.get("ConfigManagerErrorCode") or 0)
        extra.append(DriverInfo(
            device_name=name, driver_class=dev.get("PNPClass") or "",
            version="", date="", publisher="", signed=False,
            error_code=code,
            flags=f"🔴 No driver installed: {decode_error_code(code)}"))
    return drivers + extra


def fetch_drivers(old_threshold_days: int = 730) -> List[DriverInfo]:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_CMD],
        capture_output=True, text=True, errors="replace",
        creationflags=CREATE_NO_WINDOW, timeout=90,
    )
    raw = proc.stdout.strip()
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DriverReadError(
            f"could not parse the driver list PowerShell returned: {exc}"
        ) from exc
    if isinstance(data, dict):
        data = [data]

    drivers: List[DriverInfo] = []

    for d in data:
        name = d.get("Name") or ""
        if not name:
            continue
        drivers.append(_build_driver_info(d, old_threshold_days))

    driverless_proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_CMD_DRIVERLESS],
        capture_output=True, text=True, errors="replace",
        creationflags=CREATE_NO_WINDOW, timeout=90,
    )
    drivers = _merge_driverless_devices(drivers, driverless_proc.stdout.strip())

    drivers.sort(key=lambda d: (d.error_code != 0, not d.signed, d.device_name))
    return drivers
