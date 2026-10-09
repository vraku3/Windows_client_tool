"""AMD GPU fan tuning through ADLX -- what it can see, and why not. No Qt.

ADLX (`amdadlx64.dll`, AMD's official control library, shipped with the
Adrenalin driver) is the supported way to set a Radeon's fan curve. It is a
C++ interface library; the C entry points hand back objects whose first
field is a function table, and the order of each table below is taken from
the SDK headers (GPUOpen-LibrariesAndSDKs/ADLX, SDK/Include, *Vtbl structs).

Measured 2026-10-09 on this machine: ADLX 1.4 (build 121) initialises and
lists ONE GPU -- the integrated Radeon, which has no manual fan tuning.
The RX 7900 XTX is not in its list at all: it runs driver 31.0.14000.58004
dated 2022-12-02, which Windows Update installed over the current one on
2026-09-24, while ADLX came with the iGPU's 32.0.21036.18. A card ADLX
cannot see cannot have a fan curve, so `diagnose()` says exactly that,
with both driver versions, instead of offering a dead control.

Read-only on purpose: no GPU here supports manual fan tuning, so writing
fan states could not be verified, and an unverified write to a GPU fan is
not something to ship.
"""
import ctypes
import logging
from ctypes import POINTER, byref, c_char_p, c_int, c_uint, c_uint8, c_uint64, c_void_p, c_wchar_p
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_DLL = r"C:\Windows\System32\amdadlx64.dll"

# Function-table positions, from the SDK's C *Vtbl structs.
_SYS_GET_GPUS, _SYS_GET_TUNING = 1, 8
_LIST_SIZE, _GPULIST_AT = 3, 11
_RELEASE, _QUERY = 1, 2
_GPU_NAME = 7
_TUN_IS_FACTORY, _TUN_FAN_SUPPORTED, _TUN_GET_FAN = 4, 10, 16
_FAN_RANGES, _FAN_STATES = 3, 4
_STATELIST_AT = 11
_STATE_SPEED, _STATE_TEMP = 3, 5


class _IntRange(ctypes.Structure):
    _fields_ = [("minValue", c_int), ("maxValue", c_int), ("step", c_int)]


@dataclass
class AdlxGpu:
    name: str
    fan_tuning: bool
    at_factory: Optional[bool]
    curve: List[Tuple[int, int]] = field(default_factory=list)     # (°C, %)
    speed_range: Optional[Tuple[int, int]] = None
    temp_range: Optional[Tuple[int, int]] = None


def _call(obj, index: int, restype, argtypes, *args):
    vtbl = ctypes.cast(obj, POINTER(c_void_p))[0]
    fn = ctypes.cast(vtbl, POINTER(c_void_p))[index]
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(fn)(obj, *args)


def _release(*objs) -> None:
    for obj in objs:
        if obj:
            _call(obj, _RELEASE, c_int, [])


def read_gpus() -> Tuple[Optional[List[AdlxGpu]], str]:
    """(GPUs ADLX manages, reason). None with the reason when ADLX itself is
    absent or will not start -- never [], which would read as 'no AMD GPU'."""
    try:
        dll = ctypes.CDLL(_DLL)
    except OSError as e:
        return None, f"AMD's ADLX library is not installed ({e})"
    version, system = c_uint64(), c_void_p()
    dll.ADLXInitialize.argtypes = [c_uint64, POINTER(c_void_p)]
    if dll.ADLXQueryFullVersion(byref(version)) != 0 or dll.ADLXInitialize(version.value, byref(system)) != 0:
        return None, "AMD's ADLX library would not start with the installed driver"
    try:
        return _walk(system), ""
    finally:
        dll.ADLXTerminate()


def _walk(system) -> List[AdlxGpu]:
    gpus, tuning = c_void_p(), c_void_p()
    _call(system, _SYS_GET_GPUS, c_int, [POINTER(c_void_p)], byref(gpus))
    _call(system, _SYS_GET_TUNING, c_int, [POINTER(c_void_p)], byref(tuning))
    out = []
    try:
        for i in range(_call(gpus, _LIST_SIZE, c_uint, [])):
            gpu = c_void_p()
            _call(gpus, _GPULIST_AT, c_int, [c_uint, POINTER(c_void_p)], i, byref(gpu))
            try:
                out.append(_describe(tuning, gpu))
            finally:
                _release(gpu)
    finally:
        _release(tuning, gpus)
    return out


def _describe(tuning, gpu) -> AdlxGpu:
    name, supported, factory = c_char_p(), c_uint8(), c_uint8()
    _call(gpu, _GPU_NAME, c_int, [POINTER(c_char_p)], byref(name))
    _call(tuning, _TUN_FAN_SUPPORTED, c_int, [c_void_p, POINTER(c_uint8)], gpu, byref(supported))
    rc = _call(tuning, _TUN_IS_FACTORY, c_int, [c_void_p, POINTER(c_uint8)], gpu, byref(factory))
    info = AdlxGpu((name.value or b"").decode(errors="replace"), bool(supported.value),
                   bool(factory.value) if rc == 0 else None)
    if info.fan_tuning:
        _read_curve(tuning, gpu, info)
    return info


def _read_curve(tuning, gpu, info: AdlxGpu) -> None:
    iface, fan, states = c_void_p(), c_void_p(), c_void_p()
    try:
        _call(tuning, _TUN_GET_FAN, c_int, [c_void_p, POINTER(c_void_p)], gpu, byref(iface))
        _call(iface, _QUERY, c_int, [c_wchar_p, POINTER(c_void_p)], "IADLXManualFanTuning", byref(fan))
        speed, temp = _IntRange(), _IntRange()
        _call(fan, _FAN_RANGES, c_int, [POINTER(_IntRange), POINTER(_IntRange)], byref(speed), byref(temp))
        info.speed_range, info.temp_range = (speed.minValue, speed.maxValue), (temp.minValue, temp.maxValue)
        _call(fan, _FAN_STATES, c_int, [POINTER(c_void_p)], byref(states))
        for k in range(_call(states, _LIST_SIZE, c_uint, [])):
            state = c_void_p()
            _call(states, _STATELIST_AT, c_int, [c_uint, POINTER(c_void_p)], k, byref(state))
            s, t = c_int(), c_int()
            _call(state, _STATE_SPEED, c_int, [POINTER(c_int)], byref(s))
            _call(state, _STATE_TEMP, c_int, [POINTER(c_int)], byref(t))
            info.curve.append((t.value, s.value))
            _release(state)
    finally:
        _release(states, fan, iface)


@dataclass
class GpuFanStatus:
    name: str
    controllable: bool
    reason: str


def diagnose(adlx_gpus: Optional[List[AdlxGpu]], adlx_reason: str,
             windows_gpus: List[Tuple[str, str, str]]) -> List[GpuFanStatus]:
    """One line per AMD GPU Windows has: can its fan be curved, and if not, why.
    `windows_gpus` is (name, driver version, driver date) from Win32_VideoController."""
    seen = {g.name: g for g in (adlx_gpus or [])}
    newest = max((ver for _n, ver, _d in windows_gpus), default="", key=_version_key)
    out = []
    for name, version, date in windows_gpus:
        if "amd" not in name.lower() and "radeon" not in name.lower():
            continue
        if adlx_gpus is None:
            out.append(GpuFanStatus(name, False, adlx_reason))
        elif name not in seen:
            stale = f" -- older than {newest}, the driver ADLX came with" if version != newest else ""
            out.append(GpuFanStatus(name, False, f"AMD's control library cannot see this card: it runs "
                                                 f"driver {version} ({date}){stale}. Installing the current "
                                                 "AMD driver for it fixes this."))
        elif not seen[name].fan_tuning:
            out.append(GpuFanStatus(name, False, "this GPU has no adjustable fan (AMD reports no manual fan tuning)"))
        else:
            out.append(GpuFanStatus(name, True, f"current curve {seen[name].curve}"))
    return out


def _version_key(version: str) -> Tuple[int, ...]:
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return ()


def windows_gpus() -> List[Tuple[str, str, str]]:
    """(name, driver version, driver date) for every display adapter."""
    import json
    import subprocess
    cmd = ("Get-CimInstance Win32_VideoController | ForEach-Object { [pscustomobject]@{ "
           "n=$_.Name; v=$_.DriverVersion; d=$(if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' }) } } "
           "| ConvertTo-Json -Compress")
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                              capture_output=True, text=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = json.loads(done.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.warning("could not list display adapters: %s", e)
        return []
    rows = [rows] if isinstance(rows, dict) else rows
    return [(r.get("n") or "", r.get("v") or "", r.get("d") or "") for r in rows]
