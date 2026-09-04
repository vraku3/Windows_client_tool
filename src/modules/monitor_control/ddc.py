r"""DDC/CI over Dxva2.dll -- brightness, contrast and input source.

This is the engine half of Monitor Control: no Qt, no display needed, and
every call into the driver goes through one small `Dxva2Api` object so the
logic above it can be tested against a fake.

The path Windows makes you walk is
`EnumDisplayMonitors` -> `GetNumberOfPhysicalMonitorsFromHMONITOR` ->
`GetPhysicalMonitorsFromHMONITOR` -> `GetVCPFeatureAndVCPFeatureReply` /
`SetVCPFeature` / `CapabilitiesRequestAndCapabilitiesReply` ->
`DestroyPhysicalMonitors`.

Four things about real monitors shape everything here:

* **Plenty of monitors never answer.** DDC/CI ships disabled in a lot of OSD
  menus, and some panels answer a read and quietly ignore the write. So a
  monitor is never assumed controllable: `probe()` asks, and reports
  `responded=False` WITH the driver's own reason. A refused read is never
  turned into a value -- a `GetVCPFeatureAndVCPFeatureReply` that fails means
  "we could not ask", not "brightness is 0".
* **BOOL TRUE is not proof.** A reply carrying a maximum of 0 for a
  continuous control is junk, not a control with no range, and it is reported
  as "did not give a usable answer" rather than as a 0..0 slider.
* **Input source values past the standard three are vendor-specific.** MCCS
  defines 0x0F DisplayPort-1, 0x11 HDMI-1, 0x12 HDMI-2, and monitors add
  their own freely. The only honest list is what the capabilities string
  claims for VCP 0x60; when there is no capabilities string there is no list,
  and `set_input_source` refuses rather than guessing. Guessing here switches
  the monitor away from the machine that is driving it, which the user then
  cannot undo from this app.
* **Physical monitor handles leak.** They must go back through
  `DestroyPhysicalMonitors`, so `open_monitors()` is a context manager and is
  the intended way in; `list_physical_monitors()` hands you live handles and
  pairs with `close_physical_monitors()`.

A monitor that is connected but not on the desktop (switched off, or an input
the GPU is not driving) has no HMONITOR at all, so it is simply not in the
list. That is "not present", and it is a different thing from "present but
mute" -- which is what `DdcCapability.responded` records.
"""
from __future__ import annotations

import ctypes
import logging
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: VCP codes. The three this module speaks for; the rest are readable through
#: `read_vcp()` for anyone who wants them.
VCP_BRIGHTNESS = 0x10
VCP_CONTRAST = 0x12
VCP_INPUT_SOURCE = 0x60

#: MC_VCP_CODE_TYPE, as Dxva2 reports it.
MC_MOMENTARY = 0
MC_SET_PARAMETER = 1

#: How long to wait after a write before reading it back. A monitor applies a
#: VCP write asynchronously; reading immediately can still return the old
#: value on a panel that did accept it.
WRITE_SETTLE_SECONDS = 0.1

#: MCCS input source values. Everything outside this table is the vendor's
#: own and is NEVER given an invented name.
_INPUT_SOURCES = {
    0x01: "Analog-1",
    0x02: "Analog-2",
    0x03: "DVI-1",
    0x04: "DVI-2",
    0x05: "Composite-1",
    0x06: "Composite-2",
    0x07: "S-video-1",
    0x08: "S-video-2",
    0x09: "Tuner-1",
    0x0A: "Tuner-2",
    0x0B: "Tuner-3",
    0x0C: "Component-1",
    0x0D: "Component-2",
    0x0E: "Component-3",
    0x0F: "DisplayPort-1",
    0x10: "DisplayPort-2",
    0x11: "HDMI-1",
    0x12: "HDMI-2",
}


def input_source_name(value: int) -> str:
    """A name for a 0x60 value, or an honest label for a vendor one."""
    known = _INPUT_SOURCES.get(value)
    if known:
        return known
    return "vendor-specific 0x%02X" % value


class DdcError(OSError):
    """A DDC/CI call failed, carrying the reason it failed."""


# -- data -------------------------------------------------------------------

@dataclass(frozen=True)
class MonitorArray:
    """One `GetPhysicalMonitorsFromHMONITOR` allocation.

    `payload` is the ctypes array itself; `DestroyPhysicalMonitors` wants the
    whole array back, not the individual handles, so it has to be kept.
    """

    handles: List[Tuple[int, str]]
    payload: Any = None


@dataclass(frozen=True)
class PhysicalMonitor:
    """One physical monitor behind one HMONITOR, with a LIVE handle.

    `_owner` is the allocation the handle came out of, so the handle can be
    given back. It takes no part in equality or repr.

    `device` is the GDI device this HMONITOR draws to (`\\\\.\\DISPLAY2`), or
    None when Windows would not say. It is the ONLY reliable way to tie a
    monitor here to one in `view_model` -- `description` is not an identity.
    Measured on this machine: the Gigabyte's description is "Generic PnP
    Monitor" while its view is named "MO27Q28G", so no amount of string
    matching between the two ever succeeds.
    """

    handle: int
    description: str
    hmonitor: int
    device: Optional[str] = None
    _owner: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class VcpValue:
    """A VCP reply, exactly as the monitor sent it.

    `maximum` is the monitor's own range and is never assumed to be 100 --
    but for an ENUMERATED code it is not a range at all and means nothing.
    Measured here: the Gigabyte answers 0x60 with a maximum of 3 while
    claiming four inputs, and the Dell answers 0x1212.

    `current` is left raw. For 0x60 the value lives in the low byte and some
    panels mirror it into the high one -- the Dell reports DisplayPort-1 as
    0x0F0F -- so `low_byte` is what a 0x60 reply MEANS. Masking `current`
    itself would throw away what the monitor actually said.
    """

    code: int
    current: int
    maximum: int
    vcp_type: int = 0

    @property
    def low_byte(self) -> int:
        return self.current & 0xFF


@dataclass(frozen=True)
class DdcCapability:
    """What a monitor will ACTUALLY answer, and why, when it will not.

    `responded` is the load-bearing field: False means DDC/CI is not
    answering on this monitor at all, and no control should be offered for
    it. `reason` is never empty -- a verdict with nothing behind it is the
    bug this dataclass exists to prevent.

    `input_sources` holds only the values the capabilities string CLAIMS for
    VCP 0x60. `input_sources_known` False with an empty tuple means "we could
    not find out", which is not the same as "it has no inputs".
    """

    monitor: Optional[PhysicalMonitor]
    responded: bool
    reason: str
    supports_brightness: bool = False
    supports_contrast: bool = False
    supports_input_source: bool = False
    input_sources: Tuple[int, ...] = ()
    input_sources_known: bool = False
    brightness: Optional[VcpValue] = None
    contrast: Optional[VcpValue] = None
    input_source: Optional[VcpValue] = None
    capabilities_string: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None

    def input_source_choices(self) -> List[Tuple[int, str]]:
        """`(value, label)` for every input this monitor claims -- no more."""
        return [(v, input_source_name(v)) for v in self.input_sources]

    @property
    def current_input(self) -> Optional[int]:
        """The input value the monitor reports, low byte only, or None."""
        if self.input_source is None:
            return None
        return self.input_source.low_byte

    @property
    def current_input_name(self) -> Optional[str]:
        value = self.current_input
        return None if value is None else input_source_name(value)

    @property
    def model(self) -> Optional[str]:
        """The model out of the capabilities string, or None.

        Worth preferring over `PhysicalMonitor.description`: Windows names
        the Gigabyte on this machine "Generic PnP Monitor", and the
        capabilities string is the only place it says "GIGABYTE".
        """
        if not self.capabilities:
            return None
        return self.capabilities.get("model")


@dataclass(frozen=True)
class WriteResult:
    """The outcome of a VCP write, with verification kept separate from it.

    `ok` says the `SetVCPFeature` call returned success. `verified` says
    whether reading the control back agreed: True, False (the monitor took
    the call and ignored it -- common), or None (the read-back itself was
    refused, so we do not know). None is never collapsed into either.
    """

    ok: bool
    code: int
    requested: int
    applied: Optional[int]
    verified: Optional[bool]
    reason: str


# -- the capabilities string ------------------------------------------------

def _split_tags(body: str) -> Tuple[List[Tuple[str, str]], bool]:
    """`name(value)` pairs from a capabilities body, values kept nested.

    Returns the pairs and whether a parenthesis went unclosed. Nesting is the
    whole difficulty: `vcp(... 14(01 05) 16 ...)` has value lists inside the
    value list, and a naive scan to the first `)` ends the vcp tag at `14`'s
    closing paren and loses every code after it.
    """
    tags: List[Tuple[str, str]] = []
    unbalanced = False
    i, n = 0, len(body)
    while i < n:
        while i < n and body[i].isspace():
            i += 1
        start = i
        while i < n and body[i] != "(" and not body[i].isspace():
            i += 1
        name = body[start:i]
        while i < n and body[i].isspace():
            i += 1
        value = ""
        if i < n and body[i] == "(":
            depth = 0
            vstart = i + 1
            while i < n:
                if body[i] == "(":
                    depth += 1
                elif body[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if depth == 0 and i < n:
                value = body[vstart:i]
                i += 1
            else:
                value = body[vstart:]
                unbalanced = True
                i = n
        if name:
            tags.append((name, value))
    return tags, unbalanced


def _hex_bytes(text: str) -> Tuple[Tuple[int, ...], Tuple[str, ...]]:
    """Space-separated hex pairs -> ints, plus whatever would not parse."""
    values: List[int] = []
    unparsed: List[str] = []
    for token in text.split():
        try:
            values.append(int(token, 16))
        except ValueError:
            unparsed.append(token)
    return tuple(values), tuple(unparsed)


def parse_capabilities(text: str) -> Dict[str, Any]:
    """Parse an MCCS capabilities string into plain data.

    Never raises: a monitor's reply is whatever the monitor felt like
    sending, and half of one is still worth reading. `malformed` says the
    string did not have the shape it should, and `unparsed` collects the
    tokens that were dropped, so nothing disappears silently.

    `vcp_present` distinguishes "this string claims no VCP codes" (a real,
    if odd, answer) from "this string has no vcp tag at all" (we do not
    know what it supports). They must not collapse into an empty dict.
    """
    raw = text or ""
    stripped = raw.strip()
    result: Dict[str, Any] = {
        "raw": raw,
        "tags": {},
        "vcp": {},
        "vcp_present": False,
        "cmds": (),
        "model": None,
        "type": None,
        "mccs_ver": None,
        "malformed": False,
        "unparsed": (),
    }

    open_at = stripped.find("(")
    if open_at == -1:
        result["malformed"] = True
        if stripped:
            logger.debug("capabilities reply has no parentheses at all: %r",
                         stripped[:80])
        return result
    if open_at > 0:
        # Junk before the opening paren. Parse what follows anyway, but say so.
        result["malformed"] = True

    body = stripped[open_at + 1:]
    depth = 0
    end = -1
    for idx, ch in enumerate(stripped[open_at:], start=open_at):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = idx
                break
    if end == -1:
        result["malformed"] = True
    else:
        body = stripped[open_at + 1:end]

    tags, unbalanced = _split_tags(body)
    if unbalanced:
        result["malformed"] = True

    unparsed: List[str] = []
    for name, value in tags:
        key = name.lower()
        result["tags"][key] = value
        if key == "vcp":
            result["vcp_present"] = True
            codes, code_unbalanced = _split_tags(value)
            if code_unbalanced:
                result["malformed"] = True
            table: Dict[int, Tuple[int, ...]] = {}
            for code_text, values_text in codes:
                try:
                    code = int(code_text, 16)
                except ValueError:
                    unparsed.append(code_text)
                    continue
                discrete, bad_values = _hex_bytes(values_text)
                unparsed.extend(bad_values)
                table[code] = discrete
            result["vcp"] = table
        elif key == "cmds":
            commands, bad_cmds = _hex_bytes(value)
            unparsed.extend(bad_cmds)
            result["cmds"] = commands
        elif key in ("model", "type", "mccs_ver"):
            result[key] = value.strip() or None

    result["unparsed"] = tuple(unparsed)
    return result


def claimed_input_sources(parsed: Optional[Dict[str, Any]]
                          ) -> Tuple[Tuple[int, ...], bool]:
    """`(values, known)` for VCP 0x60 out of a parsed capabilities dict.

    `known` False means the string never said, and the caller must not fill
    the gap with the standard three -- half the monitors on a desk have an
    input the standard does not name.
    """
    if not parsed or not parsed.get("vcp_present"):
        return (), False
    values = parsed["vcp"].get(VCP_INPUT_SOURCE)
    if not values:
        return (), False
    return tuple(values), True


def clamp_vcp(value: int, maximum: int) -> int:
    """Hold a value inside the range the MONITOR reported, not a guessed one."""
    return max(0, min(int(value), int(maximum)))


# -- the driver layer -------------------------------------------------------

class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("hPhysicalMonitor", wintypes.HANDLE),
                ("szPhysicalMonitorDescription", wintypes.WCHAR * 128)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE,
                                      wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT),
                                      wintypes.LPARAM)


class _MONITORINFOEXW(ctypes.Structure):
    """`szDevice` is what this is for -- the rest is the price of asking."""

    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


def _last_error_text(call: str) -> str:
    """The driver's own reason, named and numbered.

    A BOOL FALSE with `GetLastError() == 0` happens, and it is reported as
    exactly that rather than dressed up -- an unexplained failure is still
    information, and pretending it was a specific error is not.
    """
    code = ctypes.get_last_error()
    if not code:
        return "%s returned FALSE with no error code" % call
    try:
        text = ctypes.FormatError(code).strip()
    except (ValueError, OSError):       # pragma: no cover - defensive
        text = ""
    unsigned = code & 0xFFFFFFFF
    if text:
        return "%s failed: %s (0x%08X)" % (call, text, unsigned)
    return "%s failed: error 0x%08X" % (call, unsigned)


class Dxva2Api:
    """The only object in this module that touches the driver.

    Every method raises `DdcError` with the driver's reason on failure; none
    of them return a sentinel that could be mistaken for a value.
    """

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._dxva2 = ctypes.WinDLL("Dxva2.dll", use_last_error=True)

        self._user32.EnumDisplayMonitors.restype = wintypes.BOOL
        self._user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC, ctypes.c_void_p, _MONITORENUMPROC, wintypes.LPARAM]
        self._user32.GetMonitorInfoW.restype = wintypes.BOOL
        self._user32.GetMonitorInfoW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_MONITORINFOEXW)]

        dxva2 = self._dxva2
        dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
        dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
        dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [
            wintypes.HANDLE, wintypes.DWORD,
            ctypes.POINTER(_PHYSICAL_MONITOR)]
        dxva2.DestroyPhysicalMonitors.restype = wintypes.BOOL
        dxva2.DestroyPhysicalMonitors.argtypes = [
            wintypes.DWORD, ctypes.POINTER(_PHYSICAL_MONITOR)]
        dxva2.GetVCPFeatureAndVCPFeatureReply.restype = wintypes.BOOL
        dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [
            wintypes.HANDLE, wintypes.BYTE, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
        dxva2.SetVCPFeature.restype = wintypes.BOOL
        dxva2.SetVCPFeature.argtypes = [wintypes.HANDLE, wintypes.BYTE,
                                        wintypes.DWORD]
        dxva2.GetCapabilitiesStringLength.restype = wintypes.BOOL
        dxva2.GetCapabilitiesStringLength.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        dxva2.CapabilitiesRequestAndCapabilitiesReply.restype = wintypes.BOOL
        dxva2.CapabilitiesRequestAndCapabilitiesReply.argtypes = [
            wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD]

    # -- enumeration --

    def enum_hmonitors(self) -> List[int]:
        """Every HMONITOR on the desktop, in enumeration order.

        A monitor that is connected but not part of the desktop has none, and
        will not appear. That is the correct answer, not an omission.
        """
        found: List[int] = []

        @_MONITORENUMPROC
        def _collect(hmonitor, _hdc, _rect, _lparam):
            found.append(int(hmonitor))
            return True

        ctypes.set_last_error(0)
        if not self._user32.EnumDisplayMonitors(None, None, _collect, 0):
            raise DdcError(_last_error_text("EnumDisplayMonitors"))
        return found

    def monitor_device_name(self, hmonitor: int) -> str:
        r"""The GDI device this HMONITOR draws to (`\\.\DISPLAY2`).

        Raises `DdcError` rather than returning "" -- an unnamed monitor and
        one whose name was refused are different, and only the caller can
        decide what to do about the second.
        """
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        ctypes.set_last_error(0)
        if not self._user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            raise DdcError(_last_error_text("GetMonitorInfoW"))
        return info.szDevice

    def open_physical_monitors(self, hmonitor: int) -> MonitorArray:
        count = wintypes.DWORD()
        ctypes.set_last_error(0)
        ok = self._dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(
            hmonitor, ctypes.byref(count))
        if not ok:
            raise DdcError(_last_error_text(
                "GetNumberOfPhysicalMonitorsFromHMONITOR"))
        if count.value == 0:
            return MonitorArray(handles=[], payload=None)

        array = (_PHYSICAL_MONITOR * count.value)()
        ctypes.set_last_error(0)
        ok = self._dxva2.GetPhysicalMonitorsFromHMONITOR(
            hmonitor, count.value, array)
        if not ok:
            raise DdcError(_last_error_text(
                "GetPhysicalMonitorsFromHMONITOR"))
        handles = [(int(entry.hPhysicalMonitor or 0),
                    entry.szPhysicalMonitorDescription)
                   for entry in array]
        return MonitorArray(handles=handles, payload=array)

    def destroy(self, array: MonitorArray) -> None:
        if array.payload is None or not array.handles:
            return
        ctypes.set_last_error(0)
        if not self._dxva2.DestroyPhysicalMonitors(len(array.handles),
                                                   array.payload):
            raise DdcError(_last_error_text("DestroyPhysicalMonitors"))

    # -- VCP --

    def get_vcp(self, handle: int, code: int) -> Tuple[int, int, int]:
        vcp_type = wintypes.DWORD()
        current = wintypes.DWORD()
        maximum = wintypes.DWORD()
        ctypes.set_last_error(0)
        ok = self._dxva2.GetVCPFeatureAndVCPFeatureReply(
            handle, code, ctypes.byref(vcp_type), ctypes.byref(current),
            ctypes.byref(maximum))
        if not ok:
            raise DdcError(_last_error_text(
                "GetVCPFeatureAndVCPFeatureReply(0x%02X)" % code))
        return vcp_type.value, current.value, maximum.value

    def set_vcp(self, handle: int, code: int, value: int) -> None:
        ctypes.set_last_error(0)
        if not self._dxva2.SetVCPFeature(handle, code, value):
            raise DdcError(_last_error_text("SetVCPFeature(0x%02X)" % code))

    def capabilities(self, handle: int) -> str:
        length = wintypes.DWORD()
        ctypes.set_last_error(0)
        if not self._dxva2.GetCapabilitiesStringLength(handle,
                                                       ctypes.byref(length)):
            raise DdcError(_last_error_text("GetCapabilitiesStringLength"))
        if length.value == 0:
            raise DdcError("GetCapabilitiesStringLength reported a length of "
                           "0 - the monitor has no capabilities string")
        buffer = ctypes.create_string_buffer(length.value)
        ctypes.set_last_error(0)
        if not self._dxva2.CapabilitiesRequestAndCapabilitiesReply(
                handle, buffer, length.value):
            raise DdcError(_last_error_text(
                "CapabilitiesRequestAndCapabilitiesReply"))
        return buffer.value.decode("ascii", "replace")


_api_singleton: Optional[Dxva2Api] = None


def _resolve(api: Optional[Any]) -> Any:
    """The caller's api, or the real one -- built once, lazily.

    Building it can raise `OSError` (no Dxva2.dll), which is left to
    propagate: a machine with no DDC/CI support at all is a fact, not a
    value to be invented.
    """
    global _api_singleton
    if api is not None:
        return api
    if _api_singleton is None:
        _api_singleton = Dxva2Api()
    return _api_singleton


# -- enumeration ------------------------------------------------------------

def list_physical_monitors(api: Optional[Any] = None) -> List[PhysicalMonitor]:
    """Every physical monitor behind every HMONITOR, with LIVE handles.

    The caller owns the handles and must pass the list to
    `close_physical_monitors()`. Prefer `open_monitors()`, which does that
    for you even when the body raises.

    A monitor that is not on the desktop is not here. `find_monitor()`
    returning None is "not present"; it is a different answer from
    `probe().responded` being False, which is "present but not answering".
    """
    api = _resolve(api)
    monitors: List[PhysicalMonitor] = []
    for hmonitor in api.enum_hmonitors():
        try:
            array = api.open_physical_monitors(hmonitor)
        except DdcError as exc:
            # One dead HMONITOR must not hide the others.
            logger.warning("could not open physical monitors for HMONITOR "
                           "0x%X: %s", hmonitor, exc)
            continue
        # A name we could not read is None, never "": one monitor Windows
        # will not name must not cost the others, and must not silently
        # match a view that also has no device name.
        device: Optional[str] = None
        try:
            device = api.monitor_device_name(hmonitor) or None
        except DdcError as exc:
            logger.warning("no GDI device name for HMONITOR 0x%X: %s",
                           hmonitor, exc)
        for handle, description in array.handles:
            monitors.append(PhysicalMonitor(handle=handle,
                                            description=description,
                                            hmonitor=hmonitor,
                                            device=device,
                                            _owner=array))
    return monitors


def close_physical_monitors(monitors: Sequence[PhysicalMonitor],
                            api: Optional[Any] = None) -> None:
    """Give every handle back, once per allocation.

    `DestroyPhysicalMonitors` takes the whole array, so monitors that came
    out of one call are destroyed together, and each array only once.
    """
    api = _resolve(api)
    seen = []
    for monitor in monitors:
        owner = monitor._owner
        if owner is None or any(owner is other for other in seen):
            continue
        seen.append(owner)
        try:
            api.destroy(owner)
        except DdcError as exc:
            # Keep going: the remaining arrays still have to be released.
            logger.warning("DestroyPhysicalMonitors failed: %s", exc)


@contextmanager
def open_monitors(api: Optional[Any] = None
                  ) -> Iterator[List[PhysicalMonitor]]:
    """The safe way in: physical monitors, always destroyed on the way out."""
    monitors = list_physical_monitors(api=api)
    try:
        yield monitors
    finally:
        close_physical_monitors(monitors, api=api)


def find_monitor(monitors: Sequence[PhysicalMonitor],
                 needle: str) -> Optional[PhysicalMonitor]:
    """First monitor whose description contains `needle`, or None.

    None means the monitor is not on the desktop -- it has no HMONITOR, so
    there is nothing to talk to. Not an error.
    """
    lowered = (needle or "").lower()
    for monitor in monitors:
        if lowered in (monitor.description or "").lower():
            return monitor
    return None


def find_monitor_for_device(monitors: Sequence[PhysicalMonitor],
                            device_name: Optional[str]
                            ) -> Optional[PhysicalMonitor]:
    r"""The monitor drawing to `device_name` (`\\.\DISPLAY2`), or None.

    This is the pairing `view_model` needs, and it is exact: both sides get
    the string from Windows, so there is nothing to normalise. Matching on
    `description` instead does not work at all -- the Gigabyte on this
    machine is "Generic PnP Monitor" to DDC and "MO27Q28G" to the view.

    An empty or missing `device_name` matches nothing rather than the first
    monitor: an inactive panel has no GDI device, and quietly handing back
    somebody else's would point every control at the wrong screen.
    """
    if not device_name:
        return None
    for monitor in monitors:
        if monitor.device and monitor.device == device_name:
            return monitor
    return None


# -- reads ------------------------------------------------------------------

#: Codes whose value is an enumeration, not a range. Their `maximum` carries
#: no information -- measured 3 on one monitor and 0x1212 on another, for the
#: same control -- so it is never used to judge the reply.
_ENUMERATED_CODES = frozenset({VCP_INPUT_SOURCE})


def _read(api: Any, monitor: PhysicalMonitor,
          code: int) -> Tuple[Optional[VcpValue], str]:
    """`(value, reason)`. `value` is None when we could not ask, never 0.

    For a continuous control a reply with a maximum of 0 is refused here
    rather than passed on: a slider with no range is not an answer, and a
    BOOL TRUE does not make it one. For an enumerated control the maximum is
    meaningless in the first place, so it decides nothing.
    """
    try:
        vcp_type, current, maximum = api.get_vcp(monitor.handle, code)
    except DdcError as exc:
        return None, str(exc)
    if maximum == 0 and code not in _ENUMERATED_CODES:
        return None, ("VCP 0x%02X replied with a maximum of 0, which is not a "
                      "usable range" % code)
    return VcpValue(code=code, current=current, maximum=maximum,
                    vcp_type=vcp_type), ""


def read_vcp(monitor: PhysicalMonitor, code: int,
             api: Optional[Any] = None) -> Optional[VcpValue]:
    """Any VCP code, or None when the monitor would not answer."""
    value, reason = _read(_resolve(api), monitor, code)
    if value is None:
        logger.info("%s: %s", monitor.description, reason)
    return value


def get_brightness(monitor: PhysicalMonitor,
                   api: Optional[Any] = None) -> Optional[VcpValue]:
    """Current and maximum brightness, or None if the monitor did not answer."""
    return read_vcp(monitor, VCP_BRIGHTNESS, api=api)


def get_contrast(monitor: PhysicalMonitor,
                 api: Optional[Any] = None) -> Optional[VcpValue]:
    return read_vcp(monitor, VCP_CONTRAST, api=api)


def get_input_source(monitor: PhysicalMonitor,
                     api: Optional[Any] = None) -> Optional[VcpValue]:
    """The input the monitor says it is showing, or None if it did not say."""
    return read_vcp(monitor, VCP_INPUT_SOURCE, api=api)


def capabilities_string(monitor: PhysicalMonitor,
                        api: Optional[Any] = None) -> Optional[str]:
    """The raw MCCS capabilities string, or None when it was refused."""
    api = _resolve(api)
    try:
        return api.capabilities(monitor.handle)
    except DdcError as exc:
        logger.info("%s: %s", monitor.description, exc)
        return None


# -- probe ------------------------------------------------------------------

def probe(monitor: PhysicalMonitor, api: Optional[Any] = None) -> DdcCapability:
    """Ask the monitor what it will actually answer. Read-only.

    Four questions -- the capabilities string, then 0x10, 0x12 and 0x60 --
    and the answers are believed over the claims: a capabilities string that
    lists 0x12 on a panel that refuses to report 0x12 does not make contrast
    supported. Every refusal ends up in `reason`.
    """
    api = _resolve(api)

    caps_text: Optional[str] = None
    caps_parsed: Optional[Dict[str, Any]] = None
    notes: List[str] = []
    failures: List[str] = []

    try:
        caps_text = api.capabilities(monitor.handle)
        caps_parsed = parse_capabilities(caps_text)
        if caps_parsed["malformed"]:
            notes.append("the capabilities string is malformed and was only "
                         "partly understood")
    except DdcError as exc:
        failures.append(str(exc))
        notes.append("capabilities string unavailable (%s), so the discrete "
                     "values for VCP 0x60 are unknown" % exc)

    brightness, brightness_reason = _read(api, monitor, VCP_BRIGHTNESS)
    contrast, contrast_reason = _read(api, monitor, VCP_CONTRAST)
    source, source_reason = _read(api, monitor, VCP_INPUT_SOURCE)

    for value, reason in ((brightness, brightness_reason),
                          (contrast, contrast_reason),
                          (source, source_reason)):
        if value is None:
            failures.append(reason)
            notes.append(reason)

    answered_anything = (caps_text is not None or brightness is not None
                         or contrast is not None or source is not None)

    sources, sources_known = claimed_input_sources(caps_parsed)
    if caps_parsed is not None and not sources_known and source is not None:
        notes.append("the capabilities string claims no discrete values for "
                     "VCP 0x60, so no input list can be offered")

    if not answered_anything:
        reason = "DDC/CI not responding: " + (
            failures[0] if failures else "the monitor answered nothing")
        if len(failures) > 1:
            reason += " (and %d further refusal(s))" % (len(failures) - 1)
        return DdcCapability(monitor=monitor, responded=False, reason=reason,
                             capabilities_string=caps_text,
                             capabilities=caps_parsed)

    supported = [name for name, value in (("brightness", brightness),
                                          ("contrast", contrast),
                                          ("input source", source))
                 if value is not None]
    reason = "DDC/CI responding"
    reason += (": " + ", ".join(supported)) if supported else \
        ": the capabilities string was readable but no VCP code answered"
    if notes:
        reason += ". " + ". ".join(notes)

    return DdcCapability(
        monitor=monitor,
        responded=True,
        reason=reason,
        supports_brightness=brightness is not None,
        supports_contrast=contrast is not None,
        supports_input_source=source is not None,
        input_sources=sources,
        input_sources_known=sources_known,
        brightness=brightness,
        contrast=contrast,
        input_source=source,
        capabilities_string=caps_text,
        capabilities=caps_parsed,
    )


# -- writes -----------------------------------------------------------------
#
# NOTE: nothing in this module calls SetVCPFeature on its own. These run only
# when a caller asks for a specific value, and every one of them reads the
# control back afterwards -- a monitor accepting the call is not evidence it
# moved.

def _write_and_verify(api: Any, monitor: PhysicalMonitor, code: int,
                      requested: int, applied: int,
                      settle: float, compare_mask: int = 0xFFFF) -> WriteResult:
    r"""Write a VCP value and read it back to see whether it took.

    `compare_mask` exists because not every VCP code reports back the way it
    was written. For an enumerated code like input source (0x60) the value
    lives in the LOW byte; the high byte is reserved, and a panel that echoes
    the value into both returns 0x1111 for a write of 0x11. Compared whole,
    that reads as "the write was accepted and ignored" — a false negative on
    a switch that actually worked. Continuous codes like brightness do use
    the full 16 bits, so they keep the exact comparison.
    """
    try:
        api.set_vcp(monitor.handle, code, applied)
    except DdcError as exc:
        return WriteResult(ok=False, code=code, requested=requested,
                           applied=None, verified=None,
                           reason="the write was refused: %s" % exc)

    if settle:
        time.sleep(settle)

    read_back, reason = _read(api, monitor, code)
    if read_back is None:
        return WriteResult(
            ok=True, code=code, requested=requested, applied=applied,
            verified=None,
            reason=("wrote %d to VCP 0x%02X, but the read-back could not be "
                    "made (%s), so this is unconfirmed"
                    % (applied, code, reason)))
    if (read_back.current & compare_mask) != (applied & compare_mask):
        return WriteResult(
            ok=True, code=code, requested=requested, applied=applied,
            verified=False,
            reason=("wrote %d to VCP 0x%02X but the monitor still reports "
                    "%d - the write was accepted and ignored"
                    % (applied, code, read_back.current)))
    return WriteResult(ok=True, code=code, requested=requested,
                       applied=applied, verified=True,
                       reason=("VCP 0x%02X is %d, confirmed by reading it "
                               "back" % (code, applied)))


def set_brightness(monitor: PhysicalMonitor, value: int,
                   api: Optional[Any] = None,
                   settle: float = WRITE_SETTLE_SECONDS) -> WriteResult:
    """Set brightness (VCP 0x10), clamped to the monitor's OWN maximum.

    The maximum is read first and the write is refused if it cannot be: a
    monitor whose range is unknown cannot be clamped, and assuming 100 is a
    guess that writes an out-of-range value into a panel.
    """
    api = _resolve(api)
    current, reason = _read(api, monitor, VCP_BRIGHTNESS)
    if current is None:
        return WriteResult(
            ok=False, code=VCP_BRIGHTNESS, requested=value, applied=None,
            verified=None,
            reason=("could not read the current brightness, so there is no "
                    "range to clamp to and nothing was written: %s" % reason))
    applied = clamp_vcp(value, current.maximum)
    return _write_and_verify(api, monitor, VCP_BRIGHTNESS, value, applied,
                             settle)


def set_contrast(monitor: PhysicalMonitor, value: int,
                 api: Optional[Any] = None,
                 settle: float = WRITE_SETTLE_SECONDS) -> WriteResult:
    """Set contrast (VCP 0x12), on the same terms as brightness."""
    api = _resolve(api)
    current, reason = _read(api, monitor, VCP_CONTRAST)
    if current is None:
        return WriteResult(
            ok=False, code=VCP_CONTRAST, requested=value, applied=None,
            verified=None,
            reason=("could not read the current contrast, so there is no "
                    "range to clamp to and nothing was written: %s" % reason))
    applied = clamp_vcp(value, current.maximum)
    return _write_and_verify(api, monitor, VCP_CONTRAST, value, applied,
                             settle)


def set_input_source(monitor: PhysicalMonitor, value: int,
                     api: Optional[Any] = None,
                     allowed: Optional[Sequence[int]] = None,
                     settle: float = WRITE_SETTLE_SECONDS) -> WriteResult:
    """Switch input (VCP 0x60) -- only to a value the monitor CLAIMS.

    Input source is not clamped, it is validated: the values are an
    enumeration, not a range, so 0x13 is not "a bit past HDMI-2", it is
    nothing at all. When the capabilities string could not be read there is
    no list to validate against and the write is refused, because the cost of
    guessing wrong is a monitor showing a different machine -- which cannot
    then be undone from this one.

    Pass `allowed` to skip the extra probe when the caller already has one.
    """
    api = _resolve(api)
    if allowed is None:
        capability = probe(monitor, api=api)
        if not capability.input_sources_known:
            return WriteResult(
                ok=False, code=VCP_INPUT_SOURCE, requested=value,
                applied=None, verified=None,
                reason=("the capabilities string does not say which input "
                        "values this monitor accepts, so nothing was "
                        "written: %s" % capability.reason))
        allowed = capability.input_sources

    if value not in tuple(allowed):
        return WriteResult(
            ok=False, code=VCP_INPUT_SOURCE, requested=value, applied=None,
            verified=None,
            reason=("this monitor does not claim input 0x%02X (%s); it claims "
                    "%s" % (value, input_source_name(value),
                            ", ".join("0x%02X (%s)" % (v, input_source_name(v))
                                      for v in allowed) or "nothing")))

    # Low byte only: 0x60 is an enumeration whose value lives in SL, and a
    # panel that mirrors it into SH would otherwise read as having ignored a
    # switch it actually made.
    return _write_and_verify(api, monitor, VCP_INPUT_SOURCE, value, value,
                             settle, compare_mask=0xFF)


# -- by GDI device: the form the UI uses ------------------------------------
#
# The tab holds a `MonitorView`, which knows `\\.\DISPLAY2` and nothing about
# HMONITORs. Everything below opens the monitors, does one thing, and gives
# the handles straight back.
#
# That is not politeness, it is the only correct shape: a `PhysicalMonitor`
# handle is owned by the `open_monitors()` block it came out of, so a UI that
# kept one from the last refresh would be calling into a handle
# `DestroyPhysicalMonitors` has already taken back. Re-finding the monitor per
# operation costs one enumeration and removes that whole class of bug.

def _no_such_device(code: int, requested: int,
                    device_name: Optional[str]) -> WriteResult:
    """The refusal used when nothing on the desktop draws to that device."""
    return WriteResult(
        ok=False, code=code, requested=requested, applied=None,
        verified=None,
        reason=("no monitor on the desktop draws to %s, so nothing was "
                "written" % (device_name or "(no device name)")))


def probe_device(device_name: Optional[str],
                 api: Optional[Any] = None) -> DdcCapability:
    r"""Probe the monitor drawing to `device_name`. Read-only.

    The returned capability carries **no handle** (`monitor` is None): the
    one it was probed through died with the `open_monitors()` block, and
    handing a dangling handle to a UI that refreshes on a timer is precisely
    the bug this shape avoids. Use the `*_for_device` writers instead.

    A device nothing draws to answers `responded=False` with a reason that
    says so -- which is "not present", a different thing from the
    `responded=False` of a monitor that is there and will not talk.
    """
    with open_monitors(api=api) as monitors:
        monitor = find_monitor_for_device(monitors, device_name)
        if monitor is None:
            return DdcCapability(
                monitor=None, responded=False,
                reason=("no monitor on the desktop draws to %s, so there is "
                        "nothing to ask" % (device_name or "(no device name)")))
        return replace(probe(monitor, api=api), monitor=None)


def set_brightness_for_device(device_name: Optional[str], value: int,
                              api: Optional[Any] = None,
                              settle: float = WRITE_SETTLE_SECONDS
                              ) -> WriteResult:
    """Set brightness on the monitor drawing to `device_name`."""
    with open_monitors(api=api) as monitors:
        monitor = find_monitor_for_device(monitors, device_name)
        if monitor is None:
            return _no_such_device(VCP_BRIGHTNESS, value, device_name)
        return set_brightness(monitor, value, api=api, settle=settle)


def set_contrast_for_device(device_name: Optional[str], value: int,
                            api: Optional[Any] = None,
                            settle: float = WRITE_SETTLE_SECONDS
                            ) -> WriteResult:
    """Set contrast on the monitor drawing to `device_name`."""
    with open_monitors(api=api) as monitors:
        monitor = find_monitor_for_device(monitors, device_name)
        if monitor is None:
            return _no_such_device(VCP_CONTRAST, value, device_name)
        return set_contrast(monitor, value, api=api, settle=settle)


def set_input_source_for_device(device_name: Optional[str], value: int,
                                api: Optional[Any] = None,
                                allowed: Optional[Sequence[int]] = None,
                                settle: float = WRITE_SETTLE_SECONDS
                                ) -> WriteResult:
    """Switch input on the monitor drawing to `device_name`.

    `allowed` still means what it does on `set_input_source`: pass the list
    the caller already probed to skip a second capabilities read. Without
    it the value is validated against a fresh probe, and an unclaimed value
    is refused rather than guessed at.
    """
    with open_monitors(api=api) as monitors:
        monitor = find_monitor_for_device(monitors, device_name)
        if monitor is None:
            return _no_such_device(VCP_INPUT_SOURCE, value, device_name)
        return set_input_source(monitor, value, api=api, allowed=allowed,
                                settle=settle)
