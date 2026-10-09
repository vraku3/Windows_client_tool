"""GPU temperature and fan speed from the display kernel interface. No Qt.

`D3DKMTQueryAdapterInfo(KMTQAITYPE_ADAPTERPERFDATA)` is where Task Manager
gets its GPU temperature: user mode, no administrator, no vendor driver
package, any WDDM 2.4+ GPU. Measured 2026-10-09: RX 7900 XTX 61.0 °C and
1,333 RPM (caps: max 3,600 RPM); the Ryzen 9950X3D's integrated Radeon
56.0 °C -- that GPU sits on the CPU's I/O die, so it is a package-area
temperature, not the core Tctl (which needs the PawnIO path).

LibreHardwareMonitor 0.9.6 reports NO sensors for the RX 7900 XTX here, so
this is the GPU's temperature source, not a fallback.

Temperature is in tenths of a degree. An adapter that answers 0 for both
temperature and fan is a software or remote adapter with no thermal data,
and is left out rather than reported as 0 °C.
"""
import ctypes
import logging
import re
from ctypes import wintypes as w
from typing import List

from .model import FAN, TEMPERATURE, Sensor

logger = logging.getLogger(__name__)

_KMTQAITYPE_ADAPTERREGISTRYINFO = 8
_KMTQAITYPE_ADAPTERPERFDATA = 62


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]


class _ADAPTERINFO(ctypes.Structure):
    _fields_ = [("hAdapter", ctypes.c_uint32), ("AdapterLuid", _LUID),
                ("NumOfSources", ctypes.c_ulong), ("bPrecisePresentRegionsPreferred", w.BOOL)]


class _ENUMADAPTERS2(ctypes.Structure):
    _fields_ = [("NumAdapters", ctypes.c_ulong), ("pAdapters", ctypes.POINTER(_ADAPTERINFO))]


class _QUERYADAPTERINFO(ctypes.Structure):
    _fields_ = [("hAdapter", ctypes.c_uint32), ("Type", ctypes.c_int),
                ("pPrivateDriverData", ctypes.c_void_p), ("PrivateDriverDataSize", ctypes.c_uint)]


class _PERFDATA(ctypes.Structure):
    _fields_ = [("PhysicalAdapterIndex", ctypes.c_uint32), ("MemoryFrequency", ctypes.c_uint64),
                ("MaxMemoryFrequency", ctypes.c_uint64), ("MaxMemoryFrequencyOC", ctypes.c_uint64),
                ("MemoryBandwidth", ctypes.c_uint64), ("PCIEBandwidth", ctypes.c_uint64),
                ("FanRPM", ctypes.c_ulong), ("Power", ctypes.c_ulong), ("Temperature", ctypes.c_ulong),
                ("PowerStateOverride", ctypes.c_ubyte)]


class _REGISTRYINFO(ctypes.Structure):
    _fields_ = [("AdapterString", ctypes.c_wchar * 260), ("BiosString", ctypes.c_wchar * 260),
                ("DacType", ctypes.c_wchar * 260), ("ChipType", ctypes.c_wchar * 260)]


def _query(gdi, handle: int, kind: int, struct) -> bool:
    info = _QUERYADAPTERINFO(handle, kind, ctypes.cast(ctypes.byref(struct), ctypes.c_void_p),
                             ctypes.sizeof(struct))
    return (gdi.D3DKMTQueryAdapterInfo(ctypes.byref(info)) & 0xFFFFFFFF) == 0


def read_gpus() -> List[Sensor]:
    """Temperature and fan sensors for every GPU that reports them."""
    try:
        gdi = ctypes.WinDLL("gdi32")
        enum = _ENUMADAPTERS2()
        if gdi.D3DKMTEnumAdapters2(ctypes.byref(enum)) & 0xFFFFFFFF:
            return []
        adapters = (_ADAPTERINFO * enum.NumAdapters)()
        enum.pAdapters = adapters
        if gdi.D3DKMTEnumAdapters2(ctypes.byref(enum)) & 0xFFFFFFFF:
            return []
    except (OSError, AttributeError) as e:
        logger.warning("D3DKMT adapter enumeration failed: %s", e)
        return []
    out: List[Sensor] = []
    seen = set()
    for adapter in adapters[:enum.NumAdapters]:
        out += _adapter_sensors(gdi, adapter, seen)
    return out


def _adapter_sensors(gdi, adapter, seen: set) -> List[Sensor]:
    perf, reg = _PERFDATA(), _REGISTRYINFO()
    if not _query(gdi, adapter.hAdapter, _KMTQAITYPE_ADAPTERPERFDATA, perf):
        return []
    if perf.Temperature == 0 and perf.FanRPM == 0:
        return []
    name = reg.AdapterString if _query(gdi, adapter.hAdapter, _KMTQAITYPE_ADAPTERREGISTRYINFO, reg) else ""
    name = name or "GPU"
    luid = (adapter.AdapterLuid.HighPart, adapter.AdapterLuid.LowPart)
    if luid in seen:            # the same adapter can be listed more than once
        return []
    seen.add(luid)
    key = _stable_key(name, seen)
    sensors = [Sensor(f"/d3dkmt/{key}/temperature", name, "GPU", TEMPERATURE,
                      perf.Temperature / 10.0 if perf.Temperature else None, "d3dkmt")]
    if perf.FanRPM:
        sensors.append(Sensor(f"/d3dkmt/{key}/fan", name, "GPU fan", FAN, float(perf.FanRPM), "d3dkmt"))
    return sensors


def _stable_key(name: str, seen: set) -> str:
    """An id that survives a reboot. The LUID does not -- Windows assigns new
    ones at every boot -- and a fan curve saved against "GPU temperature"
    must find its source again tomorrow. Two identical cards get #2, #3."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "gpu"
    count = sum(1 for k in seen if isinstance(k, str) and k.startswith(slug))
    seen.add(f"{slug}#{count + 1}")
    return slug if count == 0 else f"{slug}-{count + 1}"
