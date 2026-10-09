"""AMD GPU fan curves through ADLX, using the copy that matches the card's driver. No Qt.

ADLX (`amdadlx64.dll`) is AMD's official control library. It is a C++
interface library; the C entry points hand back objects whose first field
is a function table, and the order of each table below is taken from the
SDK headers (GPUOpen-LibrariesAndSDKs/ADLX, SDK/Include, *Vtbl structs).

Measured 2026-10-09 on this machine -- the trap this module is built around:

- the System32 copy (ADLX 1.4.121, from the integrated GPU's current driver
  32.0.21036.18) lists ONE GPU, the iGPU. The RX 7900 XTX is invisible to
  it: that card runs driver 31.0.14000.58004 (2022-12-02), which Windows
  Update put back on 2026-09-24 and which the user keeps on purpose (newer
  drivers crash games on this PC);
- that driver's OWN package carries its own copy
  (`DriverStore\\FileRepository\\u0386350.inf_*\\B386336\\amdadlx64.dll`,
  ADLX 1.0.4.19). Loaded instead -- with its matching `atiadlxx.dll` loaded
  first, so the two ADL generations never mix -- it lists both GPUs and the
  7900 XTX supports manual fan tuning: 5 points, speed 23..100 %, 25..100 °C,
  factory curve (30,23) (50,38) (63,53) (76,68) (85,100), zero-RPM on.

So the copy is chosen by matching each package's `DriverVer` to the
discrete GPU's driver version, and only ONE copy is ever loaded per
process. The curve then lives in the GPU's firmware: it keeps working when
this app is closed, and the GPU's own thermal protection stays in charge.
"""
import ctypes
import glob
import logging
import os
import re
import threading
from contextlib import contextmanager
from ctypes import POINTER, byref, c_char_p, c_int, c_uint, c_uint8, c_uint64, c_void_p, c_wchar_p
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

SYSTEM_COPY = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")

# Function-table positions, from the SDK's C *Vtbl structs.
_SYS_GET_GPUS, _SYS_GET_TUNING = 1, 8
_LIST_SIZE, _GPULIST_AT = 3, 11
_RELEASE, _QUERY = 1, 2
_GPU_NAME = 7
_TUN_IS_FACTORY, _TUN_RESET, _TUN_FAN_SUPPORTED, _TUN_GET_FAN = 4, 5, 10, 16
_FAN_RANGES, _FAN_STATES, _FAN_VALID, _FAN_SET = 3, 4, 6, 7
_FAN_ZERO_SUPPORTED, _FAN_ZERO_GET, _FAN_ZERO_SET = 8, 9, 10
_STATELIST_AT = 11
_STATE_GET_SPEED, _STATE_SET_SPEED, _STATE_GET_TEMP, _STATE_SET_TEMP = 3, 4, 5, 6

_lock = threading.Lock()
_loaded: Optional[Tuple[str, object]] = None        # (directory, dll): one copy per process


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
    zero_rpm: Optional[bool] = None


class AdlxError(RuntimeError):
    pass


# ---- choosing and loading the right copy -------------------------------------------------

def adlx_copies() -> List[Tuple[str, str]]:
    """(directory, driver version) for every ADLX copy in the driver store."""
    root = os.path.join(SYSTEM_COPY, "DriverStore", "FileRepository")
    out = []
    for dll in glob.glob(os.path.join(root, "*", "amdadlx64.dll")) + \
            glob.glob(os.path.join(root, "*", "*", "amdadlx64.dll")):
        package = os.path.relpath(dll, root).split(os.sep)[0]
        out.append((os.path.dirname(dll), _package_version(os.path.join(root, package))))
    return out


def _package_version(package_dir: str) -> str:
    for inf in glob.glob(os.path.join(package_dir, "*.inf")):
        try:
            with open(inf, encoding="utf-8", errors="replace") as f:
                m = re.search(r"^\s*DriverVer\s*=\s*[^,]*,\s*([\d.]+)", f.read(), re.M | re.I)
        except OSError as e:
            logger.warning("could not read %s: %s", inf, e)
            continue
        if m:
            return m.group(1)
    return ""


def choose_copy(windows_gpus: List[Tuple[str, str, str]], copies: List[Tuple[str, str]]) -> str:
    """The directory to load ADLX from: the copy shipped with the discrete
    Radeon's own driver when there is one, else System32."""
    discrete = [v for name, v, _d in windows_gpus
                if "radeon" in name.lower() and "(tm) graphics" not in name.lower()]
    for version in discrete:
        match = next((d for d, v in copies if v == version), None)
        if match:
            return match
    return SYSTEM_COPY


def _load(directory: str):
    global _loaded
    if _loaded is not None:
        if os.path.normcase(_loaded[0]) != os.path.normcase(directory):
            logger.warning("ADLX already loaded from %s; not mixing in %s", _loaded[0], directory)
        return _loaded[1]
    os.add_dll_directory(directory)
    adl = os.path.join(directory, "atiadlxx.dll")
    if os.path.exists(adl):
        ctypes.WinDLL(adl)                          # its own ADL first: never mix generations
    dll = ctypes.CDLL(os.path.join(directory, "amdadlx64.dll"))
    dll.ADLXInitialize.argtypes = [c_uint64, POINTER(c_void_p)]
    _loaded = (directory, dll)
    return dll


@contextmanager
def _session(directory: str) -> Iterator[c_void_p]:
    with _lock:
        try:
            dll = _load(directory)
        except OSError as e:
            raise AdlxError(f"AMD's ADLX library could not be loaded ({e})") from e
        version, system = c_uint64(), c_void_p()
        if dll.ADLXQueryFullVersion(byref(version)) != 0 or dll.ADLXInitialize(version.value, byref(system)) != 0:
            raise AdlxError("AMD's ADLX library would not start with the installed driver")
        try:
            yield system
        finally:
            dll.ADLXTerminate()


# ---- the C function tables -----------------------------------------------------------------

def _call(obj, index: int, restype, argtypes, *args):
    vtbl = ctypes.cast(obj, POINTER(c_void_p))[0]
    fn = ctypes.cast(vtbl, POINTER(c_void_p))[index]
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(fn)(obj, *args)


def _release(*objs) -> None:
    for obj in objs:
        if obj:
            _call(obj, _RELEASE, c_int, [])


@contextmanager
def _gpus(system) -> Iterator[Tuple[c_void_p, List[Tuple[str, c_void_p]]]]:
    """(tuning services, [(name, gpu)]) for the session; all released after."""
    gpus, tuning = c_void_p(), c_void_p()
    _call(system, _SYS_GET_GPUS, c_int, [POINTER(c_void_p)], byref(gpus))
    _call(system, _SYS_GET_TUNING, c_int, [POINTER(c_void_p)], byref(tuning))
    found = []
    try:
        for i in range(_call(gpus, _LIST_SIZE, c_uint, [])):
            gpu, name = c_void_p(), c_char_p()
            _call(gpus, _GPULIST_AT, c_int, [c_uint, POINTER(c_void_p)], i, byref(gpu))
            _call(gpu, _GPU_NAME, c_int, [POINTER(c_char_p)], byref(name))
            found.append(((name.value or b"").decode(errors="replace"), gpu))
        yield tuning, found
    finally:
        _release(*[g for _n, g in found])
        _release(tuning, gpus)


@contextmanager
def _fan(tuning, gpu) -> Iterator[c_void_p]:
    iface, fan = c_void_p(), c_void_p()
    try:
        if _call(tuning, _TUN_GET_FAN, c_int, [c_void_p, POINTER(c_void_p)], gpu, byref(iface)) != 0 or \
                _call(iface, _QUERY, c_int, [c_wchar_p, POINTER(c_void_p)], "IADLXManualFanTuning",
                      byref(fan)) != 0:
            raise AdlxError("this GPU did not hand over its fan tuning")
        yield fan
    finally:
        _release(fan, iface)


@contextmanager
def _states(fan) -> Iterator[List[c_void_p]]:
    lst = c_void_p()
    if _call(fan, _FAN_STATES, c_int, [POINTER(c_void_p)], byref(lst)) != 0:
        raise AdlxError("could not read the GPU's fan states")
    items = []
    try:
        for k in range(_call(lst, _LIST_SIZE, c_uint, [])):
            st = c_void_p()
            _call(lst, _STATELIST_AT, c_int, [c_uint, POINTER(c_void_p)], k, byref(st))
            items.append(st)
        yield lst, items
    finally:
        _release(*items)
        _release(lst)


def _get_int(obj, index: int) -> int:
    v = c_int()
    _call(obj, index, c_int, [POINTER(c_int)], byref(v))
    return v.value


def _get_bool(obj, index: int, *args) -> Optional[bool]:
    v = c_uint8()
    argtypes = [c_void_p] * len(args) + [POINTER(c_uint8)]
    rc = _call(obj, index, c_int, argtypes, *args, byref(v))
    return bool(v.value) if rc == 0 else None


# ---- reading -----------------------------------------------------------------------------

def read_gpus(directory: str = SYSTEM_COPY) -> Tuple[Optional[List[AdlxGpu]], str]:
    """(GPUs this ADLX copy manages, reason). None with the reason when ADLX
    itself is absent or will not start -- never [] (that reads 'no AMD GPU')."""
    try:
        with _session(directory) as system, _gpus(system) as (tuning, gpus):
            return [_describe(tuning, name, gpu) for name, gpu in gpus], ""
    except AdlxError as e:
        return None, str(e)


def _describe(tuning, name: str, gpu) -> AdlxGpu:
    info = AdlxGpu(name, bool(_get_bool(tuning, _TUN_FAN_SUPPORTED, gpu)),
                   _get_bool(tuning, _TUN_IS_FACTORY, gpu))
    if info.fan_tuning:
        with _fan(tuning, gpu) as fan:
            speed, temp = _IntRange(), _IntRange()
            _call(fan, _FAN_RANGES, c_int, [POINTER(_IntRange), POINTER(_IntRange)], byref(speed), byref(temp))
            info.speed_range, info.temp_range = (speed.minValue, speed.maxValue), (temp.minValue, temp.maxValue)
            with _states(fan) as (_lst, items):
                info.curve = [(_get_int(s, _STATE_GET_TEMP), _get_int(s, _STATE_GET_SPEED)) for s in items]
            if _get_bool(fan, _FAN_ZERO_SUPPORTED):
                info.zero_rpm = _get_bool(fan, _FAN_ZERO_GET)
    return info


# ---- writing -----------------------------------------------------------------------------

def validate_gpu_curve(points: List[Tuple[float, float]], gpu: AdlxGpu) -> List[str]:
    """Why these points must not be written to this GPU; [] when they are fine."""
    errors = []
    if len(points) != len(gpu.curve):
        errors.append(f"this GPU takes exactly {len(gpu.curve)} points")
    temps, speeds = [t for t, _ in points], [s for _, s in points]
    if any(b <= a for a, b in zip(temps, temps[1:])):
        errors.append("temperatures must strictly increase from point to point")
    if any(b < a for a, b in zip(speeds, speeds[1:])):
        errors.append("the fan may never slow down as the temperature rises")
    lo_s, hi_s = gpu.speed_range or (0, 100)
    lo_t, hi_t = gpu.temp_range or (0, 100)
    if any(not lo_s <= s <= hi_s for s in speeds):
        errors.append(f"fan speeds must be between {lo_s}% and {hi_s}% on this GPU")
    if any(not lo_t <= t <= hi_t for t in temps):
        errors.append(f"temperatures must be between {lo_t} and {hi_t} °C on this GPU")
    return errors


def set_curve(gpu_name: str, points: List[Tuple[float, float]], directory: str) -> List[Tuple[int, int]]:
    """Write the curve, have AMD validate it, set it, and return what the GPU
    reads back. Raises AdlxError with the reason if anything refuses."""
    pts = [(int(round(t)), int(round(s))) for t, s in points]
    with _session(directory) as system, _gpus(system) as (tuning, gpus):
        gpu = _find(gpus, gpu_name)
        with _fan(tuning, gpu) as fan:
            with _states(fan) as (lst, items):
                if len(items) != len(pts):
                    raise AdlxError(f"this GPU takes exactly {len(items)} points")
                for state, (t, s) in zip(items, pts):
                    _call(state, _STATE_SET_TEMP, c_int, [c_int], t)
                    _call(state, _STATE_SET_SPEED, c_int, [c_int], s)
                bad = c_int(-1)
                rc = _call(fan, _FAN_VALID, c_int, [c_void_p, POINTER(c_int)], lst, byref(bad))
                if rc != 0 or bad.value != -1:
                    raise AdlxError(f"AMD rejected the curve (point {bad.value + 1}, code {rc})")
                if _call(fan, _FAN_SET, c_int, [c_void_p], lst) != 0:
                    raise AdlxError("the GPU refused the new fan curve")
            with _states(fan) as (_lst, items):
                return [(_get_int(s, _STATE_GET_TEMP), _get_int(s, _STATE_GET_SPEED)) for s in items]


def reset_to_factory(gpu_name: str, directory: str) -> bool:
    """AMD's own reset: the driver's automatic fan control again. It resets ALL
    of the GPU's tuning, which is why the caller only uses it when the GPU was
    at factory settings before this app touched it."""
    with _session(directory) as system, _gpus(system) as (tuning, gpus):
        gpu = _find(gpus, gpu_name)
        if _call(tuning, _TUN_RESET, c_int, [c_void_p], gpu) != 0:
            raise AdlxError("the GPU refused to reset to factory settings")
        return bool(_get_bool(tuning, _TUN_IS_FACTORY, gpu))


def set_zero_rpm(gpu_name: str, on: bool, directory: str) -> None:
    with _session(directory) as system, _gpus(system) as (tuning, gpus):
        with _fan(tuning, _find(gpus, gpu_name)) as fan:
            if _call(fan, _FAN_ZERO_SET, c_int, [c_uint8], 1 if on else 0) != 0:
                raise AdlxError("the GPU refused the zero-RPM setting")


def _find(gpus, name: str):
    gpu = next((g for n, g in gpus if n == name), None)
    if gpu is None:
        raise AdlxError(f"{name} is not visible to this ADLX copy")
    return gpu


# ---- the screen's summary ----------------------------------------------------------------

@dataclass
class GpuFanStatus:
    name: str
    controllable: bool
    reason: str
    gpu: Optional[AdlxGpu] = None


def diagnose(adlx_gpus: Optional[List[AdlxGpu]], adlx_reason: str,
             windows_gpus: List[Tuple[str, str, str]]) -> List[GpuFanStatus]:
    """One line per AMD GPU Windows has: can its fan be curved, and if not, why.
    `windows_gpus` is (name, driver version, driver date) from Win32_VideoController."""
    seen = {g.name: g for g in (adlx_gpus or [])}
    out = []
    for name, version, date in windows_gpus:
        if "amd" not in name.lower() and "radeon" not in name.lower():
            continue
        if adlx_gpus is None:
            out.append(GpuFanStatus(name, False, adlx_reason))
        elif name not in seen:
            out.append(GpuFanStatus(name, False, f"AMD's control library cannot see this card (driver "
                                                 f"{version}, {date}), and no copy of it matching that "
                                                 "driver was found in the driver store"))
        elif not seen[name].fan_tuning:
            out.append(GpuFanStatus(name, False, "this GPU has no adjustable fan (AMD reports no manual fan tuning)"))
        else:
            g = seen[name]
            state = "factory automatic curve" if g.at_factory else "custom curve set"
            out.append(GpuFanStatus(name, True, f"fan curve available ({state})", g))
    return out


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
