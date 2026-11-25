r"""What memory the machine has and where it has gone.

`GetPerformanceInfo` returns, in one call, almost exactly Task Manager's
Memory panel: committed and its limit, physical total and available, the
system cache, and the paged and non-paged kernel pools. It also carries the
process, thread and handle totals the CPU panel shows, so both panels are fed
from here.

**Every figure it returns is in PAGES, not bytes** -- except `PageSize`
itself, which is how you convert them. Reporting them raw understates memory
by a factor of 4096, and the numbers still look plausible, which is the
dangerous part: 8 GB of commit reads as 2 MB rather than as an obvious error.

Two things come from elsewhere because that call does not have them:

- **Hardware reserved** is installed RAM minus what the OS can see, and
  installed RAM needs `GetPhysicallyInstalledSystemMemory`. On this machine
  the gap is real and Task Manager shows it.
- **Speed, slots and form factor** are per-DIMM facts from WMI, which is slow
  and cannot go on a once-a-second path. They are read once, on demand.

Qt-free, like the rest of the engine.
"""
import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)


class _PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", wintypes.DWORD),
        ("ProcessCount", wintypes.DWORD),
        ("ThreadCount", wintypes.DWORD),
    ]


_psapi.GetPerformanceInfo.restype = wintypes.BOOL
_psapi.GetPerformanceInfo.argtypes = [
    ctypes.POINTER(_PERFORMANCE_INFORMATION), wintypes.DWORD]
_kernel32.GetPhysicallyInstalledSystemMemory.restype = wintypes.BOOL
_kernel32.GetPhysicallyInstalledSystemMemory.argtypes = [
    ctypes.POINTER(ctypes.c_ulonglong)]


@dataclass(frozen=True, slots=True)
class MemoryStatus:
    """Task Manager's Memory panel, in BYTES.

    Converted here rather than at the call site so a caller cannot forget
    that the API speaks pages.
    """

    total: int
    available: int
    committed: int
    commit_limit: int
    commit_peak: int
    cached: int
    kernel_paged: int
    kernel_nonpaged: int
    installed: Optional[int] = None
    #: Installed minus what the OS can address, or None if we could not read
    #: how much is installed. Never 0 as a stand-in: "no reserved memory" and
    #: "we could not tell" are different answers.
    hardware_reserved: Optional[int] = None
    #: These ride along in the same call and feed the CPU panel.
    processes: int = 0
    threads: int = 0
    handles: int = 0

    @property
    def in_use(self) -> int:
        return self.total - self.available

    @property
    def used_percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.in_use / self.total * 100.0


@dataclass(frozen=True, slots=True)
class MemoryModule:
    """One physical stick."""

    slot: Optional[str] = None
    capacity: Optional[int] = None
    speed_mhz: Optional[int] = None
    form_factor: Optional[str] = None
    manufacturer: Optional[str] = None


#: Win32_PhysicalMemory.FormFactor, which is a number on the wire.
_FORM_FACTORS = {
    8: "DIMM", 12: "SODIMM", 13: "SRIMM", 0: "Unknown", 1: "Other",
    2: "SIP", 3: "DIP", 4: "ZIP", 5: "SOJ", 6: "Proprietary", 7: "SIMM",
    9: "TSOP", 10: "PGA", 11: "RIMM", 14: "SMD", 15: "SSMP", 20: "FB-DIMM",
}


def memory_status() -> MemoryStatus:
    """Where the machine's memory has gone, right now."""
    info = _PERFORMANCE_INFORMATION()
    info.cb = ctypes.sizeof(info)
    if not _psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
        raise OSError(f"GetPerformanceInfo failed: "
                      f"{ctypes.WinError(ctypes.get_last_error())}")

    page = info.PageSize or 4096
    total = info.PhysicalTotal * page
    installed = _installed_bytes()
    reserved = None
    if installed is not None:
        # Never negative: the two figures come from different sources and
        # can disagree by a page or two.
        reserved = max(0, installed - total)

    return MemoryStatus(
        total=total,
        available=info.PhysicalAvailable * page,
        committed=info.CommitTotal * page,
        commit_limit=info.CommitLimit * page,
        commit_peak=info.CommitPeak * page,
        cached=info.SystemCache * page,
        kernel_paged=info.KernelPaged * page,
        kernel_nonpaged=info.KernelNonpaged * page,
        installed=installed,
        hardware_reserved=reserved,
        processes=info.ProcessCount,
        threads=info.ThreadCount,
        handles=info.HandleCount,
    )


def _installed_bytes() -> Optional[int]:
    """How much RAM is fitted, which is more than the OS can address.

    The difference is what Task Manager calls "Hardware reserved". `None`
    when the call fails rather than falling back to the OS figure, which
    would silently report the reserved amount as zero.
    """
    kilobytes = ctypes.c_ulonglong(0)
    if not _kernel32.GetPhysicallyInstalledSystemMemory(
            ctypes.byref(kilobytes)):
        logger.debug("GetPhysicallyInstalledSystemMemory failed")
        return None
    return kilobytes.value * 1024


def memory_modules() -> List[MemoryModule]:
    """The physical sticks: slot, size, speed, form factor.

    WMI, so it is slow -- measured at tens of milliseconds. Read once when a
    panel asks, never on the refresh path.
    """
    try:
        import wmi
    except (ImportError, OSError):  # pragma: no cover
        logger.debug("WMI is not available for the memory module list")
        return []

    try:
        import ctypes
        ctypes.windll.ole32.CoInitialize(None)
    except Exception:  # noqa: BLE001 - already initialised is fine
        logger.debug("memory_modules: giving up on this read", exc_info=True)
        pass

    try:
        client = wmi.WMI()
        return [_module(entry) for entry in client.Win32_PhysicalMemory()]
    except Exception as error:  # noqa: BLE001 - the panel degrades, not dies
        logger.warning("Could not read the memory modules: %s", error)
        return []


def _module(entry) -> MemoryModule:
    return MemoryModule(
        slot=_text(getattr(entry, "DeviceLocator", None)),
        capacity=_number(getattr(entry, "Capacity", None)),
        speed_mhz=_number(getattr(entry, "Speed", None)),
        form_factor=_FORM_FACTORS.get(
            _number(getattr(entry, "FormFactor", None))),
        manufacturer=_text(getattr(entry, "Manufacturer", None)),
    )


def _text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    # Never "": a caller cannot tell an empty string from "not known".
    return text or None


def _number(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
