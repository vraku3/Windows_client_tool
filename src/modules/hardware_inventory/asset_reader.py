"""Readers behind the asset views: monitors, memory slots, firmware, battery.

Qt-free.  Each reader returns ``(value, error)`` where ``error`` is a sentence
when the read was refused or failed, so "could not look" is never shown as
"there is nothing".  WMI callers must run on a COM-initialised thread
(``COMWorker``).
"""
from __future__ import annotations

import logging
import platform
import socket
import winreg
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from modules.hardware_inventory import asset_parse as ap

logger = logging.getLogger(__name__)

_DISPLAY_ENUM = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"


def _wmi(namespace: Optional[str] = None):
    import wmi
    return wmi.WMI(namespace=namespace) if namespace else wmi.WMI()


def _reg_edids() -> List[Tuple[str, bytes]]:
    """(instance path, EDID bytes) for every monitor the registry remembers."""
    found: List[Tuple[str, bytes]] = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_ENUM) as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            model = winreg.EnumKey(root, i)
            with winreg.OpenKey(root, model) as mk:
                for j in range(winreg.QueryInfoKey(mk)[0]):
                    inst = winreg.EnumKey(mk, j)
                    try:
                        with winreg.OpenKey(mk, inst + r"\Device Parameters") as dp:
                            edid = winreg.QueryValueEx(dp, "EDID")[0]
                    except OSError:
                        logger.debug("no EDID under %s\\%s", model, inst)
                        continue
                    found.append((f"DISPLAY\\{model}\\{inst}", bytes(edid)))
    return found


def _connected_instances() -> Tuple[Optional[set], str]:
    """Instance paths Windows reports as currently connected."""
    try:
        names = {str(m.InstanceName).rsplit("_", 1)[0].upper()
                 for m in _wmi("root\\wmi").WmiMonitorID()}
        return names, ""
    except Exception as e:  # WMI raises a bare com_error
        logger.warning("WmiMonitorID unavailable: %s", e)
        return None, f"Could not tell which monitors are connected ({e})."


def read_monitors() -> Tuple[List[ap.MonitorRecord], str]:
    """Every monitor with an EDID.  Registry entries for monitors no longer
    attached are kept with ``active=False`` (a swapped panel is useful
    history for an asset audit), duplicates of one physical panel collapsed."""
    try:
        edids = _reg_edids()
    except OSError as e:
        logger.warning("Cannot read the DISPLAY enum: %s", e)
        return [], f"Could not read monitor records from the registry ({e})."
    connected, why = _connected_instances()
    seen: Dict[Tuple[str, str], ap.MonitorRecord] = {}
    for path, blob in edids:
        rec = ap.parse_edid(blob, path)
        if rec is None:
            logger.debug("%s does not hold a valid EDID", path)
            continue
        rec.active = None if connected is None else path.upper() in connected
        key = (rec.manufacturer_id + str(rec.product_code), rec.serial)
        prev = seen.get(key)
        if prev is None or (rec.active and not prev.active):
            seen[key] = rec
    recs = sorted(seen.values(), key=lambda r: (not r.active, r.name))
    return recs, why


@dataclass
class MemoryReport:
    slots: List[ap.MemorySlot]
    total_slots: Optional[int] = None
    max_capacity_bytes: Optional[int] = None
    ecc_mode: str = ""          # controller-level error correction, "" = unknown
    error: str = ""


def _max_capacity_bytes(arr) -> Optional[int]:
    """Win32_PhysicalMemoryArray.MaxCapacity is a uint32 in KB.  Microsoft's
    documented convention is that 0x80000000 there is a sentinel meaning "the
    real figure does not fit in 32 bits, read MaxCapacityEx instead" (a
    uint64, also KB).  Measured on this board (ASRock X870E Taichi):
    MaxCapacity itself already reports 134217728 KB = 128 GB across 4 slots,
    so the sentinel does not fire here, but a board rated for 2 TB+ would
    need it -- checking is nearly free and wrong on the boards it applies to.
    """
    try:
        cap_kb = int(arr.MaxCapacity or 0)
    except (TypeError, ValueError):
        return None
    if cap_kb == 0x80000000:
        try:
            cap_kb = int(getattr(arr, "MaxCapacityEx", 0) or 0)
        except (TypeError, ValueError):
            cap_kb = 0
    return cap_kb * 1024 if cap_kb else None


def read_memory_slots() -> MemoryReport:
    """Empty slots are added only when the slot count could be read."""
    try:
        c = _wmi()
        sticks = [{
            "BankLabel": s.BankLabel, "DeviceLocator": s.DeviceLocator,
            "Capacity": s.Capacity, "FormFactor": s.FormFactor,
            "SMBIOSMemoryType": s.SMBIOSMemoryType, "Speed": s.Speed,
            "ConfiguredClockSpeed": s.ConfiguredClockSpeed,
            "DataWidth": s.DataWidth, "TotalWidth": s.TotalWidth,
            "Manufacturer": s.Manufacturer, "PartNumber": s.PartNumber,
            "SerialNumber": s.SerialNumber,
        } for s in c.Win32_PhysicalMemory()]
    except Exception as e:
        logger.warning("Win32_PhysicalMemory failed: %s", e)
        return MemoryReport([], None, None, "", f"Could not read memory modules ({e}).")
    total: Optional[int] = None
    ecc_code = None
    max_cap: Optional[int] = None
    try:
        arr = _wmi().Win32_PhysicalMemoryArray()
        if arr:
            total = sum(int(a.MemoryDevices or 0) for a in arr) or None
            ecc_code = arr[0].MemoryErrorCorrection
            max_cap = _max_capacity_bytes(arr[0])
    except Exception as e:
        logger.warning("Win32_PhysicalMemoryArray failed: %s", e)
    err = "" if total is not None else "Slot count unavailable; empty slots are not shown."
    ecc = ap.ecc_mode_name(ecc_code) if ecc_code is not None else ""
    return MemoryReport(ap.build_slot_map(sticks, total), total, max_cap, ecc, err)


def _read_firmware_mode() -> Optional[str]:
    """GetFirmwareType: 1 = BIOS, 2 = UEFI.  (The PEFirmwareType registry value
    is absent on a normal install, so it is not a source.)"""
    import ctypes
    kind = ctypes.c_uint32(0)
    try:
        ok = ctypes.windll.kernel32.GetFirmwareType(ctypes.byref(kind))
    except (AttributeError, OSError):
        logger.debug("GetFirmwareType unavailable", exc_info=True)
        return None
    if not ok:
        return None
    return {1: "Legacy BIOS", 2: "UEFI"}.get(kind.value)


def _read_secure_boot(mode: Optional[str]) -> Tuple[Optional[bool], str]:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\SecureBoot\State") as k:
            return bool(winreg.QueryValueEx(k, "UEFISecureBootEnabled")[0]), ""
    except FileNotFoundError:
        # The State key exists only on UEFI machines that support Secure Boot.
        if mode == "Legacy BIOS":
            return False, "Not supported: the machine boots in legacy BIOS mode."
        return False, "Windows reports Secure Boot as unsupported on this firmware."
    except OSError as e:
        logger.warning("Secure Boot state refused: %s", e)
        return None, f"Could not read Secure Boot state ({e})."


def _read_tpm() -> Tuple[Optional[bool], str, str]:
    try:
        tpms = _wmi("root\\cimv2\\Security\\MicrosoftTpm").Win32_Tpm()
    except Exception as e:
        text = str(e)
        if "denied" in text.lower() or "0x80041003" in text:
            return None, "", "Reading the TPM needs administrator rights."
        logger.warning("Win32_Tpm failed: %s", e)
        return None, "", f"Could not query the TPM ({e})."
    if not tpms:
        return False, "", "No TPM was reported by Windows."
    spec = str(tpms[0].SpecVersion or "").split(",")[0].strip()
    return True, spec, ""


def _read_virtualization() -> Optional[bool]:
    try:
        procs = _wmi().Win32_Processor()
        if procs and procs[0].VirtualizationFirmwareEnabled is not None:
            return bool(procs[0].VirtualizationFirmwareEnabled)
    except Exception as e:
        logger.warning("VirtualizationFirmwareEnabled unreadable: %s", e)
    return None


def _read_bios() -> Tuple[Optional[date], str, str, str]:
    """(release date, version, system serial, error)."""
    try:
        b = _wmi().Win32_BIOS()[0]
    except Exception as e:
        logger.warning("Win32_BIOS failed: %s", e)
        return None, "", "", f"Could not read BIOS ({e})."
    released = None
    raw = str(b.ReleaseDate or "")[:8]
    try:
        released = datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        logger.debug("BIOS release date %r not parseable", raw)
    return released, str(b.SMBIOSBIOSVersion or ""), str(b.SerialNumber or "").strip(), ""


def _board_serial() -> str:
    """Baseboard serial, for boards whose BIOS serial is a placeholder."""
    try:
        return str(_wmi().Win32_BaseBoard()[0].SerialNumber or "").strip()
    except Exception as e:
        logger.warning("Win32_BaseBoard failed: %s", e)
        return ""


def read_firmware() -> ap.FirmwareInfo:
    fw = ap.FirmwareInfo()
    fw.firmware_mode = _read_firmware_mode()
    fw.secure_boot, fw.secure_boot_reason = _read_secure_boot(fw.firmware_mode)
    fw.virtualization = _read_virtualization()
    fw.tpm_present, fw.tpm_version, fw.tpm_reason = _read_tpm()
    fw.bios_date, fw.bios_version, _serial, _err = _read_bios()
    return fw


def read_battery() -> Tuple[Optional[ap.BatteryHealth], str]:
    """(health, note).  (None, "No battery") is a desktop; (None, reason) is a
    refused read, and the two must stay distinguishable."""
    try:
        if not _wmi().Win32_Battery():
            return None, "No battery installed (desktop or docked)."
    except Exception as e:
        logger.warning("Win32_Battery failed: %s", e)
        return None, f"Could not query the battery ({e})."
    try:
        w = _wmi("root\\wmi")
        design = int(w.BatteryStaticData()[0].DesignedCapacity)
        full = int(w.BatteryFullChargedCapacity()[0].FullChargedCapacity)
        cycles = None
        try:
            cycles = int(w.BatteryCycleCount()[0].CycleCount)
        except Exception:
            logger.debug("BatteryCycleCount unavailable", exc_info=True)
        return ap.BatteryHealth(design, full, cycles), ""
    except Exception as e:
        logger.warning("Battery capacity classes failed: %s", e)
        return None, f"Battery present but capacity could not be read ({e})."


def physical_drives() -> List[Dict[str, str]]:
    """Physical drives as {Model, Size, Interface, Serial, Partitions} rows.

    Win32_DiskDrive omits Storage Spaces pool members (a 4 TB and a 2 TB NVMe
    here), so Get-PhysicalDisk is the source; the WMI list is only the
    fallback when that read fails.
    """
    from modules.disk_health import disk_reader as dr
    try:
        rep = dr.read_disk_report()
    except dr.DiskReadError as e:
        logger.warning("Get-PhysicalDisk unavailable, using Win32_DiskDrive: %s", e)
        from modules.hardware_inventory import hardware_reader as hr
        return hr.get_storage_info()[0]
    rows = []
    for d in rep.disks:
        if d.is_virtual:
            continue
        rows.append({
            "Model": d.name, "Size": dr.format_size(d.size_bytes),
            "Interface": f"{d.bus} {d.media}".strip(), "Serial": d.serial,
            "Partitions": str(sum(1 for p in rep.partitions if str(p.disk) == d.device_id)),
        })
    return rows


def read_asset_record() -> Tuple[Dict[str, str], List[ap.Finding]]:
    """Gather everything and build the flat record plus firmware findings."""
    import psutil
    from modules.hardware_inventory import hardware_reader as hr

    overview = dict(hr.get_overview())
    drives = physical_drives()
    mem_report = read_memory_slots()
    slots = mem_report.slots
    monitors, _merr = read_monitors()
    fw = read_firmware()
    _d, ver, serial, _e = _read_bios()
    if ap.is_placeholder(serial):
        serial = _board_serial()
    manufacturer = overview.get("Manufacturer", "")
    rec = ap.build_asset_record(
        hostname=socket.gethostname(),
        manufacturer=manufacturer,
        model=overview.get("Model", ""),
        serial=serial,
        cpu=overview.get("CPU", ""),
        ram_bytes=psutil.virtual_memory().total,
        slots=slots,
        disks=drives,
        monitors=[m for m in monitors if m.active is not False],
        os_name=platform.platform(),
        bios=f"{ver} ({fw.bios_date.isoformat()})" if fw.bios_date else ver,
        fw=fw,
    )
    return rec, ap.firmware_findings(fw) + ap.memory_findings(slots, mem_report.max_capacity_bytes)
