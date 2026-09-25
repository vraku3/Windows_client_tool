"""Power and frequency: what each core is running at, and the power settings
that decide it. No Qt.

Per-core frequency comes from `CallNtPowerInformation(ProcessorInformation)`,
which reports each logical processor's current and maximum MHz and the limit
Windows has put on it. It is Windows' own account -- on some AMD/Intel boost
implementations "current" tracks the P-state Windows requested, not the
instantaneous clock -- so the tab says where the number came from. A limit
below the maximum is the honest signal for throttling.
"""
import ctypes
import logging
import re
import subprocess
from ctypes import wintypes
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

PROCESSOR_INFORMATION = 11


@dataclass(frozen=True)
class CoreFreq:
    index: int
    current_mhz: int
    max_mhz: int
    limit_mhz: int

    @property
    def throttled(self) -> bool:
        return 0 < self.limit_mhz < self.max_mhz

    @property
    def percent(self) -> float:
        return 100.0 * self.current_mhz / self.max_mhz if self.max_mhz else 0.0


class _PPI(ctypes.Structure):
    _fields_ = [("Number", wintypes.ULONG), ("MaxMhz", wintypes.ULONG),
                ("CurrentMhz", wintypes.ULONG), ("MhzLimit", wintypes.ULONG),
                ("MaxIdleState", wintypes.ULONG), ("CurrentIdleState", wintypes.ULONG)]


def read_frequencies(cpu_count: Optional[int] = None) -> Optional[List[CoreFreq]]:
    """One entry per logical processor, or None if the call was refused."""
    import psutil
    n = cpu_count or psutil.cpu_count(logical=True) or 0
    if n <= 0:
        return None
    try:
        powrprof = ctypes.WinDLL("powrprof")
        buf = (_PPI * n)()
        status = powrprof.CallNtPowerInformation(
            PROCESSOR_INFORMATION, None, 0, ctypes.byref(buf), ctypes.sizeof(buf))
    except (OSError, AttributeError) as e:
        logger.warning("CallNtPowerInformation unavailable: %s", e)
        return None
    if status != 0:
        logger.warning("CallNtPowerInformation returned 0x%08x", status & 0xFFFFFFFF)
        return None
    return [CoreFreq(p.Number, p.CurrentMhz, p.MaxMhz, p.MhzLimit) for p in buf]


def summarise(freqs: List[CoreFreq]) -> str:
    if not freqs:
        return "no data"
    cur = [f.current_mhz for f in freqs]
    text = (f"{min(cur):,}-{max(cur):,} MHz now   ·   max {max(f.max_mhz for f in freqs):,} MHz")
    limited = sum(1 for f in freqs if f.throttled)
    if limited:
        text += f"   ·   {limited} core(s) limited by Windows below their maximum"
    return text


# ---- power plan and source -----------------------------------------------------------

_PLAN = re.compile(r"GUID:\s*([0-9a-fA-F-]{36})\s*\((.+?)\)(\s*\*)?")


@dataclass(frozen=True)
class PowerPlan:
    guid: str
    name: str
    active: bool


def parse_plans(text: str) -> List[PowerPlan]:
    return [PowerPlan(m.group(1).lower(), m.group(2), bool(m.group(3)))
            for m in _PLAN.finditer(text or "")]


def _powercfg(*args) -> Optional[str]:
    try:
        done = subprocess.run(["powercfg", *args], capture_output=True, text=True,
                              timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("powercfg %s failed: %s", args, e)
        return None
    if done.returncode != 0:
        logger.warning("powercfg %s rc=%s: %s", args, done.returncode, (done.stderr or done.stdout).strip())
        return None
    return done.stdout


def list_plans() -> Optional[List[PowerPlan]]:
    out = _powercfg("/list")
    return None if out is None else parse_plans(out)


def set_active_plan(guid: str) -> Tuple[bool, str]:
    """Switch plan, then read it back: a zero exit is not proof."""
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", guid or ""):
        return False, "not a plan GUID"
    if _powercfg("/setactive", guid) is None:
        return False, "powercfg refused (some plans are locked by policy)"
    plans = list_plans() or []
    if any(p.guid == guid.lower() and p.active for p in plans):
        return True, "Power plan changed."
    return False, "powercfg reported success but the active plan did not change"


@dataclass(frozen=True)
class PowerStatus:
    on_battery: Optional[bool]       # None: unknown
    percent: Optional[int]
    minutes_left: Optional[int]
    plugged_note: str = ""


def read_power_status() -> PowerStatus:
    import psutil
    try:
        battery = psutil.sensors_battery()
    except (OSError, AttributeError) as e:
        logger.warning("battery unreadable: %s", e)
        return PowerStatus(None, None, None, "battery state could not be read")
    if battery is None:
        return PowerStatus(False, None, None, "no battery: a desktop on mains power")
    secs = battery.secsleft
    minutes = secs // 60 if isinstance(secs, int) and secs >= 0 else None
    return PowerStatus(not battery.power_plugged, int(battery.percent), minutes)


# ---- effective frequency (what the cores actually run at) ------------------------------

PDH_FMT_DOUBLE = 0x200
BS = chr(92)     # a backslash, spelled so no escape can be misread


class _PDH_VALUE(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", ctypes.c_double)]


class EffectiveFreq:
    """Per-logical-processor effective clock, from the `% Processor Performance`
    performance counter (100% = the nominal clock; boost reads above it).

    `CallNtPowerInformation` above reports the nominal number and does not move
    when the CPU boosts -- measured on this machine: 4300 MHz flat while the
    counter read ~120%, i.e. ~5.1 GHz. A rate counter needs two samples, so the
    first `read()` after construction answers None. Own the instance for the
    tab's lifetime and `close()` it.
    """

    def __init__(self, count: int) -> None:
        self._count = min(count, 64)      # instances are "group,index"; group 0 only
        self._pdh = ctypes.WinDLL("pdh")
        self._query = wintypes.HANDLE()
        self._counters: List[wintypes.HANDLE] = []
        self._primed = False
        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
            raise OSError("PdhOpenQuery failed")
        for i in range(self._count):
            handle = wintypes.HANDLE()
            path = BS + "Processor Information(0," + str(i) + ")" + BS + "% Processor Performance"
            if self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle)) != 0:
                self.close()
                raise OSError(f"PdhAddEnglishCounter failed for logical processor {i}")
            self._counters.append(handle)

    def read(self, nominal_mhz: List[int]) -> Optional[List[int]]:
        """Effective MHz per logical processor, or None until primed / on failure."""
        if self._pdh.PdhCollectQueryData(self._query) != 0:
            return None
        if not self._primed:
            self._primed = True
            return None
        out: List[int] = []
        for i, handle in enumerate(self._counters):
            value = _PDH_VALUE()
            if self._pdh.PdhGetFormattedCounterValue(
                    handle, PDH_FMT_DOUBLE, None, ctypes.byref(value)) != 0 or value.CStatus != 0:
                return None
            base = nominal_mhz[i] if i < len(nominal_mhz) else 0
            out.append(int(round(base * value.value / 100.0)))
        return out

    def close(self) -> None:
        if getattr(self, "_query", None) and self._query.value:
            self._pdh.PdhCloseQuery(self._query)
            self._query = wintypes.HANDLE()
