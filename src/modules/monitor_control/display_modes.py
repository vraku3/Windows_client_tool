r"""What each display can actually run: the GDI mode enumeration.

The CCD API in `display_config.py` says what the desktop looks like now. It
does not enumerate alternatives, so a mode picker has to go through
`EnumDisplaySettingsExW`, keyed by the `\\.\DISPLAYn` name that
`monitor_identity.source_gdi_name` produces.

Everything here is READ-ONLY. `ChangeDisplaySettingsExW` is deliberately not
imported: this module answers what is possible, and nothing in it moves the
machine.

Four things about the real enumeration, each of which yields a wrong answer
rather than an error:

* **The rate list is a property of a resolution, not of a display.**
  `\\.\DISPLAY1` here enumerates 19 distinct refresh rates over 226 modes,
  but 280 Hz exists only below the native resolution. A UI that shows a
  display's whole rate list at 2560x1440 offers modes that do not exist.
* **`dmDisplayFrequency` of 0 or 1 means "the hardware default".** Both
  appear in real enumerations. Neither is a rate, so both become `None`
  here rather than a "1 Hz" entry nobody can tell from a real one.
* **An unattached device answers, quietly.** DISPLAY3..DISPLAY5 on this
  machine are real entries with no current mode; a success return with a
  zeroed DEVMODE reads as a 0x0 display at 0 Hz unless it is checked.
* **Duplicates are normal.** The same width/height/rate/depth comes back
  several times; a picker that does not deduplicate shows each resolution
  half a dozen times.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: EnumDisplaySettingsEx mode numbers.
ENUM_CURRENT_SETTINGS = -1
ENUM_REGISTRY_SETTINGS = -2

#: DEVMODE dmDisplayFlags.
DM_INTERLACED = 0x2

#: DISPLAY_DEVICE StateFlags.
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4

#: dmDisplayFrequency values that mean "whatever the hardware defaults to".
#: Documented by Windows, and both occur in real enumerations.
_DEFAULT_FREQUENCIES = (0, 1)


# -- the pure part -----------------------------------------------------


@dataclass(frozen=True)
class DisplayMode:
    """One mode a display offers.

    `refresh_hz` is `None` when the enumeration reported the hardware
    default (0 or 1) rather than a rate. That is "we do not know", and it
    is never rendered as a number the display could be set to.
    """

    width: int
    height: int
    refresh_hz: Optional[float]
    bits_per_pixel: int
    interlaced: bool

    @property
    def resolution(self) -> Tuple[int, int]:
        return (self.width, self.height)

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def describe(self) -> str:
        rate = f"{self.refresh_hz:g} Hz" if self.refresh_hz else "default rate"
        return f"{self.width}x{self.height} @ {rate}"


def normalize_refresh(frequency: int) -> Optional[float]:
    """`dmDisplayFrequency` -> a rate, or `None` for the hardware default."""
    if frequency in _DEFAULT_FREQUENCIES:
        return None
    return float(frequency)


def build_mode(width: int, height: int, frequency: int, bits_per_pixel: int,
               display_flags: int) -> DisplayMode:
    """Assemble a `DisplayMode` from raw DEVMODE fields.

    Split from the ctypes call so the frequency rule and the interlaced bit
    are testable with no display attached.
    """
    return DisplayMode(
        width=width,
        height=height,
        refresh_hz=normalize_refresh(frequency),
        bits_per_pixel=bits_per_pixel,
        interlaced=bool(display_flags & DM_INTERLACED),
    )


def _sort_key(width: int, height: int) -> Tuple[int, int, int]:
    return (width * height, width, height)


def resolutions_from(modes: Iterable[DisplayMode]) -> List[Tuple[int, int]]:
    """Distinct resolutions, biggest first."""
    unique = {m.resolution for m in modes}
    return sorted(unique, key=lambda r: _sort_key(*r), reverse=True)


def refresh_rates_from(modes: Iterable[DisplayMode], width: int,
                       height: int) -> List[float]:
    """The rates valid AT this resolution, ascending.

    A rate the display supports somewhere is not a rate it supports here.
    Modes whose rate is the hardware default carry no number and are left
    out rather than reported as 0.
    """
    rates = {m.refresh_hz for m in modes
             if m.width == width and m.height == height
             and m.refresh_hz is not None}
    return sorted(rates)


def _rank(mode: DisplayMode) -> Tuple[Tuple[int, int, int], float, int]:
    """Bigger, then faster, then deeper. An unknown rate sorts below every
    known one -- a mode we cannot name a rate for is not one to recommend."""
    return (_sort_key(mode.width, mode.height),
            mode.refresh_hz if mode.refresh_hz is not None else -1.0,
            mode.bits_per_pixel)


def best_from(modes: Sequence[DisplayMode],
              native: Optional[Tuple[int, int]] = None) -> Optional[DisplayMode]:
    """The mode to recommend.

    With `native` -- the panel's own resolution, from
    `monitor_identity.native_resolution` -- this is the fastest mode AT that
    resolution. Without it, the largest resolution and then the fastest rate
    at it, which is a poorer rule and is only the fallback.

    The difference is not academic. The Dell here is a 2560x1440 panel that
    runs 280 Hz, and the driver also offers 3840x2160 by rendering high and
    downscaling (AMD VSR), capped at 120 Hz. Largest-first recommends
    3840x2160@120: softer than the panel's own pixels AND 160 Hz slower.
    Anyone pressing a "best mode" button is trying to escape exactly that.

    A `native` the device does not enumerate falls back rather than
    answering nothing -- an EDID disagreeing with the mode list is a reason
    to distrust the EDID, not to refuse to answer.
    """
    if not modes:
        return None
    if native is not None:
        at_native = [m for m in modes if m.resolution == tuple(native)]
        if at_native:
            return max(at_native, key=_rank)
        logger.debug("native resolution %s is not in the mode list; falling "
                     "back to the largest enumerated mode", native)
    return max(modes, key=_rank)


# -- the ctypes edge ---------------------------------------------------


class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", ctypes.c_wchar * 32),
        ("DeviceString", ctypes.c_wchar * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", ctypes.c_wchar * 128),
        ("DeviceKey", ctypes.c_wchar * 128),
    ]


def _new_devmode() -> _DEVMODEW:
    devmode = _DEVMODEW()
    devmode.dmSize = ctypes.sizeof(_DEVMODEW)
    return devmode


def _enum_settings(device_name: str, mode_num: int, devmode) -> int:
    """`EnumDisplaySettingsExW`, in one place so tests can stand in for it.

    Non-zero is success. A zero return is the normal end of the
    enumeration, not an error worth logging: every device ends that way.
    """
    return ctypes.windll.user32.EnumDisplaySettingsExW(
        ctypes.c_wchar_p(device_name), ctypes.c_int(mode_num),
        ctypes.byref(devmode), 0)


def _enum_devices(index: int, device) -> int:
    """`EnumDisplayDevicesW` over the machine's display adapters."""
    return ctypes.windll.user32.EnumDisplayDevicesW(
        None, wintypes.DWORD(index), ctypes.byref(device), 0)


def modes_for(gdi_device_name: str) -> List[DisplayMode]:
    """Every distinct mode `\\\\.\\DISPLAYn` offers, in enumeration order.

    An empty list is a real answer: DISPLAY6..DISPLAY10 on this machine
    belong to the iGPU and enumerate nothing at all. It is not an error and
    does not raise.
    """
    modes: List[DisplayMode] = []
    seen = set()
    index = 0
    while True:
        devmode = _new_devmode()
        if not _enum_settings(gdi_device_name, index, devmode):
            break
        index += 1
        if not devmode.dmPelsWidth or not devmode.dmPelsHeight:
            continue
        mode = build_mode(devmode.dmPelsWidth, devmode.dmPelsHeight,
                          devmode.dmDisplayFrequency, devmode.dmBitsPerPel,
                          devmode.dmDisplayFlags)
        if mode in seen:
            continue
        seen.add(mode)
        modes.append(mode)
    return modes


def current_mode(gdi_device_name: str) -> Optional[DisplayMode]:
    """The mode the display is running now, or `None` if it is running none.

    `None` for every device that is not attached to the desktop. The call
    can also succeed and leave the buffer zeroed, so the size is checked
    too -- a 0x0 mode at 0 Hz is not a display state, it is an absence
    dressed up as one.
    """
    devmode = _new_devmode()
    if not _enum_settings(gdi_device_name, ENUM_CURRENT_SETTINGS, devmode):
        return None
    if not devmode.dmPelsWidth or not devmode.dmPelsHeight:
        logger.debug("ENUM_CURRENT_SETTINGS succeeded for %s with a zeroed "
                     "DEVMODE -- reporting no current mode", gdi_device_name)
        return None
    return build_mode(devmode.dmPelsWidth, devmode.dmPelsHeight,
                      devmode.dmDisplayFrequency, devmode.dmBitsPerPel,
                      devmode.dmDisplayFlags)


def resolutions(gdi_device_name: str) -> List[Tuple[int, int]]:
    """Distinct resolutions this display offers, biggest first."""
    return resolutions_from(modes_for(gdi_device_name))


def refresh_rates_for(gdi_device_name: str, width: int,
                      height: int) -> List[float]:
    """The rates valid at exactly this resolution, ascending."""
    return refresh_rates_from(modes_for(gdi_device_name), width, height)


def best_mode(gdi_device_name: str,
              native: Optional[Tuple[int, int]] = None) -> Optional[DisplayMode]:
    """The mode to recommend for this device. See `best_from` for the rule.

    Pass `native` — from `monitor_identity.native_resolution` — whenever it
    is known. Without it the answer is largest-resolution-first, which on a
    panel the GPU offers downsampled modes for recommends something both
    softer and slower than the glass can do.
    """
    return best_from(modes_for(gdi_device_name), native=native)


def attached_devices() -> List[Tuple[str, str, bool, bool]]:
    """`(device_name, adapter_string, attached, primary)` for every device.

    Every display device the machine enumerates, attached or not: this
    machine reports 10, of which 2 are on the desktop. The flags are read
    rather than inferred, so a device that is present and unattached is
    reported as exactly that.
    """
    devices: List[Tuple[str, str, bool, bool]] = []
    index = 0
    while True:
        device = _DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
        if not _enum_devices(index, device):
            break
        index += 1
        devices.append((
            device.DeviceName,
            device.DeviceString,
            bool(device.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP),
            bool(device.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE),
        ))
    return devices
