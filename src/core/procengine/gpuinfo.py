r"""GPU utilisation, adapter memory, and the facts about each adapter.

Two sources, and the split is the engine's usual one -- what changes every
second against what never changes.

**Hot: PDH, with the query held open.** There is no `NtQuerySystemInformation`
for the GPU the way there is for processors and memory; the scheduler's
accounting reaches user mode only through the performance counters. Four
counter sets matter:

| Set | What it gives |
|---|---|
| `GPU Engine` | one instance per (process, physical engine), utilisation |
| `GPU Adapter Memory` | dedicated and shared bytes in use, per adapter |
| `GPU Process Memory` | the same, per process -- for the Details column |
| `GPU Local Adapter Memory` | the on-card half, on partitioned adapters |

Measured on this machine: 483 engine instances, and a full sample of all of
them costs **0.33 ms** -- as long as the query stays open. PDH does the
delta itself and keeps the instance list; reopening it every tick would
re-enumerate 483 counters instead.

Two consequences of the counters being percentages worth stating up front:

- **The first collection has no answer, and PDH says so** by failing
  `PdhGetFormattedCounterArray` with `PDH_INVALID_DATA` rather than
  returning zeros. That lands exactly on the rule the rest of the engine
  keeps, so it is passed through as `None`, not smoothed into 0%.
- **English counter names, always** (`AddEnglishCounter`). The counter set
  is called "GPU Engine" only on an English install; the localised name is
  what `\GPU Engine(*)\...` would have to spell everywhere else. This is
  the same trap as matching the loopback adapter by name.

**Cold: `HKLM\SOFTWARE\Microsoft\DirectX`.** One subkey per adapter DirectX
has ever seen, carrying the name, driver version, feature levels and the
memory limits -- which is where Task Manager's GPU panel gets them. It is
read once and joined to the live sample by LUID.

Notably NOT from WMI: `Win32_VideoController.AdapterRAM` is a signed 32-bit
field, and this machine's 24 GB card reports **-1048576** through it. WMI is
consulted for one thing the registry does not carry, the driver date, and
that join is on the PCI vendor and device ids rather than on the name.

Qt-free, like the rest of the engine.
"""
import logging
import re
import winreg
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DIRECTX_KEY = r"SOFTWARE\Microsoft\DirectX"

#: WARP, the software rasteriser Windows always presents alongside the real
#: adapters. Recognised by its fixed PCI identity rather than by the string
#: "Microsoft Basic Render Driver", which is the English name only.
SOFTWARE_ADAPTER = (0x1414, 0x8C)

#: `pid_1234_luid_0x00000000_0x00015725_phys_0_eng_2_engtype_Compute 0`.
#: The engine type is taken to the end of the string on purpose: real types
#: include "Video Codec Engine", "Compute 0" and "High Priority 3D", so a
#: pattern that stops at a space or a digit drops most of them.
_ENGINE_RE = re.compile(
    r"pid_(?P<pid>\d+)_luid_(?P<hi>0x[0-9A-Fa-f]+)_(?P<lo>0x[0-9A-Fa-f]+)"
    r"_phys_(?P<phys>\d+)_eng_(?P<eng>\d+)_engtype_(?P<engtype>.+)$")

#: `pid_1234_luid_..._phys_0`, or the same without the pid for an adapter
#: total, optionally with the `_part_N` suffix the local-memory set adds.
_MEMORY_RE = re.compile(
    r"(?:pid_(?P<pid>\d+)_)?luid_(?P<hi>0x[0-9A-Fa-f]+)_(?P<lo>0x[0-9A-Fa-f]+)"
    r"_phys_(?P<phys>\d+)(?:_part_(?P<part>\d+))?$")


@dataclass(frozen=True, slots=True)
class EngineInstance:
    """One process's share of one physical engine."""

    pid: int
    luid: int
    physical: int
    engine: int
    engtype: str


@dataclass(frozen=True, slots=True)
class MemoryInstance:
    """One adapter, or one process on one adapter."""

    luid: int
    physical: int
    pid: Optional[int] = None
    partition: Optional[int] = None


@dataclass(frozen=True, slots=True)
class EngineLoad:
    """How busy one class of engine is, 0..100."""

    engtype: str
    percent: float


@dataclass(frozen=True, slots=True)
class AdapterUsage:
    """One adapter's live figures."""

    luid: int
    engines: Tuple[EngineLoad, ...] = ()
    dedicated_bytes: Optional[int] = None
    shared_bytes: Optional[int] = None

    @property
    def utilisation(self) -> Optional[float]:
        """The busiest engine, which is Task Manager's headline figure.

        Not the sum of the engine types: a card decoding video while it
        composites the desktop would read as 160% busy, and a card doing
        four things at once could pass 300%. `None` when nothing reported
        -- an adapter we did not hear from is not an idle one.
        """
        if not self.engines:
            return None
        return max(load.percent for load in self.engines)


@dataclass(frozen=True, slots=True)
class AdapterFacts:
    """What never changes about an adapter, read once."""

    luid: int
    name: Optional[str] = None
    driver_version: Optional[str] = None
    driver_date: Optional[str] = None
    directx_version: Optional[str] = None
    feature_level: Optional[str] = None
    dedicated_limit: Optional[int] = None
    shared_limit: Optional[int] = None
    vendor_id: Optional[int] = None
    device_id: Optional[int] = None
    software: bool = False
    #: Why a fact above is `None`, keyed by the field name. The standing
    #: rule: a value we could not read says so, rather than arriving as a
    #: zero that reads like a measurement.
    unavailable: Dict[str, str] = field(default_factory=dict)


# ---- reading the instance names -----------------------------------------

def parse_engine_instance(name: str) -> Optional[EngineInstance]:
    """Take a `GPU Engine` instance name apart, or `None` if it is not one.

    PDH also produces roll-up instances (`total`) on some counter sets, and
    an unrecognised name is skipped rather than guessed at.
    """
    match = _ENGINE_RE.fullmatch(name or "")
    if match is None:
        return None
    return EngineInstance(
        pid=int(match["pid"]),
        luid=_luid(match["hi"], match["lo"]),
        physical=int(match["phys"]),
        engine=int(match["eng"]),
        engtype=match["engtype"])


def parse_memory_instance(name: str) -> Optional[MemoryInstance]:
    """Take a `GPU * Memory` instance name apart, or `None`."""
    match = _MEMORY_RE.fullmatch(name or "")
    if match is None:
        return None
    return MemoryInstance(
        luid=_luid(match["hi"], match["lo"]),
        physical=int(match["phys"]),
        pid=int(match["pid"]) if match["pid"] else None,
        partition=int(match["part"]) if match["part"] else None)


def _luid(high: str, low: str) -> int:
    """Both halves of the LUID, kept as one 64-bit number.

    Keeping only the low half is fine until a machine's LUID counter passes
    2^32, at which point two adapters collide and their figures are added
    together.
    """
    return (int(high, 16) << 32) | int(low, 16)


# ---- from instances to a per-adapter figure -----------------------------

def summarise_engines(
        readings: Dict[str, float]) -> Dict[int, Tuple[EngineLoad, ...]]:
    """Per adapter, how busy each class of engine is.

    Two different aggregations, because the instance name carries two
    different kinds of multiplicity:

    - **Across processes on one physical engine: added.** Each instance is
      that process's share of that engine's time, so the sum is the
      engine's busy percentage.
    - **Across physical engines of one type: the busiest, not the sum.**
      This machine's card presents two Copy engines and the basic render
      driver presents thirty-three 3D engines. Adding them would report
      3300% for a class of engine that is at most fully busy.
    """
    per_engine: Dict[Tuple[int, str, int], float] = {}
    for name, value in readings.items():
        parsed = parse_engine_instance(name)
        if parsed is None:
            continue
        key = (parsed.luid, parsed.engtype, parsed.engine)
        per_engine[key] = per_engine.get(key, 0.0) + float(value)

    per_type: Dict[int, Dict[str, float]] = {}
    for (luid, engtype, _), percent in per_engine.items():
        types = per_type.setdefault(luid, {})
        types[engtype] = max(types.get(engtype, 0.0), percent)

    out: Dict[int, Tuple[EngineLoad, ...]] = {}
    for luid, types in per_type.items():
        loads = [EngineLoad(engtype=engtype,
                            percent=min(100.0, max(0.0, percent)))
                 for engtype, percent in types.items()]
        # Busiest first: the panel shows the top few, and the interesting
        # engine is never the one that has been idle all session.
        loads.sort(key=lambda load: (-load.percent, load.engtype))
        out[luid] = tuple(loads)
    return out


def summarise_processes(readings: Dict[str, float]) -> Dict[int, float]:
    """Per PID, how much GPU it is using -- Task Manager's "GPU" column.

    Aggregated differently from the per-adapter figure, and the difference
    matters. An adapter's utilisation is its busiest ENGINE, because two
    engines running at once do not make the card 160% busy. A process's
    figure is the busiest engine of ITS work, for the same reason -- but
    taken across every adapter it touches, since a process rendering on
    the discrete card and decoding on the integrated one is using both.

    Summing a process's instances instead would report 200% for something
    doing two things at once, which is the number Task Manager's column
    conspicuously never shows.
    """
    per_engine: Dict[Tuple[int, int, str, int], float] = {}
    for name, value in readings.items():
        parsed = parse_engine_instance(name)
        if parsed is None:
            continue
        key = (parsed.pid, parsed.luid, parsed.engtype, parsed.engine)
        per_engine[key] = per_engine.get(key, 0.0) + float(value)

    out: Dict[int, float] = {}
    for (pid, _luid, _engtype, _engine), percent in per_engine.items():
        out[pid] = max(out.get(pid, 0.0), min(100.0, max(0.0, percent)))
    return out


def _summarise_by_pid(readings: Dict[str, float]) -> Dict[int, int]:
    """A `GPU Process Memory` counter's instances, totalled per pid."""
    out: Dict[int, int] = {}
    for name, value in readings.items():
        parsed = parse_memory_instance(name)
        if parsed is None or parsed.pid is None:
            continue
        out[parsed.pid] = out.get(parsed.pid, 0) + int(value)
    return out


def _summarise_memory(readings: Dict[str, float]) -> Dict[int, int]:
    """Per adapter, the total bytes reported by a memory counter set."""
    out: Dict[int, int] = {}
    for name, value in readings.items():
        parsed = parse_memory_instance(name)
        if parsed is None:
            continue
        out[parsed.luid] = out.get(parsed.luid, 0) + int(value)
    return out


# ---- the live sampler ---------------------------------------------------

_ENGINE_PATH = r"\GPU Engine(*)\Utilization Percentage"
_DEDICATED_PATH = r"\GPU Adapter Memory(*)\Dedicated Usage"
_SHARED_PATH = r"\GPU Adapter Memory(*)\Shared Usage"
_PROC_DEDICATED_PATH = r"\GPU Process Memory(*)\Dedicated Usage"
_PROC_SHARED_PATH = r"\GPU Process Memory(*)\Shared Usage"


class GpuSampler:
    """A PDH query held open across ticks.

    Held open on purpose. PDH keeps the instance list and the previous
    reading inside the query, which is what makes a sample of 483 counters
    cost a third of a millisecond; opening and closing one per tick would
    re-enumerate the whole counter set every second.

    Use it as a context manager, or call `close()` -- an abandoned query is
    a leaked handle in the PDH service.
    """

    def __init__(self) -> None:
        self._query = None
        self._counters: Dict[str, object] = {}
        self._pdh = None
        self._open()

    # ---- lifecycle ------------------------------------------------------

    def _open(self) -> None:
        try:
            import win32pdh
        except ImportError:  # pragma: no cover - pywin32 is a hard dependency
            logger.warning("win32pdh is unavailable; the GPU panel is blind")
            return
        self._pdh = win32pdh
        try:
            self._query = win32pdh.OpenQuery()
            for name, path in (("engine", _ENGINE_PATH),
                               ("dedicated", _DEDICATED_PATH),
                               ("shared", _SHARED_PATH),
                               ("proc_dedicated", _PROC_DEDICATED_PATH),
                               ("proc_shared", _PROC_SHARED_PATH)):
                # AddEnglishCounter, not AddCounter: the counter set is
                # named "GPU Engine" only on an English install.
                self._counters[name] = win32pdh.AddEnglishCounter(
                    self._query, path)
        except Exception as error:  # noqa: BLE001 - any PDH failure is fatal
            logger.warning("Could not open the GPU counters: %s", error)
            self.close()

    def close(self) -> None:
        """Release the query. Safe to call twice, and after a failed open."""
        query, self._query = self._query, None
        self._counters.clear()
        if query is not None and self._pdh is not None:
            try:
                self._pdh.CloseQuery(query)
            except Exception as error:  # noqa: BLE001
                logger.debug("Closing the GPU query failed: %s", error)

    def __enter__(self) -> "GpuSampler":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---- the reading ----------------------------------------------------

    def sample(self) -> Optional[List[AdapterUsage]]:
        """One reading per adapter, or `None` when there is nothing at all.

        The two kinds of counter here do not become available together.
        Memory in use is an instantaneous number and answers on the first
        collection; utilisation is a percentage over an interval, and PDH
        refuses the first read of it with `PDH_INVALID_DATA` rather than
        returning a zero. So the first tick of a session yields adapters
        with real memory figures and no engines, whose `utilisation` is
        therefore `None` -- a dash on the panel rather than a 0% that
        claims an idle GPU nobody measured.

        `None` only when there is no measurement whatsoever: after
        `close()`, or when the counters could not be opened.
        """
        if self._query is None:
            return None
        try:
            self._pdh.CollectQueryData(self._query)
        except Exception as error:  # noqa: BLE001
            logger.debug("The GPU query collected nothing: %s", error)
            return None

        engines = summarise_engines(self._array("engine"))
        dedicated = _summarise_memory(self._array("dedicated"))
        shared = _summarise_memory(self._array("shared"))
        if not engines and not dedicated and not shared:
            return None

        luids = set(engines) | set(dedicated) | set(shared)
        usage = [AdapterUsage(luid=luid,
                              engines=engines.get(luid, ()),
                              dedicated_bytes=dedicated.get(luid),
                              shared_bytes=shared.get(luid))
                 for luid in sorted(luids)]
        return usage

    def process_memory(self) -> Dict[int, Tuple[int, int]]:
        """Per pid, `(dedicated bytes, shared bytes)` on the GPU.

        Summed across adapters: a process can hold memory on more than one,
        and the question the properties dialog asks is how much video
        memory THIS process is using, not where.

        Like `process_usage`, this reads the last collection rather than
        taking one, so every figure on a row comes from the same instant.
        """
        dedicated = _summarise_by_pid(self._array("proc_dedicated"))
        shared = _summarise_by_pid(self._array("proc_shared"))
        return {pid: (dedicated.get(pid, 0), shared.get(pid, 0))
                for pid in set(dedicated) | set(shared)}

    def process_usage(self) -> Dict[int, float]:
        """Per-pid GPU percentages from the LAST collection.

        Deliberately does not collect: `sample()` and this are read from the
        same tick by the process panes, and collecting twice would halve
        each interval and report figures for windows that do not line up
        with the rest of the row.
        """
        return summarise_processes(self._array("engine"))

    def _array(self, name: str) -> Dict[str, float]:
        """One counter's instances, or nothing when PDH has no answer yet.

        `PDH_INVALID_DATA` on the first collection is the expected case,
        not an error: the counter is a percentage and one reading does not
        make one. An instance that vanished between the two collections
        raises the same way, which is equally not a failure.
        """
        counter = self._counters.get(name)
        if counter is None:
            return {}
        try:
            return self._pdh.GetFormattedCounterArray(
                counter, self._pdh.PDH_FMT_DOUBLE)
        except Exception as error:  # noqa: BLE001
            logger.debug("GPU counter %s has no reading yet: %s", name, error)
            return {}


# ---- the facts, read once -----------------------------------------------

def feature_level_name(level: Optional[int]) -> Optional[str]:
    """`0xC200` -> `"12_2"`, the way DirectX spells its feature levels.

    The encoding is one nibble each for major and minor in the top half of
    the word, so it generalises rather than needing a table. `None` for a
    missing or zero value -- this machine's integrated adapter carries no
    `MaxD3D12FeatureLevel` at all, and "0_0" would be a fabricated answer.
    """
    if not level:
        return None
    return f"{(level >> 12) & 0xF}_{(level >> 8) & 0xF}"


def adapter_facts() -> List[AdapterFacts]:
    """Every display adapter DirectX knows about, newest reading first.

    From the registry rather than from WMI or DXGI: the key is what Task
    Manager's own panel reads, it needs no COM and no device enumeration,
    and it carries the 64-bit memory sizes that `Win32_VideoController`
    cannot express.
    """
    rows = _directx_rows()
    if not rows:
        return []
    dates = _driver_dates()
    facts = []
    for values in rows:
        vendor = _int(values.get("VendorId"))
        device = _int(values.get("DeviceId"))
        unavailable: Dict[str, str] = {}

        d3d12 = feature_level_name(_int(values.get("MaxD3D12FeatureLevel")))
        d3d11 = feature_level_name(_int(values.get("MaxD3D11FeatureLevel")))
        if d3d12:
            directx, level = "12", d3d12
        elif d3d11:
            directx, level = "11", d3d11
        else:
            directx = level = None
            unavailable["directx_version"] = (
                "the adapter records no Direct3D feature level")

        driver = _version(_int(values.get("DriverVersion")))
        if driver is None:
            unavailable["driver_version"] = "the adapter records no driver version"

        date = dates.get((vendor, device))
        if date is None:
            unavailable["driver_date"] = "no matching Win32_VideoController entry"

        name = _text(values.get("Description"))
        if name is None:
            # A real, if rare, shape: a DirectX registry entry with
            # literally no Description, VendorId, or DeviceId at all --
            # measured on a real machine (a stale/orphaned adapter
            # subkey, distinct from a genuine software adapter, which
            # always carries SOFTWARE_ADAPTER's known vendor+device
            # pair). Disclosed the same way as the other unreadable
            # fields above, never silently defaulted to a name that
            # looks real.
            unavailable["name"] = "the adapter records no description"

        dedicated = _int(values.get("DedicatedVideoMemory"))
        facts.append(AdapterFacts(
            luid=_int(values.get("AdapterLuid")) or 0,
            name=name,
            driver_version=driver,
            driver_date=date,
            directx_version=(f"{directx} (FL {level})" if directx else None),
            feature_level=level,
            dedicated_limit=dedicated,
            shared_limit=_int(values.get("SharedSystemMemory")),
            vendor_id=vendor,
            device_id=device,
            software=(vendor, device) == SOFTWARE_ADAPTER,
            unavailable=unavailable))
    # Real adapters first, and the biggest of those first: the panel opens
    # on whichever card the machine actually renders with.
    facts.sort(key=lambda entry: (entry.software,
                                  -(entry.dedicated_limit or 0)))
    return facts


def _directx_rows() -> List[Dict[str, object]]:
    """Every value of every adapter subkey under the DirectX key."""
    rows = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DIRECTX_KEY) as key:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(key, index)
                except OSError:
                    # OSError here is how winreg says the enumeration is
                    # finished. Control flow, not a failure.
                    logger.debug("_directx_rows: registry enumeration finished", exc_info=True)
                    break
                index += 1
                values = _key_values(key, name)
                if values:
                    rows.append(values)
    except OSError as error:
        logger.warning("The DirectX registry key is unreadable: %s", error)
    return rows


def _key_values(parent, name: str) -> Dict[str, object]:
    values: Dict[str, object] = {}
    try:
        with winreg.OpenKey(parent, name) as sub:
            index = 0
            while True:
                try:
                    key, value, _kind = winreg.EnumValue(sub, index)
                except OSError:
                    # OSError here is how winreg says the enumeration is
                    # finished. Control flow, not a failure.
                    logger.debug("_key_values: registry enumeration finished", exc_info=True)
                    break
                index += 1
                values[key] = value
    except OSError as error:
        logger.debug("Adapter subkey %s is unreadable: %s", name, error)
    return values


def _driver_dates() -> Dict[Tuple[Optional[int], Optional[int]], str]:
    """Driver dates from WMI, keyed by PCI vendor and device id.

    The one fact the DirectX key does not carry. Joined on the ids parsed
    out of `PNPDeviceID` rather than on the adapter's name: two identical
    cards share a name, and a name is the wrong kind of key for a join that
    has real identifiers available.

    Costs about 140 ms, which is why it is on the read-once path with the
    DIMM query and never on a tick.
    """
    try:
        import wmi
    except ImportError:  # pragma: no cover
        return {}
    dates: Dict[Tuple[Optional[int], Optional[int]], str] = {}
    try:
        for controller in wmi.WMI().Win32_VideoController():
            ids = _pci_ids(getattr(controller, "PNPDeviceID", "") or "")
            stamp = str(getattr(controller, "DriverDate", "") or "")[:8]
            if ids and len(stamp) == 8:
                dates[ids] = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
    except Exception as error:  # noqa: BLE001
        logger.debug("Could not read the video controllers: %s", error)
    return dates


_PCI_RE = re.compile(r"VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})")


def _pci_ids(pnp_id: str) -> Optional[Tuple[int, int]]:
    match = _PCI_RE.search(pnp_id)
    if match is None:
        return None
    return int(match.group(1), 16), int(match.group(2), 16)


def _version(packed: Optional[int]) -> Optional[str]:
    """The registry's QWORD driver version as its four dotted parts."""
    if not packed:
        return None
    return ".".join(str((packed >> shift) & 0xFFFF)
                    for shift in (48, 32, 16, 0))


def _text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value) -> Optional[int]:
    if isinstance(value, int):
        return value
    return None
