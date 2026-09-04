r"""Display-audio endpoints: which monitor carries which sound device.

A monitor connected over HDMI or DisplayPort brings an audio endpoint with
it. Windows names those endpoints per display — "2 - MO27Q28G" — which is
the only thread tying an audio device back to a panel, and it is a thin one.

Everything here reads. Enumeration goes through
`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render`,
which needs no elevation and no COM; the default output device goes through
`IMMDeviceEnumerator::GetDefaultAudioEndpoint` over a raw ctypes vtable,
because `comtypes` is not a dependency of this project and is not being made
one.

**THE COM WRITE PATH IS UNVERIFIED.** `set_endpoint_enabled` drives
`IPolicyConfig::SetEndpointVisibility`, an undocumented interface that
Microsoft has never published a header for. Nothing in this file has ever
been executed against a real endpoint: disabling the wrong one takes the
sound off the machine, and the machine this was written on was in use. Both
write paths are therefore behind a `confirm_supervised=True` interlock, and
refuse otherwise — see `SupervisionRequired`.

**That round-trip has now been done** (2026-09-04, this machine): hide ->
re-read -> show -> re-read, three times over, on an active display endpoint
that was not the default output. `SetEndpointVisibility` returned S_OK every
time and the state moved and came back deterministically — though not where
it was expected to. It does not touch the documented nibble at all: it
flips `DEVICE_STATE_HIDDEN` (`0x10000000`), leaving the endpoint ACTIVE and
merely invisible. The interlock stays, no longer because the path is
unproven but because hiding an endpoint is a machine-wide change that the
caller must mean.

Four things about the real data would break an implementation written from
the documentation alone. All four are handled here rather than found later.

* **`DeviceState` carries undocumented bits.** The live display endpoints on
  the reference machine report `0x10000001`: ACTIVE (1) *plus* an
  undocumented `0x10000000`. `raw == DEVICE_STATE_ACTIVE` is False for those,
  so equality reports the monitors that are playing audio right now as being
  in an unknown state. Only the documented nibble is ever compared; the rest
  is reported by `undocumented_state_bits`, not discarded.

* **Most endpoints are ghosts.** 21 of 25 on the reference machine are
  NOTPRESENT — every headset, dock and monitor ever plugged in. Anything
  assuming the registry lists present hardware picks a device that has not
  existed for years.

* **Endpoint names are not unique.** The same panel appears as both
  "2 - MO27Q28G" and "4 - MO27Q28G" here, remembered on two display indices.
  Two *identical* monitors go further and produce two identically-named live
  endpoints, which nothing in the registry can tell apart.

* **Mapping a monitor to an endpoint is therefore FALLIBLE.** When more than
  one live endpoint answers to a name, `endpoint_for_monitor` returns None
  and `ambiguous_matches` returns the contenders. Picking one would be a coin
  flip presented as an answer.

Sources for the `IPolicyConfig` vtable layout, which is the one number in
this file that cannot be checked without writing:

* https://github.com/DanStevens/AudioEndPointController/blob/master/EndPointController/PolicyConfig.h
* https://docs.rs/com-policy-config/latest/src/com_policy_config/lib.rs.html

Both give the same order for IID `{f8679f50-...}`: three IUnknown slots, then
GetMixFormat, GetDeviceFormat, **ResetDeviceFormat**, SetDeviceFormat,
GetProcessingPeriod, SetProcessingPeriod, GetShareMode, SetShareMode,
GetPropertyValue, SetPropertyValue, SetDefaultEndpoint, SetEndpointVisibility
— putting SetEndpointVisibility at vtable index **14**, not 12. Index 12 is
`SetPropertyValue(PCWSTR, const PROPERTYKEY&, PROPVARIANT*)`; calling that
with a string and an int passes an integer where a `PROPERTYKEY*` is
expected. The older `IPolicyConfigVista` IID `{568b9108-...}` omits
ResetDeviceFormat and lands SetEndpointVisibility at 13, which is why both
constants exist and why the Vista IID is tried as a fallback.
"""
from __future__ import annotations

import ctypes
import logging
import re
import winreg
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: The endpoint list. Readable by an ordinary user; no COM, no elevation.
MMDEVICES_RENDER_KEY = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render")

#: PKEY_Device_FriendlyName — "2 - MO27Q28G", the per-display name.
PKEY_DEVICE_FRIENDLY_NAME = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"

#: PKEY_Device_DeviceDesc — "AMD High Definition Audio Device", the driver.
PKEY_DEVICE_DESCRIPTION = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"

# ── DEVICE_STATE_XXX, from mmdeviceapi.h ───────────────────────────────

DEVICE_STATE_ACTIVE = 0x00000001
DEVICE_STATE_DISABLED = 0x00000002
DEVICE_STATE_NOTPRESENT = 0x00000004
DEVICE_STATE_UNPLUGGED = 0x00000008

#: The only bits Microsoft documents. Everything above them is Windows'
#: business, and is reported rather than assumed away.
DEVICE_STATE_MASK = 0x0000000F

#: NOT from mmdeviceapi.h -- measured, on this machine, 2026-09-04.
#:
#: `IPolicyConfig::SetEndpointVisibility(id, 0)` SETS this bit and
#: `(id, 1)` clears it, six times out of six across three round trips, with
#: the documented nibble untouched at ACTIVE throughout. So it is the
#: "hidden from the sound device list" flag -- Windows' own "Don't allow" --
#: and NOT the driver quirk it looked like before anything wrote to it.
#:
#: That matters twice over. It is why an endpoint can be ACTIVE and still be
#: invisible in Sound settings, so `state` alone cannot answer "is this on?"
#: -- `is_hidden` does. And it retires the earlier reading of this bit: the
#: machine this module was written on reported `0x10000001` on every live
#: display endpoint because those endpoints were HIDDEN, not because its
#: driver differed from this one's.
DEVICE_STATE_HIDDEN = 0x10000000


class EndpointState(str, Enum):
    """What Windows says about an endpoint. `UNKNOWN` means exactly that."""

    ACTIVE = "active"
    DISABLED = "disabled"
    NOTPRESENT = "not present"
    UNPLUGGED = "unplugged"
    UNKNOWN = "unknown"


_STATE_BY_BIT: Dict[int, EndpointState] = {
    DEVICE_STATE_ACTIVE: EndpointState.ACTIVE,
    DEVICE_STATE_DISABLED: EndpointState.DISABLED,
    DEVICE_STATE_NOTPRESENT: EndpointState.NOTPRESENT,
    DEVICE_STATE_UNPLUGGED: EndpointState.UNPLUGGED,
}


class AudioEndpointError(RuntimeError):
    """The endpoint list could not be read, with the reason.

    Never raised to mean "there are none" — that is an empty list. A refused
    or missing read is an error, because reporting it as an empty machine is
    how someone concludes they have no sound hardware.
    """


class AudioPolicyError(RuntimeError):
    """A write through IPolicyConfig failed, carrying the HRESULT."""


class SupervisionRequired(RuntimeError):
    """A write was attempted without `confirm_supervised=True`.

    The interlock exists because this file's write paths have never been run
    against a real endpoint and the obvious failure mode is a machine with no
    sound. It is not a permission check; it is a statement that the caller
    has read the module docstring and is watching.
    """


def decode_state(raw: Optional[int]) -> EndpointState:
    """`DeviceState` -> a state, masking off the undocumented bits.

    `None` (the value could not be read) is UNKNOWN, never ACTIVE. So is a
    value with no documented bit set, and so is one with two of them set —
    the four states are mutually exclusive, so two at once means Windows is
    saying something this code does not understand.
    """
    if raw is None:
        return EndpointState.UNKNOWN
    documented = raw & DEVICE_STATE_MASK
    state = _STATE_BY_BIT.get(documented)
    if state is None:
        logger.warning("DeviceState 0x%08X has no single documented state bit "
                       "(masked to 0x%X); reporting unknown", raw, documented)
        return EndpointState.UNKNOWN
    return state


def undocumented_state_bits(raw: Optional[int]) -> int:
    """Whatever `DeviceState` carried above the documented nibble.

    Reported so a change in Windows is visible rather than silently masked
    away. Mostly this is `DEVICE_STATE_HIDDEN`, which is not a quirk at all
    but the visibility flag — see that constant. An empty result is a normal
    reading of a visible endpoint, not a sign the read failed.
    """
    if raw is None:
        return 0
    return raw & ~DEVICE_STATE_MASK


def is_hidden(raw: Optional[int]) -> Optional[bool]:
    """Is this endpoint hidden from the sound device list?

    None when `DeviceState` could not be read — never False, which would
    claim an endpoint is visible on the strength of a read that failed.

    Kept separate from `decode_state` because they answer different
    questions and an endpoint routinely disagrees with itself: hiding one
    leaves it ACTIVE and merely invisible, so `state` says it is working
    and this says you cannot pick it.
    """
    if raw is None:
        return None
    return bool(raw & DEVICE_STATE_HIDDEN)


# ── naming ─────────────────────────────────────────────────────────────

#: Windows' per-display endpoint name: "<display index> - <monitor name>".
#: Only display-audio sinks are named this way, which is what makes the
#: monitor mapping possible at all.
_NUMBERED_NAME_RE = re.compile(r"^\s*(\d+)\s*-\s*(\S.*?)\s*$")

#: Endpoint names that mean "a sink on a graphics adapter" but name no
#: monitor. Only display audio behind a display driver — a Realtek codec's
#: "Digital Output" is S/PDIF and has nothing to do with a monitor.
_BARE_GPU_SINK_NAMES = frozenset({
    "digital output",
    "digital display audio",
    "digital output device",
})

#: Names that say "display" on their own face.
_SELF_EVIDENT_SINK_RE = re.compile(r"hdmi|displayport|display\s*audio",
                                   re.IGNORECASE)

#: A description belonging to a graphics adapter's audio driver.
_DISPLAY_DRIVER_RE = re.compile(
    r"display\s*audio"
    r"|\b(amd|ati|nvidia|intel)\b.*high\s+definition\s+audio",
    re.IGNORECASE)


def parse_monitor_label(friendly_name: Optional[str]) -> Tuple[Optional[int],
                                                               Optional[str]]:
    """`"2 - MO27Q28G"` -> `(2, "MO27Q28G")`; anything else -> `(None, None)`.

    Returning None rather than the raw name matters: "Digital Output" names
    no monitor, and letting it stand in for one makes every GPU sink look
    like a panel called "Digital Output".
    """
    if not friendly_name:
        return None, None
    match = _NUMBERED_NAME_RE.match(friendly_name)
    if not match:
        return None, None
    return int(match.group(1)), match.group(2)


def looks_like_display_audio(friendly_name: Optional[str],
                             device_description: Optional[str]) -> bool:
    """Is this endpoint a monitor's audio sink?

    Three tiers, in order of how much the name itself tells us:

    1. The numbered "N - Monitor" form. Only display sinks get it.
    2. A name that says HDMI / DisplayPort / Display Audio outright.
    3. A bare GPU sink name ("Digital Output"), which counts *only* behind a
       graphics adapter's audio driver. This tier is the one that needs the
       description: onboard S/PDIF is called "Digital Output" too.
    """
    name = (friendly_name or "").strip()
    description = (device_description or "").strip()

    if _NUMBERED_NAME_RE.match(name):
        return True
    if name and _SELF_EVIDENT_SINK_RE.search(name):
        return True
    if name.casefold() in _BARE_GPU_SINK_NAMES:
        return bool(_DISPLAY_DRIVER_RE.search(description))
    return False


@dataclass(frozen=True)
class AudioEndpoint:
    """One render endpoint, present or long gone."""

    endpoint_id: str
    friendly_name: str
    device_description: str
    state: EndpointState
    raw_state: Optional[int]
    is_display_audio: bool

    #: Values whose read was refused or otherwise failed, by name. Empty on
    #: a clean read. A name that could not be read shows as "" in
    #: `friendly_name`, and this is what keeps that from looking like a
    #: device that genuinely has no name.
    unreadable: Tuple[str, ...] = field(default=())

    @property
    def display_index(self) -> Optional[int]:
        return parse_monitor_label(self.friendly_name)[0]

    @property
    def monitor_name(self) -> Optional[str]:
        """The panel this endpoint belongs to, or None if the name says nothing."""
        return parse_monitor_label(self.friendly_name)[1]

    @property
    def is_active(self) -> bool:
        return self.state is EndpointState.ACTIVE

    @property
    def label(self) -> str:
        """What a UI row should say."""
        if self.friendly_name and self.device_description:
            return f"{self.device_description} ({self.friendly_name})"
        return self.friendly_name or self.device_description or self.endpoint_id


def build_endpoints(rows: Sequence[dict]) -> List[AudioEndpoint]:
    """Plain dicts from the registry layer -> endpoints.

    Kept apart from the registry call so every rule above is testable with no
    audio hardware and no registry.
    """
    endpoints = []
    for row in rows:
        friendly = row.get("friendly_name")
        description = row.get("device_description")
        raw_state = row.get("raw_state")
        endpoints.append(AudioEndpoint(
            endpoint_id=row.get("endpoint_id", ""),
            friendly_name=friendly or "",
            device_description=description or "",
            state=decode_state(raw_state),
            raw_state=raw_state,
            is_display_audio=looks_like_display_audio(friendly, description),
            unreadable=tuple(row.get("unreadable", ())),
        ))
    return endpoints


# ── reading the registry ───────────────────────────────────────────────

def _read_string(key, value_name: str) -> Tuple[Optional[str], Optional[str]]:
    """`(value, failure_reason)`. Both None-able, and never conflated.

    A value that is simply absent is `(None, None)` — a definite answer.
    A read that was refused is `(None, "<reason>")`.
    """
    try:
        data, _kind = winreg.QueryValueEx(key, value_name)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"{value_name}: {exc.strerror or exc}"
    if data is None:
        return None, None
    return str(data), None


def read_endpoint_rows(subkey: str = MMDEVICES_RENDER_KEY) -> List[dict]:
    """Raw rows for every render endpoint the registry remembers.

    Raises `AudioEndpointError` if the key itself cannot be opened. An empty
    list is a real claim about the machine and is never how a refusal is
    reported.
    """
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey, 0,
                              winreg.KEY_READ)
    except FileNotFoundError as exc:
        raise AudioEndpointError(
            rf"registry key not found: HKLM\{subkey}") from exc
    except PermissionError as exc:
        raise AudioEndpointError(
            rf"access denied reading HKLM\{subkey}") from exc
    except OSError as exc:
        raise AudioEndpointError(
            rf"could not open HKLM\{subkey}: {exc}") from exc

    rows: List[dict] = []
    with root:
        index = 0
        while True:
            try:
                guid = winreg.EnumKey(root, index)
            except OSError:
                # ERROR_NO_MORE_ITEMS: winreg has no count call worth using,
                # so the exception IS the loop's terminator. Logged rather
                # than left bare so it does not read as a swallowed error.
                logger.debug("End of endpoint enumeration at index %d", index)
                break
            index += 1
            rows.append(_read_one_endpoint(root, guid, subkey))
    return rows


def _read_one_endpoint(root, guid: str, subkey: str) -> dict:
    unreadable: List[str] = []
    raw_state: Optional[int] = None
    friendly: Optional[str] = None
    description: Optional[str] = None

    try:
        endpoint_key = winreg.OpenKey(root, guid, 0, winreg.KEY_READ)
    except OSError as exc:
        logger.warning(r"could not open HKLM\%s\%s: %s", subkey, guid, exc)
        return {"endpoint_id": guid, "friendly_name": None,
                "device_description": None, "raw_state": None,
                "unreadable": ("DeviceState", PKEY_DEVICE_FRIENDLY_NAME,
                               PKEY_DEVICE_DESCRIPTION)}

    with endpoint_key:
        try:
            value, _kind = winreg.QueryValueEx(endpoint_key, "DeviceState")
            raw_state = int(value)
        except FileNotFoundError:
            unreadable.append("DeviceState")
            logger.warning(r"%s\%s has no DeviceState value", subkey, guid)
        except OSError as exc:
            unreadable.append("DeviceState")
            logger.warning(r"could not read DeviceState of %s\%s: %s",
                           subkey, guid, exc)

        try:
            properties = winreg.OpenKey(endpoint_key, "Properties", 0,
                                        winreg.KEY_READ)
        except FileNotFoundError:
            properties = None
        except OSError as exc:
            properties = None
            unreadable.extend((PKEY_DEVICE_FRIENDLY_NAME,
                               PKEY_DEVICE_DESCRIPTION))
            logger.warning(r"could not open %s\%s\Properties: %s",
                           subkey, guid, exc)

        if properties is not None:
            with properties:
                friendly, why = _read_string(properties,
                                             PKEY_DEVICE_FRIENDLY_NAME)
                if why:
                    unreadable.append(PKEY_DEVICE_FRIENDLY_NAME)
                    logger.warning("endpoint %s: %s", guid, why)
                description, why = _read_string(properties,
                                                PKEY_DEVICE_DESCRIPTION)
                if why:
                    unreadable.append(PKEY_DEVICE_DESCRIPTION)
                    logger.warning("endpoint %s: %s", guid, why)

    return {"endpoint_id": guid, "friendly_name": friendly,
            "device_description": description, "raw_state": raw_state,
            "unreadable": tuple(unreadable)}


def list_render_endpoints(subkey: str = MMDEVICES_RENDER_KEY
                          ) -> List[AudioEndpoint]:
    """Every render endpoint this machine remembers, ghosts included.

    Read-only, unelevated. Raises `AudioEndpointError` if the list itself
    could not be read.
    """
    return build_endpoints(read_endpoint_rows(subkey))


def display_audio_endpoints(endpoints: Sequence[AudioEndpoint]
                            ) -> List[AudioEndpoint]:
    """The subset that belongs to a monitor, live ones first."""
    found = [e for e in endpoints if e.is_display_audio]
    found.sort(key=lambda e: (not e.is_active,
                              e.display_index if e.display_index is not None
                              else 1_000_000,
                              e.friendly_name))
    return found


# ── mapping a monitor to an endpoint ───────────────────────────────────

def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def matches_for_monitor(endpoints: Sequence[AudioEndpoint],
                        monitor_name: str) -> List[AudioEndpoint]:
    """Every display endpoint whose name answers to `monitor_name`.

    Matches either the parsed monitor name ("MO27Q28G") or the whole endpoint
    name ("2 - MO27Q28G"), since a UI may hand back what it displayed. An
    empty or whitespace-only name matches nothing — otherwise a blank field
    quietly selects whichever endpoint happens to be first.
    """
    target = _normalise(monitor_name or "")
    if not target:
        return []

    found = []
    for endpoint in endpoints:
        if not endpoint.is_display_audio:
            continue
        parsed = endpoint.monitor_name
        if parsed and _normalise(parsed) == target:
            found.append(endpoint)
        elif endpoint.friendly_name and \
                _normalise(endpoint.friendly_name) == target:
            found.append(endpoint)
    return found


def _contenders(endpoints: Sequence[AudioEndpoint],
                monitor_name: str) -> List[AudioEndpoint]:
    """Matches narrowed to the ones that could plausibly be meant.

    Three tiers, because a stale ghost is not a real rival to a live device:
    an ACTIVE endpoint beats everything; failing that, a DISABLED or
    UNPLUGGED one is still real hardware and beats a NOTPRESENT ghost;
    failing that, the ghosts are all there is.

    This is what makes "2 - MO27Q28G" (active) answerable despite
    "4 - MO27Q28G" (a ghost of the same panel) also matching, while leaving
    two genuinely live identical monitors ambiguous.
    """
    matches = matches_for_monitor(endpoints, monitor_name)
    if not matches:
        return []

    active = [e for e in matches if e.state is EndpointState.ACTIVE]
    if active:
        return active
    real = [e for e in matches if e.state not in (EndpointState.NOTPRESENT,
                                                  EndpointState.UNKNOWN)]
    return real or matches


def endpoint_for_monitor(endpoints: Sequence[AudioEndpoint],
                         monitor_name: str) -> Optional[AudioEndpoint]:
    """The one endpoint belonging to `monitor_name`, or None.

    None means either "nothing matched" or "several did" — ask
    `ambiguous_matches` which. Mapping is by NAME and is fallible: two
    identical monitors produce two identically-named live endpoints and
    nothing in the registry separates them, so this returns None rather than
    picking one.
    """
    contenders = _contenders(endpoints, monitor_name)
    if len(contenders) == 1:
        return contenders[0]
    if len(contenders) > 1:
        logger.warning("monitor %r matches %d live audio endpoints (%s); "
                       "refusing to pick one", monitor_name, len(contenders),
                       ", ".join(e.endpoint_id for e in contenders))
    return None


def ambiguous_matches(endpoints: Sequence[AudioEndpoint],
                      monitor_name: str) -> List[AudioEndpoint]:
    """The rival endpoints when `monitor_name` is genuinely ambiguous.

    Empty when the name maps cleanly or matches nothing — a non-empty result
    is always a real problem the caller has to put to the user.
    """
    contenders = _contenders(endpoints, monitor_name)
    return contenders if len(contenders) > 1 else []


# ── the COM edge ───────────────────────────────────────────────────────
#
# Raw ctypes vtable calls. `comtypes` is not a dependency of this project and
# is not becoming one for two COM interfaces, one of which is undocumented
# and has no type library to import anyway.

_ole32 = ctypes.windll.ole32

_S_OK = 0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = -2147417850          # 0x80010106
_E_NOTFOUND = -2147023728                  # 0x80070490
_REGDB_E_CLASSNOTREG = -2147221164         # 0x80040154
_E_NOINTERFACE = -2147467262               # 0x80004002

_COINIT_APARTMENTTHREADED = 0x2
_CLSCTX_ALL = 0x17

CLSID_MMDEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDEVICE_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"

#: PolicyConfigClient, undocumented.
CLSID_POLICY_CONFIG_CLIENT = "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"
IID_IPOLICY_CONFIG = "{F8679F50-850A-41CF-9C72-430F290290C8}"
IID_IPOLICY_CONFIG_VISTA = "{568B9108-44BF-40B4-9006-86AFE5B5A620}"

#: EDataFlow / ERole, from mmdeviceapi.h.
E_RENDER = 0
E_CONSOLE = 0

#: IMMDeviceEnumerator vtable: 3 IUnknown, EnumAudioEndpoints (3),
#: GetDefaultAudioEndpoint (4), GetDevice (5).
_IMMDEVICE_ENUMERATOR_GET_DEFAULT = 4

#: IMMDevice vtable: 3 IUnknown, Activate (3), OpenPropertyStore (4),
#: GetId (5), GetState (6).
_IMMDEVICE_GET_ID = 5

#: IPolicyConfig: 3 IUnknown + 12 interface methods, SetEndpointVisibility
#: last. See the module docstring — 12 is SetPropertyValue, not this.
IPOLICYCONFIG_SET_ENDPOINT_VISIBILITY_VTBL_INDEX = 14

#: IPolicyConfigVista omits ResetDeviceFormat, so everything after it shifts
#: down by one.
IPOLICYCONFIG_VISTA_SET_ENDPOINT_VISIBILITY_VTBL_INDEX = 13


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    out = _GUID()
    hr = _ole32.CLSIDFromString(wintypes.LPCWSTR(text), ctypes.byref(out))
    if hr != _S_OK:
        raise AudioEndpointError(f"CLSIDFromString({text}) failed: {_hr(hr)}")
    return out


def _hr(code: int) -> str:
    """An HRESULT as something worth putting in a message."""
    named = {
        _S_OK: "S_OK",
        _E_NOTFOUND: "E_NOTFOUND (no such device)",
        _REGDB_E_CLASSNOTREG: "REGDB_E_CLASSNOTREG (class not registered)",
        _E_NOINTERFACE: "E_NOINTERFACE",
        -2147024891: "E_ACCESSDENIED",
        -2147024809: "E_INVALIDARG",
    }.get(code)
    return f"{named} (0x{code & 0xFFFFFFFF:08X})" if named \
        else f"HRESULT 0x{code & 0xFFFFFFFF:08X}"


def _com_call(pointer, index: int, argtypes, *args) -> int:
    """Call vtable slot `index` on a raw interface pointer."""
    vtable = ctypes.cast(
        pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return proto(vtable[index])(pointer, *args)


def _release(pointer) -> None:
    if pointer:
        _com_call(pointer, 2, ())


class _Apartment:
    """CoInitializeEx/CoUninitialize, balanced.

    Only uninitialises when this object is what initialised the apartment.
    `RPC_E_CHANGED_MODE` means somebody already set up a different threading
    model — the calls below work fine either way, so it is not an error, but
    it must not be paired with a CoUninitialize.
    """

    def __enter__(self):
        _ole32.CoInitializeEx.restype = ctypes.c_long
        hr = _ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        self._owns = hr in (_S_OK, _S_FALSE)
        if hr < 0 and hr != _RPC_E_CHANGED_MODE:
            raise AudioEndpointError(f"CoInitializeEx failed: {_hr(hr)}")
        return self

    def __exit__(self, *_exc):
        if self._owns:
            _ole32.CoUninitialize()
        return False


def _create_instance(clsid: str, iid: str) -> Tuple[Optional[object], int]:
    """`(pointer, hresult)`. A null pointer always comes with a reason."""
    pointer = ctypes.c_void_p()
    _ole32.CoCreateInstance.restype = ctypes.c_long
    hr = _ole32.CoCreateInstance(ctypes.byref(_guid(clsid)), None, _CLSCTX_ALL,
                                 ctypes.byref(_guid(iid)),
                                 ctypes.byref(pointer))
    if hr != _S_OK or not pointer:
        return None, hr
    return pointer, hr


@dataclass(frozen=True)
class DefaultEndpointResult:
    """The default output device, or an honest account of why we don't know.

    `determined` False and `endpoint_id` None is "we could not look".
    `determined` True and `endpoint_id` None is "this machine has no default
    render device", which is a real state (every endpoint disabled). The two
    are never collapsed, which is why `default_render_endpoint`'s bare
    `Optional[str]` is not the whole interface.
    """

    endpoint_id: Optional[str]
    determined: bool
    reason: str


def default_render_endpoint_detail() -> DefaultEndpointResult:
    """`IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender, eConsole)`.

    Read-only; needs no elevation. The id it returns is the full MMDevice
    form, `{0.0.0.00000000}.{<guid>}` — the registry subkey is the trailing
    guid alone, so compare with `endpoint_guid`.
    """
    try:
        # Every Release MUST happen inside the apartment. Releasing an
        # interface after CoUninitialize is a use-after-free, and it
        # segfaults the process rather than returning an error — see
        # `_Apartment`.
        with _Apartment():
            enumerator = None
            device = ctypes.c_void_p()
            try:
                enumerator, hr = _create_instance(CLSID_MMDEVICE_ENUMERATOR,
                                                  IID_IMMDEVICE_ENUMERATOR)
                if enumerator is None:
                    return DefaultEndpointResult(
                        None, False,
                        f"could not create MMDeviceEnumerator: {_hr(hr)}")

                hr = _com_call(enumerator, _IMMDEVICE_ENUMERATOR_GET_DEFAULT,
                               (wintypes.DWORD, wintypes.DWORD,
                                ctypes.POINTER(ctypes.c_void_p)),
                               E_RENDER, E_CONSOLE, ctypes.byref(device))
                if hr == _E_NOTFOUND:
                    return DefaultEndpointResult(
                        None, True,
                        "GetDefaultAudioEndpoint returned E_NOTFOUND: this "
                        "machine has no default render endpoint")
                if hr != _S_OK or not device:
                    return DefaultEndpointResult(
                        None, False,
                        f"GetDefaultAudioEndpoint failed: {_hr(hr)}")

                raw_id = wintypes.LPWSTR()
                hr = _com_call(device, _IMMDEVICE_GET_ID,
                               (ctypes.POINTER(wintypes.LPWSTR),),
                               ctypes.byref(raw_id))
                if hr != _S_OK or not raw_id.value:
                    return DefaultEndpointResult(
                        None, False, f"IMMDevice::GetId failed: {_hr(hr)}")

                endpoint_id = raw_id.value
                _ole32.CoTaskMemFree(raw_id)
                return DefaultEndpointResult(
                    endpoint_id, True, "read via IMMDeviceEnumerator")
            finally:
                _release(device)
                _release(enumerator)
    except AudioEndpointError as exc:
        return DefaultEndpointResult(None, False, str(exc))
    except OSError as exc:
        return DefaultEndpointResult(
            None, False, f"COM call raised: {exc}")


def default_render_endpoint() -> Optional[str]:
    """The default output device id, or None.

    None is deliberately ambiguous between "no default" and "could not
    determine", because that is all a bare `Optional[str]` can say — the
    reason is logged, and `default_render_endpoint_detail` returns it. Prefer
    the detailed form anywhere the difference matters, which is anywhere the
    answer is shown to a user.
    """
    result = default_render_endpoint_detail()
    if result.endpoint_id is None:
        logger.warning("default render endpoint not available: %s",
                       result.reason)
    return result.endpoint_id


def endpoint_guid(endpoint_id: str) -> str:
    """The registry subkey for an endpoint id, in either form.

    `{0.0.0.00000000}.{5f9f...}` and a bare `{5f9f...}` both give `{5f9f...}`.
    """
    text = (endpoint_id or "").strip()
    if text.count("}.{") == 1:
        return "{" + text.split("}.{", 1)[1]
    return text


# ── writes. NEVER RUN AGAINST A REAL ENDPOINT WITHOUT SUPERVISION ──────

def _require_supervision(confirm_supervised: bool, what: str) -> None:
    if not confirm_supervised:
        raise SupervisionRequired(
            f"{what} refused: hiding an audio endpoint is a machine-wide "
            "change and hiding the wrong one takes the sound off the "
            "machine. Pass confirm_supervised=True only when the caller "
            "has actually asked a person first.")


def set_endpoint_enabled(endpoint_id: str, enabled: bool, *,
                         confirm_supervised: bool = False) -> bool:
    """Show or hide an endpoint via `IPolicyConfig::SetEndpointVisibility`.

    **UNVERIFIED.** See the module docstring: the interface is undocumented,
    the vtable index is taken from two agreeing third-party headers rather
    than from Microsoft, and nothing here has been run against real hardware.

    Returns True only when the call returned S_OK. Every other outcome raises
    `AudioPolicyError` carrying the HRESULT, or `SupervisionRequired` — this
    never returns False and never quietly does nothing, because a write that
    reports "failed" with no reason is indistinguishable from one that was
    never attempted.

    Takes effect immediately when it works, unlike the registry fallback.
    Needs the endpoint id in full MMDevice form,
    `{0.0.0.00000000}.{<guid>}` — the bare guid is not what IPolicyConfig
    expects.
    """
    _require_supervision(confirm_supervised,
                         f"set_endpoint_enabled({endpoint_id!r}, {enabled})")
    if not endpoint_id:
        raise AudioPolicyError("no endpoint id given")

    try:
        # As in `default_render_endpoint_detail`: Release happens inside the
        # apartment, never after CoUninitialize.
        with _Apartment():
            policy = None
            try:
                # The Win7+ interface first; the Vista one shifts every slot
                # after ResetDeviceFormat down by one, so the index travels
                # with the IID and is never assumed.
                policy, hr = _create_instance(CLSID_POLICY_CONFIG_CLIENT,
                                              IID_IPOLICY_CONFIG)
                index = IPOLICYCONFIG_SET_ENDPOINT_VISIBILITY_VTBL_INDEX
                which = "IPolicyConfig"
                if policy is None:
                    first_hr = hr
                    policy, hr = _create_instance(
                        CLSID_POLICY_CONFIG_CLIENT, IID_IPOLICY_CONFIG_VISTA)
                    index = \
                        IPOLICYCONFIG_VISTA_SET_ENDPOINT_VISIBILITY_VTBL_INDEX
                    which = "IPolicyConfigVista"
                    if policy is None:
                        raise AudioPolicyError(
                            f"PolicyConfigClient unavailable: IPolicyConfig "
                            f"{_hr(first_hr)}, IPolicyConfigVista {_hr(hr)}")

                hr = _com_call(policy, index,
                               (wintypes.LPCWSTR, ctypes.c_int),
                               endpoint_id, 1 if enabled else 0)
                if hr != _S_OK:
                    raise AudioPolicyError(
                        f"{which}::SetEndpointVisibility({endpoint_id}, "
                        f"{int(enabled)}) failed: {_hr(hr)}")
                logger.info("%s::SetEndpointVisibility(%s, %d) returned S_OK",
                            which, endpoint_id, int(enabled))
                return True
            finally:
                _release(policy)
    except OSError as exc:
        raise AudioPolicyError(
            f"SetEndpointVisibility({endpoint_id}) raised: {exc}") from exc


@dataclass(frozen=True)
class RegistryStateWrite:
    """What the registry fallback did, and when it will be visible."""

    endpoint_id: str
    written: bool
    previous_raw_state: Optional[int]
    new_raw_state: Optional[int]

    #: The flag that matters. The audio service reads DeviceState at start,
    #: so a value written underneath it changes nothing until the service
    #: restarts — in practice, the next logon. Reporting this write as "done"
    #: is how someone concludes the feature is broken.
    applies_at_next_logon: bool
    reason: str


def set_endpoint_enabled_via_registry(endpoint_id: str, enabled: bool, *,
                                      confirm_supervised: bool = False,
                                      subkey: str = MMDEVICES_RENDER_KEY
                                      ) -> RegistryStateWrite:
    """Fallback: write `DeviceState` directly. Needs elevation.

    Documented fallback for the case where `PolicyConfigClient` is not
    registered or `SetEndpointVisibility` refuses. It is strictly worse than
    the COM path in one way that must be surfaced, not buried: **it does not
    take effect until the audio service restarts**, so
    `applies_at_next_logon` is True on every successful write.

    The undocumented high bits of the existing value are PRESERVED — only the
    documented nibble is replaced. Clobbering `0x10000000` would be a change
    nobody asked for, on a bit whose meaning is unknown.

    Raises `SupervisionRequired` unless explicitly confirmed. Never raises on
    a failed write: it returns `written=False` with the reason, because a
    caller reaching this path has already had one write fail on it.
    """
    _require_supervision(
        confirm_supervised,
        f"set_endpoint_enabled_via_registry({endpoint_id!r}, {enabled})")

    guid = endpoint_guid(endpoint_id)
    if not guid:
        return RegistryStateWrite(endpoint_id, False, None, None, False,
                                  "no endpoint id given")

    path = rf"{subkey}\{guid}"
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                             winreg.KEY_READ | winreg.KEY_SET_VALUE)
    except FileNotFoundError:
        return RegistryStateWrite(endpoint_id, False, None, None, False,
                                  rf"no such endpoint: HKLM\{path}")
    except PermissionError:
        return RegistryStateWrite(
            endpoint_id, False, None, None, False,
            rf"access denied opening HKLM\{path} for write — this needs "
            "elevation")
    except OSError as exc:
        return RegistryStateWrite(endpoint_id, False, None, None, False,
                                  rf"could not open HKLM\{path}: {exc}")

    with key:
        previous: Optional[int] = None
        try:
            value, _kind = winreg.QueryValueEx(key, "DeviceState")
            previous = int(value)
        except FileNotFoundError:
            previous = None
        except OSError as exc:
            return RegistryStateWrite(
                endpoint_id, False, None, None, False,
                f"could not read the current DeviceState, so it cannot be "
                f"changed without destroying it: {exc}")

        documented = DEVICE_STATE_ACTIVE if enabled else DEVICE_STATE_DISABLED
        preserved = undocumented_state_bits(previous)
        new_state = preserved | documented

        try:
            winreg.SetValueEx(key, "DeviceState", 0, winreg.REG_DWORD,
                              new_state)
        except OSError as exc:
            return RegistryStateWrite(
                endpoint_id, False, previous, None, False,
                rf"could not write DeviceState to HKLM\{path}: {exc}")

    logger.info("DeviceState of %s: 0x%08X -> 0x%08X (applies at next logon)",
                guid, previous or 0, new_state)
    return RegistryStateWrite(
        endpoint_id, True, previous, new_state, True,
        "DeviceState written; the audio service reads it at start, so this "
        "does not take effect until it restarts (next logon)")
