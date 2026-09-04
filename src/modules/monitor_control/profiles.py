r"""Named display profiles, keyed on EDID.

A profile is "this is what my desk looks like": every monitor, its resolution,
refresh, position, orientation, and which one is primary. Saving one is easy.
Applying one correctly rests entirely on **which monitor is which**, and that
is the only genuinely hard decision in this file.

**Three identifiers Windows offers are all wrong for this, and each one looks
right until it isn't:**

* `\\.\DISPLAY1` / `\\.\DISPLAY2` are *positions in a list*. On this machine
  the Gigabyte is DISPLAY1 and the Dell is DISPLAY2; swap the two DisplayPort
  cables and the names swap while the monitors have not moved. A profile that
  restored 2560x1440@144 "to DISPLAY2" would then configure the other panel.
* The CCD **target id** (256, 264, 265 here) is an adapter output number. It
  identifies the port, not the thing plugged into it.
* The **UID in the device path** (`...&UID256#...`) is the same target id in
  another costume, so the device path is not stable either.

So identity is the **EDID**: manufacturer (a PNP three-letter code), product
code, and serial number. That triple survives a reboot, a re-plug into
another port, a driver reinstall, and a GPU swap. It is read from
`HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\<hwid>\<instance>\Device
Parameters\EDID`, which is readable **unelevated** — measured on this machine.

Two byte orders in the EDID are not guessable and are the classic way to get
this wrong: the manufacturer id at bytes 8-9 is **big**-endian, and the
product code at bytes 10-11, two bytes later in the same header, is
**little**-endian. Get them right and `manufacturer + product_code`
reproduces the hardware id in the device path exactly (`GBT273C`, `DELD0E6`),
which is a free correctness check and is what the tests assert.

Serial numbers come from two places and both are needed. The 0xFF descriptor
block holds an ASCII serial when the manufacturer bothered; the Gigabyte here
has one (`25362F004687`) while its 4-byte numeric serial is the `0x01010101`
filler. The LG is the other way round: no 0xFF block at all, so its numeric
serial (292849) is the only thing distinguishing it from a second identical
LG. A parser that reads only one of the two leaves monitors unidentified.

**Refusals are never rounded off to answers.** A monitor whose EDID could not
be read gets `identified = False` and a `reason`; it is not quietly keyed on
its device path, because a key that looks stable and is not is exactly the
failure this module was designed to prevent. `can_apply` then refuses, naming
the monitor.

`apply_profile` is the only thing here that could change the machine, and it
will not do so without `confirm=True`; without it you get the plan.

No Qt, no `App` import — the app data directory is computed the same way
`app.py:_get_app_data_dir` computes it, exactly as `gpresult/rsop_snapshot.py`
does, so this module is testable headless.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import re
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from modules.monitor_control import display_config as dc

logger = logging.getLogger(__name__)

#: Bumped when the on-disk shape changes in a way a reader must know about.
#: A reader never *requires* a match — see `DisplayProfile.from_dict`.
SCHEMA_VERSION = 1

#: Subdirectory of %APPDATA%/WindowsTweaker that holds profiles.
PROFILE_DIRNAME = "monitor_profiles"

_SUFFIX = ".profile.json"

#: DISPLAYCONFIG_ROTATION.
ROTATION_UNSPECIFIED = 0
ROTATION_IDENTITY = 1
ROTATION_ROTATE90 = 2
ROTATION_ROTATE180 = 3
ROTATION_ROTATE270 = 4

_ROTATION_NAMES = {
    ROTATION_UNSPECIFIED: "unspecified",
    ROTATION_IDENTITY: "landscape",
    ROTATION_ROTATE90: "portrait",
    ROTATION_ROTATE180: "landscape (flipped)",
    ROTATION_ROTATE270: "portrait (flipped)",
}


def rotation_name(value: int) -> str:
    return _ROTATION_NAMES.get(value, f"unknown rotation {value}")


#: Where a serial number came from. An empty string means there is none, and
#: `MonitorIdentity.reason` then says why.
SERIAL_FROM_DESCRIPTOR = "edid-descriptor"
SERIAL_FROM_NUMERIC = "edid-numeric"


class EdidError(ValueError):
    """The blob handed over is not an EDID base block."""


class ProfileRefused(RuntimeError):
    """`apply_profile` would not proceed, and this is why."""


# ══ EDID ═══════════════════════════════════════════════════════════════

_EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"

#: Numeric serials that are not serials. `0x01010101` is what the Gigabyte on
#: this machine reports — four identical bytes is a factory filler, and so are
#: all-zero and all-ones.
_FILLER_SERIALS = {0x00000000, 0x01010101, 0xFFFFFFFF}

#: Descriptor block tags.
_DESC_SERIAL = 0xFF
_DESC_NAME = 0xFC

_DESCRIPTOR_OFFSETS = (54, 72, 90, 108)


@dataclass(frozen=True)
class EdidInfo:
    """The identifying fields of an EDID base block."""

    manufacturer: str
    product_code: str
    serial: Optional[str]
    serial_source: str
    model_name: str = ""


def _pnp_id(value: int) -> str:
    """The three-letter PNP code packed five bits per letter, big-endian."""
    letters = [chr(((value >> shift) & 0x1F) + 0x40) for shift in (10, 5, 0)]
    return "".join(letters)


def _descriptor_text(blob: bytes, offset: int) -> Optional[Tuple[int, str]]:
    r"""The 13-byte text of a descriptor block, terminated and cleaned.

    EDID 1.4 says terminate with 0x0A and pad with 0x20, and the Dell here
    does exactly that (`b"8DYM7P2\n     "`). **The Gigabyte does not**: it
    fills all 13 bytes and terminates with NUL (`b"25362F004687\x00"`).

    So both terminators are honoured, and the result is stripped of control
    characters rather than only whitespace -- `str.strip()` leaves `\x00`
    alone, because NUL is not whitespace, and the serial went on to become
    part of the EDID key. That key is written into profile JSON and compared
    against live monitors, so a stray NUL is the "identifier that looks
    stable and is not" this module was written to avoid.
    """
    block = blob[offset:offset + 18]
    if len(block) < 18 or block[0:3] != b"\x00\x00\x00":
        return None
    body = block[5:18].split(b"\n")[0].split(b"\x00")[0]
    text = body.decode("ascii", "ignore").strip()
    # Anything else below 0x20 would survive `.strip()` in the middle of a
    # string too; a descriptor has no business carrying one.
    text = "".join(ch for ch in text if ch >= " ")
    return block[3], text


def parse_edid(blob: bytes) -> EdidInfo:
    r"""The identifying fields of an EDID, or `EdidError`.

    Raising rather than returning a half-filled record is deliberate: a
    corrupt blob that parsed "successfully" would produce a plausible key
    (`\x00\x00\x00`-derived letters are `@@@`) that quietly matches nothing,
    or worse, matches another unreadable monitor.
    """
    if len(blob) < 128:
        raise EdidError(f"EDID is {len(blob)} bytes, need at least 128")
    if blob[0:8] != _EDID_HEADER:
        raise EdidError("EDID header missing (00 FF FF FF FF FF FF 00)")

    manufacturer = _pnp_id(int.from_bytes(blob[8:10], "big"))
    product_code = "%04X" % int.from_bytes(blob[10:12], "little")

    serial: Optional[str] = None
    serial_source = ""
    model_name = ""
    for offset in _DESCRIPTOR_OFFSETS:
        found = _descriptor_text(blob, offset)
        if found is None:
            continue
        tag, text = found
        if tag == _DESC_SERIAL and text and serial is None:
            serial, serial_source = text, SERIAL_FROM_DESCRIPTOR
        elif tag == _DESC_NAME and text and not model_name:
            model_name = text

    if serial is None:
        numeric = int.from_bytes(blob[12:16], "little")
        if numeric not in _FILLER_SERIALS:
            serial, serial_source = str(numeric), SERIAL_FROM_NUMERIC

    return EdidInfo(manufacturer=manufacturer, product_code=product_code,
                    serial=serial, serial_source=serial_source,
                    model_name=model_name)


# ══ identity ═══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MonitorIdentity:
    """Who a monitor *is*, as opposed to where it happens to be plugged in."""

    manufacturer: str
    product_code: str
    serial: Optional[str]
    serial_source: str = ""
    friendly_name: str = ""
    #: Recorded for diagnostics and for reaching the device again this
    #: session. NEVER part of the key: it carries the connector's UID.
    device_path: str = ""
    #: Empty when this identity is complete; otherwise why it is not.
    reason: str = ""

    @property
    def key(self) -> str:
        """The stable identifier. EDID only — no display number, no target id,
        no device path."""
        parts = [self.manufacturer, self.product_code]
        if self.serial:
            parts.append(self.serial)
        return "-".join(p for p in parts if p)

    @property
    def identified(self) -> bool:
        """True when the key distinguishes this monitor from a twin.

        Without a serial, two identical panels produce one key — which is a
        real situation, not a parse failure, and `can_apply` refuses on it
        rather than guessing.
        """
        return bool(self.manufacturer and self.product_code and self.serial)

    @property
    def label(self) -> str:
        """What to put in a sentence a person reads."""
        name = self.friendly_name or f"{self.manufacturer} {self.product_code}"
        return f"{name} [{self.key}]"

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "manufacturer": self.manufacturer,
                "product_code": self.product_code, "serial": self.serial,
                "serial_source": self.serial_source,
                "friendly_name": self.friendly_name,
                "device_path": self.device_path, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MonitorIdentity":
        # `key` is derived, and is written out for readers that are not this
        # code (and for grepping a profile). It is deliberately not read back.
        return cls(manufacturer=data.get("manufacturer", ""),
                   product_code=data.get("product_code", ""),
                   serial=data.get("serial"),
                   serial_source=data.get("serial_source", ""),
                   friendly_name=data.get("friendly_name", ""),
                   device_path=data.get("device_path", ""),
                   reason=data.get("reason", ""))


# ══ the profile ════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ProfileMonitor:
    """One monitor's state in a profile."""

    identity: MonitorIdentity
    active: bool
    resolution: Optional[Tuple[int, int]]
    refresh_hz: float
    position: Optional[Tuple[int, int]]
    orientation: int = ROTATION_UNSPECIFIED
    primary: bool = False
    #: Recorded for this session only. Never matched on — see the module
    #: docstring on why a target id is a port, not a monitor.
    target_id: int = 0
    adapter: Tuple[int, int] = (0, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity": self.identity.to_dict(), "active": self.active,
            "resolution": list(self.resolution) if self.resolution else None,
            "refresh_hz": self.refresh_hz,
            "position": list(self.position) if self.position else None,
            "orientation": self.orientation, "primary": self.primary,
            "target_id": self.target_id, "adapter": list(self.adapter),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProfileMonitor":
        def pair(value):
            return tuple(value) if value else None

        return cls(
            identity=MonitorIdentity.from_dict(data.get("identity", {})),
            active=bool(data.get("active", False)),
            resolution=pair(data.get("resolution")),
            refresh_hz=float(data.get("refresh_hz", 0.0)),
            position=pair(data.get("position")),
            orientation=int(data.get("orientation", ROTATION_UNSPECIFIED)),
            primary=bool(data.get("primary", False)),
            target_id=int(data.get("target_id", 0)),
            adapter=tuple(data.get("adapter", (0, 0))),
        )


@dataclass(frozen=True)
class DisplayProfile:
    """A named desktop layout."""

    name: str
    created_at: str
    monitors: List[ProfileMonitor] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def keys(self) -> List[str]:
        return [m.identity.key for m in self.monitors]

    def active_monitors(self) -> List[ProfileMonitor]:
        return [m for m in self.monitors if m.active]

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": self.schema_version, "name": self.name,
                "created_at": self.created_at,
                "monitors": [m.to_dict() for m in self.monitors]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DisplayProfile":
        """Tolerant on purpose: unknown fields are ignored and missing ones
        take the dataclass default, so a profile written by another build of
        this app still loads. The failure mode that prevents is a user losing
        their saved layouts on upgrade."""
        return cls(
            name=data.get("name", ""), created_at=data.get("created_at", ""),
            monitors=[ProfileMonitor.from_dict(m)
                      for m in data.get("monitors", [])],
            schema_version=int(data.get("schema_version", 0)),
        )


@dataclass(frozen=True)
class ProfileSummary:
    """Enough to fill a list without deserialising the whole profile."""

    name: str
    created_at: str
    path: str
    monitor_count: int
    active_count: int


# ══ persistence ════════════════════════════════════════════════════════

def default_profile_dir() -> str:
    r"""`%APPDATA%\WindowsTweaker\monitor_profiles`.

    The app's own convention (`app.py:_get_app_data_dir`), computed here
    rather than imported: importing `app` builds the singleton and drags in
    Qt, which would make this module untestable headless. Nothing is created
    here — only `save_profile` has the right to make directories.
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "WindowsTweaker", PROFILE_DIRNAME)


_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(name: str) -> str:
    r"""A profile name is user-typed and becomes a filename.

    Rejected rather than sanitised: silently turning `..\..\evil` into
    `evil` saves a profile under a name the user did not ask for and cannot
    find again.
    """
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("a profile needs a name")
    if _UNSAFE_NAME.search(cleaned) or cleaned in {".", ".."}:
        raise ValueError(
            f"profile name {name!r} contains characters that are not allowed "
            r'in a filename (< > : " / \ | ? *)')
    return cleaned


def profile_path(name: str, directory: Optional[str] = None) -> str:
    return os.path.join(directory or default_profile_dir(),
                        _safe_name(name) + _SUFFIX)


def save_profile(profile: DisplayProfile,
                 directory: Optional[str] = None) -> str:
    """Write the profile as JSON. Returns the path it went to."""
    path = profile_path(profile.name, directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(profile.to_dict(), handle, indent=2)
    logger.info("Saved display profile %r to %s", profile.name, path)
    return path


def load_profile(name: str, directory: Optional[str] = None) -> DisplayProfile:
    path = profile_path(name, directory)
    with open(path, "r", encoding="utf-8") as handle:
        return DisplayProfile.from_dict(json.load(handle))


def list_profiles(directory: Optional[str] = None) -> List[ProfileSummary]:
    """Every saved profile, newest first. A missing directory is empty, not
    an error — nothing has been saved yet is the normal first-run state."""
    directory = directory or default_profile_dir()
    summaries: List[ProfileSummary] = []
    try:
        entries = sorted(os.listdir(directory))
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Cannot list display profiles in %s: %s", directory, exc)
        return []

    for entry in entries:
        if not entry.endswith(_SUFFIX):
            continue
        path = os.path.join(directory, entry)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable display profile %s: %s",
                           path, exc)
            continue
        monitors = data.get("monitors", [])
        summaries.append(ProfileSummary(
            name=data.get("name") or entry[:-len(_SUFFIX)],
            created_at=data.get("created_at", ""), path=path,
            monitor_count=len(monitors),
            active_count=sum(1 for m in monitors if m.get("active"))))

    summaries.sort(key=lambda s: s.created_at, reverse=True)
    return summaries


def delete_profile(name: str, directory: Optional[str] = None) -> bool:
    """True if it was there and is gone. False if it was never there — that
    is not an error, and an exception would make a double-click on Delete a
    crash."""
    path = profile_path(name, directory)
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.error("Cannot delete display profile %s: %s", path, exc)
        raise
    logger.info("Deleted display profile %r", name)
    return True


# ══ capture ════════════════════════════════════════════════════════════

def _path_for(topology: dc.DisplayTopology,
              target_id: int) -> Optional[dc.DisplayPath]:
    """The path carrying this target's real state — an active one if there is
    one. Mirrors `DisplayTopology.monitors`' own rule, which exists because an
    inactive path's fields are stale."""
    best: Optional[dc.DisplayPath] = None
    for path in topology.paths:
        if path.target_id != target_id or not path.available:
            continue
        if best is None or (path.active and not best.active):
            best = path
    return best


def capture_profile(name: str,
                    topology: Optional[dc.DisplayTopology] = None,
                    identify=None) -> DisplayProfile:
    """The machine's current topology, as a named profile.

    Read-only. `identify` is the EDID lookup, injectable for tests.
    """
    topology = topology if topology is not None else dc.query()
    identify = identify or read_identity

    monitors: List[ProfileMonitor] = []
    for monitor in topology.monitors():
        path = _path_for(topology, monitor.target_id)
        monitors.append(ProfileMonitor(
            identity=identify(monitor),
            active=monitor.active,
            resolution=monitor.resolution,
            refresh_hz=monitor.refresh_hz,
            position=monitor.position,
            orientation=path.rotation if path else ROTATION_UNSPECIFIED,
            # The primary monitor is the one whose desktop surface starts at
            # the virtual desktop's origin. That is what "primary" means; it
            # is not a separate flag anywhere in the CCD data.
            primary=monitor.active and monitor.position == (0, 0),
            target_id=monitor.target_id,
            adapter=monitor.adapter,
        ))

    return DisplayProfile(name=name,
                          created_at=datetime.now().isoformat(timespec="seconds"),
                          monitors=monitors)


# ══ can_apply — the refusal ════════════════════════════════════════════

def _present_keys(present: Sequence[MonitorIdentity]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for identity in present:
        counts[identity.key] = counts.get(identity.key, 0) + 1
    return counts


def can_apply(profile: DisplayProfile,
              present: Optional[Sequence[MonitorIdentity]] = None
              ) -> Tuple[bool, str]:
    """`(ok, reason)` — and the reason is filled in either way.

    The rules, in the order they are checked:

    1. An empty profile has nothing to apply.
    2. Every monitor the profile records as **on** must be present. Missing
       ones are named individually: "one monitor is missing" sends someone to
       check three cables.
    3. A monitor the profile records as **off** need not be present — that is
       exactly the situation the profile describes. It is still named in the
       reason rather than passed over silently.
    4. A key that matches more than one present monitor is refused. Two
       identical panels whose EDIDs carry no serial share a key, and choosing
       between them would be a coin flip.

    Never a partial verdict: this returns False, and `apply_profile` raises on
    it, so there is no path that half-configures a desktop.
    """
    if not profile.monitors:
        return False, f"profile {profile.name!r} records no monitors"

    if present is None:
        try:
            present = live_identities()
        except OSError as exc:
            return False, f"could not read the current topology: {exc}"

    counts = _present_keys(present)
    notes: List[str] = []

    ambiguous = sorted(k for k, n in counts.items() if n > 1)
    if ambiguous:
        return False, (
            "ambiguous monitor identity: "
            + ", ".join(f"{k} matches {counts[k]} connected monitors"
                        for k in ambiguous)
            + " — these panels report no EDID serial number, so applying "
              "would be a guess about which is which")

    profile_counts: Dict[str, int] = {}
    for monitor in profile.monitors:
        key = monitor.identity.key
        profile_counts[key] = profile_counts.get(key, 0) + 1
    duplicated = sorted(k for k, n in profile_counts.items() if n > 1)
    if duplicated:
        return False, (
            "ambiguous monitor identity in the profile itself: "
            + ", ".join(f"{k} appears {profile_counts[k]} times"
                        for k in duplicated))

    missing = [m for m in profile.active_monitors()
               if m.identity.key not in counts]
    if missing:
        return False, (
            f"profile {profile.name!r} cannot be applied — "
            + "; ".join(f"{m.identity.label} is not connected"
                        for m in missing))

    absent_off = [m for m in profile.monitors
                  if not m.active and m.identity.key not in counts]
    if absent_off:
        notes.append("recorded switched off and not connected: "
                     + ", ".join(m.identity.label for m in absent_off))

    unidentified = [m for m in profile.active_monitors()
                    if not m.identity.identified]
    if unidentified:
        notes.append("identified by model only (no EDID serial): "
                     + ", ".join(m.identity.label for m in unidentified))

    reason = (f"all {len(profile.active_monitors())} monitor(s) this profile "
              f"turns on are connected")
    if notes:
        reason += " — " + "; ".join(notes)
    return True, reason


# ══ apply — WRITTEN, NOT RUN ═══════════════════════════════════════════

@dataclass(frozen=True)
class ApplyStep:
    """One monitor's worth of change, addressed by identity."""

    monitor_key: str
    label: str
    #: The GDI device name this identity resolves to *right now*. Looked up at
    #: apply time and never stored in the profile, because it renumbers.
    device: Optional[str]
    detach: bool
    resolution: Optional[Tuple[int, int]]
    refresh_hz: float
    position: Optional[Tuple[int, int]]
    orientation: int
    primary: bool
    #: Filled in when this step could not be turned into a real device call.
    reason: str = ""


@dataclass(frozen=True)
class ApplyPlan:
    steps: List[ApplyStep]
    applied: bool
    reason: str

    @property
    def would_change(self) -> bool:
        return bool(self.steps)


#: ChangeDisplaySettingsEx flags.
CDS_UPDATEREGISTRY = 0x00000001
CDS_NORESET = 0x10000000
CDS_SET_PRIMARY = 0x00000010

#: DEVMODE field selectors.
DM_BITSPERPEL = 0x00040000
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFLAGS = 0x00200000
DM_DISPLAYFREQUENCY = 0x00400000
DM_POSITION = 0x00000020
DM_DISPLAYORIENTATION = 0x00000080

DISP_CHANGE_SUCCESSFUL = 0

_DISP_CHANGE = {
    0: "DISP_CHANGE_SUCCESSFUL",
    1: "DISP_CHANGE_RESTART",
    -1: "DISP_CHANGE_FAILED",
    -2: "DISP_CHANGE_BADMODE",
    -3: "DISP_CHANGE_NOTUPDATED",
    -4: "DISP_CHANGE_BADFLAGS",
    -5: "DISP_CHANGE_BADPARAM",
    -6: "DISP_CHANGE_BADDUALVIEW",
}

#: DMDO_* — the DEVMODE orientation values, which are NOT the CCD rotation
#: values: CCD counts 1..4 from "identity", DEVMODE counts 0..3.
_DEVMODE_ORIENTATION = {
    ROTATION_IDENTITY: 0, ROTATION_ROTATE90: 1,
    ROTATION_ROTATE180: 2, ROTATION_ROTATE270: 3,
}


def apply_profile(profile: DisplayProfile,
                  present: Optional[Sequence[MonitorIdentity]] = None,
                  confirm: bool = False,
                  writer=None,
                  devices: Optional[Dict[str, str]] = None) -> ApplyPlan:
    r"""Reconfigure the desktop to match `profile`.

    **Gated twice on purpose.** `can_apply` must pass — this raises
    `ProfileRefused` otherwise, before a single device call, so there is no
    path that leaves half a desktop configured. And `confirm` must be True;
    without it you get the plan and nothing is written. The default of a
    function that rearranges someone's monitors is "tell me what you would
    do".

    The write itself goes through `ChangeDisplaySettingsExW` per GDI device
    with `CDS_UPDATEREGISTRY | CDS_NORESET`, followed by one
    `ChangeDisplaySettingsExW(NULL, NULL, NULL, 0, NULL)` to commit them
    together — the documented multi-monitor sequence. Applying each device
    immediately instead produces a visible cascade of mode changes and can
    transiently overlap two monitors at the same origin.

    Identities are resolved to `\\.\DISPLAYn` here, at apply time, and never
    read from the profile: that name is a list position (see the module
    docstring).

    `devices` overrides that resolution with an EDID-key → device-name map.
    It exists so the plan can be built and asserted against without a
    matching monitor physically attached — the plan is pure decision-making
    and should not need the hardware to be exercised.
    """
    ok, reason = can_apply(profile, present)
    if not ok:
        raise ProfileRefused(reason)

    devices = device_names_by_key() if devices is None else dict(devices)
    steps: List[ApplyStep] = []
    for monitor in profile.monitors:
        key = monitor.identity.key
        device = devices.get(key)
        step_reason = ""
        if device is None and monitor.active:
            step_reason = (f"{monitor.identity.label} is connected but has no "
                           r"\\.\DISPLAYn name — it cannot be configured")
        steps.append(ApplyStep(
            monitor_key=key, label=monitor.identity.label, device=device,
            detach=not monitor.active, resolution=monitor.resolution,
            refresh_hz=monitor.refresh_hz, position=monitor.position,
            orientation=monitor.orientation, primary=monitor.primary,
            reason=step_reason))

    blocked = [s for s in steps if s.reason]
    if blocked:
        raise ProfileRefused("; ".join(s.reason for s in blocked))

    if not confirm:
        return ApplyPlan(steps=steps, applied=False,
                         reason="dry run — pass confirm=True to apply")

    writer = writer or _change_display_settings
    for step in steps:
        _apply_step(step, writer)
    # Commit every staged CDS_NORESET change in one go.
    writer(None, None, 0)
    logger.info("Applied display profile %r (%d monitors)",
                profile.name, len(steps))
    return ApplyPlan(steps=steps, applied=True,
                     reason=f"applied {len(steps)} monitor(s)")


def _apply_step(step: ApplyStep, writer) -> None:
    if step.detach:
        # A NULL DEVMODE with CDS_UPDATEREGISTRY is how a display is removed
        # from the desktop. There is no "disable" flag.
        writer(step.device, None, CDS_UPDATEREGISTRY | CDS_NORESET)
        return

    devmode = _build_devmode(step)
    flags = CDS_UPDATEREGISTRY | CDS_NORESET
    if step.primary:
        flags |= CDS_SET_PRIMARY
    writer(step.device, devmode, flags)


class _DEVMODEW(ctypes.Structure):
    """Only the union arm the display path uses; the printer arm is longer,
    so `dmSize` is what tells Windows which one this is."""

    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD),
    ]


def _build_devmode(step: ApplyStep) -> _DEVMODEW:
    devmode = _DEVMODEW()
    devmode.dmSize = ctypes.sizeof(_DEVMODEW)
    fields = 0
    if step.resolution:
        devmode.dmPelsWidth, devmode.dmPelsHeight = step.resolution
        fields |= DM_PELSWIDTH | DM_PELSHEIGHT
    if step.position is not None:
        devmode.dmPositionX, devmode.dmPositionY = step.position
        fields |= DM_POSITION
    if step.refresh_hz:
        # DEVMODE's frequency is an integer, so 143.998 Hz is requested as
        # 144. That is how Windows itself stores it; the fractional rate comes
        # back from the CCD API, not from here.
        devmode.dmDisplayFrequency = int(round(step.refresh_hz))
        fields |= DM_DISPLAYFREQUENCY
    if step.orientation in _DEVMODE_ORIENTATION:
        devmode.dmDisplayOrientation = _DEVMODE_ORIENTATION[step.orientation]
        fields |= DM_DISPLAYORIENTATION
    devmode.dmFields = fields
    return devmode


def _change_display_settings(device: Optional[str],
                             devmode: Optional[_DEVMODEW],
                             flags: int) -> None:
    result = ctypes.windll.user32.ChangeDisplaySettingsExW(
        ctypes.c_wchar_p(device), ctypes.byref(devmode) if devmode else None,
        None, wintypes.DWORD(flags), None)
    if result != DISP_CHANGE_SUCCESSFUL:
        name = _DISP_CHANGE.get(result, f"result {result}")
        raise OSError(f"ChangeDisplaySettingsExW({device}) failed: {name}")


# ══ the ctypes edge: EDID and device names ═════════════════════════════

class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]


class _DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("size", wintypes.UINT),
                ("adapterId", _LUID), ("id", wintypes.UINT)]


class _TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER), ("flags", wintypes.UINT),
                ("outputTechnology", wintypes.UINT),
                ("edidManufactureId", wintypes.USHORT),
                ("edidProductCodeId", wintypes.USHORT),
                ("connectorInstance", wintypes.UINT),
                ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
                ("monitorDevicePath", wintypes.WCHAR * 128)]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128),
                ("DeviceKey", wintypes.WCHAR * 128)]


DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2

#: EnumDisplayDevices flag: return the device *interface* name in DeviceID,
#: which is the same `\\?\DISPLAY#...` string the CCD API reports. Without it
#: DeviceID is a monitor hardware id with no instance, and the two cannot be
#: matched up.
EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001


def _swap16(value: int) -> int:
    return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)


def target_device_name(target_id: int,
                       adapter: Tuple[int, int]) -> Optional[Dict[str, Any]]:
    """`DisplayConfigGetDeviceInfo(GET_TARGET_NAME)`, or None if it refused.

    Read-only, unelevated. This is where the friendly name and the device path
    come from; the EDID *serial* is not in this struct, which is why the
    registry read below exists.
    """
    info = _TARGET_DEVICE_NAME()
    info.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME
    info.header.size = ctypes.sizeof(_TARGET_DEVICE_NAME)
    info.header.adapterId.LowPart = adapter[0]
    info.header.adapterId.HighPart = adapter[1]
    info.header.id = target_id
    rc = ctypes.windll.user32.DisplayConfigGetDeviceInfo(ctypes.byref(info))
    if rc != 0:
        logger.warning("DisplayConfigGetDeviceInfo(target %s) failed: %s",
                       target_id, dc.win32_error_name(rc))
        return None
    return {
        # The API returns the manufacturer id byte-swapped relative to the
        # EDID's own big-endian field.
        "manufacturer": _pnp_id(_swap16(info.edidManufactureId)),
        "product_code": "%04X" % info.edidProductCodeId,
        "friendly_name": info.monitorFriendlyDeviceName,
        "device_path": info.monitorDevicePath,
    }


_DEVICE_PATH = re.compile(r"^\\\\[?.]\\DISPLAY#([^#]+)#([^#]+)#", re.IGNORECASE)


def read_edid_for_device_path(device_path: str) -> Tuple[Optional[bytes], str]:
    r"""`(blob, reason)` for a `\\?\DISPLAY#GBT273C#7&...#{guid}` path.

    The blob lives at
    `HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\<hwid>\<instance>\Device
    Parameters\EDID`, and is readable by an ordinary user — measured on this
    machine, no UAC prompt. `(None, reason)` when it is not: a refused read is
    reported, never rendered as "this monitor has no serial".
    """
    import winreg

    match = _DEVICE_PATH.match(device_path or "")
    if not match:
        return None, f"device path not in the expected form: {device_path!r}"
    hardware_id, instance = match.group(1), match.group(2)
    key_path = (r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
                f"\\{hardware_id}\\{instance}\\Device Parameters")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            blob, _kind = winreg.QueryValueEx(key, "EDID")
    except FileNotFoundError:
        return None, rf"no EDID recorded at HKLM\{key_path}"
    except PermissionError as exc:
        return None, rf"EDID at HKLM\{key_path} refused: {exc}"
    except OSError as exc:
        return None, rf"EDID at HKLM\{key_path} unreadable: {exc}"
    return bytes(blob), ""


def read_identity(monitor: dc.Monitor) -> MonitorIdentity:
    """The full EDID identity of one `display_config.Monitor`.

    Two reads, and the second is allowed to fail: the CCD device-name call
    gives manufacturer, product and friendly name; the registry EDID adds the
    serial. Losing the serial degrades the key to model-level and is recorded
    in `reason` — it never silently produces a key that looks complete.
    """
    named = target_device_name(monitor.target_id, monitor.adapter)
    if named is None:
        return MonitorIdentity(
            manufacturer="", product_code="", serial=None,
            reason=(f"DisplayConfigGetDeviceInfo refused for target "
                    f"{monitor.target_id}; this monitor cannot be identified"))

    blob, blob_reason = read_edid_for_device_path(named["device_path"])
    serial: Optional[str] = None
    serial_source = ""
    reason = blob_reason
    if blob is not None:
        try:
            edid = parse_edid(blob)
        except EdidError as exc:
            reason = f"EDID present but unreadable: {exc}"
        else:
            serial, serial_source = edid.serial, edid.serial_source
            if serial is None:
                reason = "this monitor's EDID carries no serial number"
            # The registry blob and the CCD call must agree; if they do not,
            # one of them is describing a different monitor and neither can be
            # trusted as an identity.
            if (edid.manufacturer != named["manufacturer"]
                    or edid.product_code != named["product_code"]):
                return MonitorIdentity(
                    manufacturer=named["manufacturer"],
                    product_code=named["product_code"], serial=None,
                    friendly_name=named["friendly_name"],
                    device_path=named["device_path"],
                    reason=(f"EDID says {edid.manufacturer}{edid.product_code} "
                            f"but the display config says "
                            f"{named['manufacturer']}{named['product_code']}"))

    return MonitorIdentity(
        manufacturer=named["manufacturer"], product_code=named["product_code"],
        serial=serial, serial_source=serial_source,
        friendly_name=named["friendly_name"],
        device_path=named["device_path"], reason=reason)


def live_identities(topology: Optional[dc.DisplayTopology] = None
                    ) -> List[MonitorIdentity]:
    """Every connected monitor's identity. Read-only."""
    topology = topology if topology is not None else dc.query()
    return [read_identity(m) for m in topology.monitors()]


def device_names_by_key() -> Dict[str, str]:
    r"""EDID key → `\\.\DISPLAYn`, resolved now.

    The bridge between the two halves of Windows' display APIs:
    `EnumDisplayDevicesW(name, 0, ..., EDD_GET_DEVICE_INTERFACE_NAME)` puts
    the monitor's device interface path in `DeviceID`, and that string is
    byte-for-byte the `monitorDevicePath` the CCD API reports — measured on
    this machine. Matching on it is what lets an EDID-keyed profile reach a
    GDI device without ever *storing* the GDI name.

    Only attached devices have one, so an inactive monitor is absent here.
    """
    user32 = ctypes.windll.user32
    by_path: Dict[str, str] = {}
    index = 0
    while True:
        adapter = _DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(adapter), 0):
            break
        index += 1
        monitor = _DISPLAY_DEVICEW()
        monitor.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
        if user32.EnumDisplayDevicesW(adapter.DeviceName, 0,
                                      ctypes.byref(monitor),
                                      EDD_GET_DEVICE_INTERFACE_NAME):
            by_path[monitor.DeviceID.lower()] = adapter.DeviceName

    result: Dict[str, str] = {}
    for identity in live_identities():
        device = by_path.get((identity.device_path or "").lower())
        if device:
            result[identity.key] = device
    return result
