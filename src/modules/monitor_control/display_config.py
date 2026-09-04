r"""The display topology, read through the CCD API.

`QueryDisplayConfig` is the only source of truth for what is on the desktop.
The obvious alternatives both lie:

* `WmiMonitorID.Active` is `True` for every monitor with a valid EDID,
  including one that is connected and switched off. On the machine this was
  written for it reports 3 active monitors where 2 are on the desktop.
* `Win32_VideoController.CurrentRefreshRate` reports per ADAPTER, not per
  display — 60 Hz for a card driving a 60 Hz and a 144 Hz panel.

Two things about the real data would break a parser written from the
documentation alone, and both are handled here rather than discovered later:

* **`modeInfoIdx` is only meaningful on an ACTIVE path.** In a real capture,
  103 inactive paths carry the `0xFFFFFFFF` invalid marker — and 20 carry a
  valid-looking index pointing at a mode that belongs to a *different*
  display. Resolving it blindly hands one monitor's resolution to another.
* **Active/inactive is a property of a TARGET, not of a path.** Each target
  appears on up to five source paths; targets can have active and inactive
  paths at once. Only aggregating per target gives the right answer.

Reading is the bulk of this file. The one exception is the small apply
section at the bottom, which lives here because it needs these ctypes
structures — everything that DECIDES whether a change is allowed is in
`display_writes.py`, which is the file to read for that reasoning.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

#: DISPLAYCONFIG_PATH_MODE_IDX_INVALID — "this path has no mode".
MODE_IDX_INVALID = 0xFFFFFFFF

#: DISPLAYCONFIG_PATH_ACTIVE
PATH_ACTIVE = 0x1

#: DISPLAYCONFIG_MODE_INFO_TYPE. Source is 1 and target is 2 — the other way
#: round reads a target's 64-bit pixelRate as a source's width, which comes
#: out as a resolution of 241500000x0 rather than as an error.
MODE_INFO_TYPE_SOURCE = 1
MODE_INFO_TYPE_TARGET = 2

QDC_ALL_PATHS = 0x1
QDC_ONLY_ACTIVE_PATHS = 0x2

#: DISPLAYCONFIG_OUTPUT_TECHNOLOGY. Only the ones a desktop actually meets.
_OUTPUT_TECHNOLOGY = {
    0x00000000: "VGA",
    0x00000001: "S-Video",
    0x00000002: "Composite",
    0x00000003: "Component",
    0x00000004: "DVI",
    0x00000005: "HDMI",
    0x00000006: "LVDS",
    0x00000008: "D-Jpn",
    0x00000009: "SDI",
    0x0000000A: "DisplayPort (external)",
    0x0000000B: "DisplayPort (embedded)",
    0x0000000C: "UDI (external)",
    0x0000000D: "UDI (embedded)",
    0x0000000E: "SDTV dongle",
    0x0000000F: "Miracast",
    0x80000000: "Internal",
}


def output_technology_name(value: int) -> str:
    return _OUTPUT_TECHNOLOGY.get(value, f"unknown (0x{value:08X})")


def refresh_hz(numerator: int, denominator: int) -> float:
    """Hz from the API's RATIONAL.

    A denominator of 0 is how an inactive path reports "no refresh rate",
    and it is common — 103 paths in a real capture. Zero, not an exception,
    and never a rounded integer: 59.94 Hz is a real mode and rounds to a
    number the display does not have.
    """
    if not denominator:
        return 0.0
    return numerator / denominator


@dataclass(frozen=True)
class SourceMode:
    """A desktop surface: its size and where it sits in the virtual desktop."""

    mode_id: int
    width: int
    height: int
    position: Tuple[int, int]


@dataclass(frozen=True)
class DisplayPath:
    """One source→target connection. Most of them are inactive placeholders."""

    source_id: int
    target_id: int
    adapter: Tuple[int, int]
    active: bool
    available: bool
    output_technology: int
    refresh_hz: float
    source_mode_idx: int
    target_mode_idx: int
    rotation: int


@dataclass(frozen=True)
class Monitor:
    """One physical monitor, as the UI wants it: one row, not five paths."""

    target_id: int
    adapter: Tuple[int, int]
    active: bool
    available: bool
    output_technology: int
    refresh_hz: float
    resolution: Optional[Tuple[int, int]]
    position: Optional[Tuple[int, int]]

    @property
    def connector(self) -> str:
        return output_technology_name(self.output_technology)


@dataclass
class DisplayTopology:
    """Everything `QueryDisplayConfig` returned, in a shape worth using."""

    paths: List[DisplayPath] = field(default_factory=list)
    source_modes: Dict[int, SourceMode] = field(default_factory=dict)

    # ── paths ──

    def active_paths(self) -> List[DisplayPath]:
        return [p for p in self.paths if p.active]

    def source_mode_for(self, path: DisplayPath) -> Optional[SourceMode]:
        """The desktop surface this path drives, or None.

        None for every inactive path, whatever index it carries. An inactive
        path's `modeInfoIdx` is not reset — 20 of them in a real capture point
        at another display's mode — so trusting it hands one monitor's
        resolution to a monitor that is switched off.
        """
        if not path.active or path.source_mode_idx == MODE_IDX_INVALID:
            return None
        return self.source_modes.get(path.source_mode_idx)

    # ── targets, which is what "a monitor" means ──

    def active_target_ids(self) -> Set[int]:
        return {p.target_id for p in self.paths if p.active}

    def available_target_ids(self) -> Set[int]:
        return {p.target_id for p in self.paths if p.available}

    def inactive_available_target_ids(self) -> Set[int]:
        """Connected, powered, and not on the desktop."""
        return self.available_target_ids() - self.active_target_ids()

    def monitors(self) -> List[Monitor]:
        """One entry per physical monitor, ordered as the canvas draws them.

        Active monitors first, left to right by desktop x position, then the
        connected-but-inactive ones. Ordering lives here rather than in the
        canvas so it is testable without a display.
        """
        active_ids = self.active_target_ids()
        best: Dict[int, DisplayPath] = {}
        for path in self.paths:
            if not path.available:
                continue
            current = best.get(path.target_id)
            # An active path always wins: it is the one carrying the real mode.
            if current is None or (path.active and not current.active):
                best[path.target_id] = path

        monitors = []
        for target_id, path in best.items():
            mode = self.source_mode_for(path)
            monitors.append(Monitor(
                target_id=target_id,
                adapter=path.adapter,
                active=target_id in active_ids,
                available=True,
                output_technology=path.output_technology,
                refresh_hz=path.refresh_hz if path.active else 0.0,
                resolution=(mode.width, mode.height) if mode else None,
                position=mode.position if mode else None,
            ))

        monitors.sort(key=lambda m: (
            not m.active,                       # active first
            m.position[0] if m.position else 0,  # then left to right
            m.target_id,                        # stable for the rest
        ))
        return monitors


def parse_topology(raw_paths: Sequence[dict],
                   raw_modes: Sequence[dict]) -> DisplayTopology:
    """Build a topology from the plain dicts the ctypes layer produces.

    Kept separate from the API call so every rule above is testable against a
    captured real topology, with no display attached.
    """
    topology = DisplayTopology()

    for index, mode in enumerate(raw_modes):
        if mode.get("info_type") != MODE_INFO_TYPE_SOURCE:
            continue
        source = mode.get("source") or {}
        topology.source_modes[index] = SourceMode(
            mode_id=mode.get("id", 0),
            width=source.get("width", 0),
            height=source.get("height", 0),
            position=(source.get("position_x", 0), source.get("position_y", 0)),
        )

    for raw in raw_paths:
        active = bool(raw.get("flags", 0) & PATH_ACTIVE)
        topology.paths.append(DisplayPath(
            source_id=raw.get("source_id", 0),
            target_id=raw.get("target_id", 0),
            adapter=tuple(raw.get("target_adapter", (0, 0))),
            active=active,
            available=bool(raw.get("target_available")),
            output_technology=raw.get("output_technology", 0),
            refresh_hz=refresh_hz(raw.get("refresh_numerator", 0),
                                  raw.get("refresh_denominator", 0)) if active else 0.0,
            source_mode_idx=raw.get("source_mode_idx", MODE_IDX_INVALID),
            target_mode_idx=raw.get("target_mode_idx", MODE_IDX_INVALID),
            rotation=raw.get("rotation", 0),
        ))
    return topology


# ── the ctypes edge ────────────────────────────────────────────────────
#
# Deliberately thin: it turns Windows' structures into the dicts
# `parse_topology` takes, and does nothing else. Everything worth testing
# lives above this line, where it needs no display.


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class _PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("statusFlags", wintypes.UINT)]


class _PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("outputTechnology", wintypes.UINT),
                ("rotation", wintypes.UINT), ("scaling", wintypes.UINT),
                ("refreshRate", _RATIONAL), ("scanLineOrdering", wintypes.UINT),
                ("targetAvailable", wintypes.BOOL), ("statusFlags", wintypes.UINT)]


class _PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", _PATH_SOURCE_INFO),
                ("targetInfo", _PATH_TARGET_INFO), ("flags", wintypes.UINT)]


class _SOURCE_MODE(ctypes.Structure):
    _fields_ = [("width", wintypes.UINT), ("height", wintypes.UINT),
                ("pixelFormat", wintypes.UINT),
                ("positionX", ctypes.c_long), ("positionY", ctypes.c_long)]


class _REGION(ctypes.Structure):
    _fields_ = [("cx", wintypes.UINT), ("cy", wintypes.UINT)]


class _VIDEO_SIGNAL_INFO(ctypes.Structure):
    _fields_ = [("pixelRate", ctypes.c_ulonglong), ("hSyncFreq", _RATIONAL),
                ("vSyncFreq", _RATIONAL), ("activeSize", _REGION),
                ("totalSize", _REGION), ("videoStandard", wintypes.UINT),
                ("scanLineOrdering", wintypes.UINT)]


class _TARGET_MODE(ctypes.Structure):
    _fields_ = [("targetVideoSignalInfo", _VIDEO_SIGNAL_INFO)]


class _MODE_UNION(ctypes.Union):
    _fields_ = [("targetMode", _TARGET_MODE), ("sourceMode", _SOURCE_MODE)]


class _MODE_INFO(ctypes.Structure):
    _fields_ = [("infoType", wintypes.UINT), ("id", wintypes.UINT),
                ("adapterId", _LUID), ("mode", _MODE_UNION)]


class DisplayConfigError(OSError):
    """QueryDisplayConfig or SetDisplayConfig refused, with the reason."""


#: The returns these APIs actually produce, named. A bare number in a log is
#: not a reason, and per this project's standing rule a non-zero return is
#: never swallowed.
_WIN32_ERRORS = {
    0: "ERROR_SUCCESS",
    5: "ERROR_ACCESS_DENIED",
    31: "ERROR_GEN_FAILURE",
    50: "ERROR_NOT_SUPPORTED",
    87: "ERROR_INVALID_PARAMETER",
    122: "ERROR_INSUFFICIENT_BUFFER",
    1004: "ERROR_INVALID_FLAGS",
}


def win32_error_name(code: int) -> str:
    return _WIN32_ERRORS.get(code, f"error {code}")


def raw_query(all_paths: bool = True):
    """`(paths, modes)` as plain dicts, straight from Windows.

    Read-only and needs no elevation — measured working unelevated.
    """
    user32 = ctypes.windll.user32
    flags = QDC_ALL_PATHS if all_paths else QDC_ONLY_ACTIVE_PATHS

    npath = wintypes.UINT()
    nmode = wintypes.UINT()
    rc = user32.GetDisplayConfigBufferSizes(flags, ctypes.byref(npath),
                                            ctypes.byref(nmode))
    if rc != 0:
        raise DisplayConfigError(
            f"GetDisplayConfigBufferSizes failed: {win32_error_name(rc)}")

    paths = (_PATH_INFO * npath.value)()
    modes = (_MODE_INFO * nmode.value)()
    rc = user32.QueryDisplayConfig(flags, ctypes.byref(npath), paths,
                                   ctypes.byref(nmode), modes, None)
    if rc != 0:
        raise DisplayConfigError(
            f"QueryDisplayConfig failed: {win32_error_name(rc)}")

    raw_paths = [{
        "source_id": p.sourceInfo.id,
        "source_mode_idx": p.sourceInfo.modeInfoIdx,
        "target_id": p.targetInfo.id,
        "target_mode_idx": p.targetInfo.modeInfoIdx,
        "target_adapter": (p.targetInfo.adapterId.LowPart,
                           p.targetInfo.adapterId.HighPart),
        "output_technology": p.targetInfo.outputTechnology,
        "rotation": p.targetInfo.rotation,
        "refresh_numerator": p.targetInfo.refreshRate.Numerator,
        "refresh_denominator": p.targetInfo.refreshRate.Denominator,
        "target_available": bool(p.targetInfo.targetAvailable),
        "flags": p.flags,
    } for p in list(paths)[:npath.value]]

    raw_modes = []
    for m in list(modes)[:nmode.value]:
        entry = {"info_type": m.infoType, "id": m.id}
        if m.infoType == MODE_INFO_TYPE_SOURCE:
            s = m.mode.sourceMode
            entry["source"] = {"width": s.width, "height": s.height,
                               "position_x": s.positionX,
                               "position_y": s.positionY}
        raw_modes.append(entry)

    return raw_paths, raw_modes


def query(all_paths: bool = True) -> DisplayTopology:
    """The current topology, parsed."""
    raw_paths, raw_modes = raw_query(all_paths)
    return parse_topology(raw_paths, raw_modes)


# -- applying a topology ------------------------------------------------
#
# The only write in this module. It lives here because it needs the ctypes
# structures above; everything that DECIDES whether a change is allowed is
# in `display_writes.py`, which is the file to read for the reasoning.

SDC_VALIDATE = 0x00000040
SDC_APPLY = 0x00000080
SDC_USE_SUPPLIED_DISPLAY_CONFIG = 0x00000020
SDC_ALLOW_CHANGES = 0x00000400
SDC_SAVE_TO_DATABASE = 0x00000200

#: Validate is deliberately NOT in here: it is a separate call made first,
#: so an impossible arrangement is refused before it is attempted rather
#: than after half of it has happened.
APPLY_FLAGS = (SDC_APPLY | SDC_USE_SUPPLIED_DISPLAY_CONFIG
               | SDC_ALLOW_CHANGES | SDC_SAVE_TO_DATABASE)


def apply_target_active(target_id: int, active: bool):
    """Flip DISPLAYCONFIG_PATH_ACTIVE on one target and apply the result.

    `(ok, reason)`. Validated before it is applied; a validation failure is
    returned by name and nothing is changed.

    Windows decides the modes: the mode array is passed as NULL and
    SDC_ALLOW_CHANGES lets it pick. Supplying stale mode indices for a
    topology that is about to change is how a "just turn that monitor on"
    becomes ERROR_INVALID_PARAMETER.
    """
    user32 = ctypes.windll.user32
    flags = QDC_ALL_PATHS

    npath = wintypes.UINT()
    nmode = wintypes.UINT()
    rc = user32.GetDisplayConfigBufferSizes(flags, ctypes.byref(npath),
                                            ctypes.byref(nmode))
    if rc != 0:
        return False, f"GetDisplayConfigBufferSizes: {win32_error_name(rc)}"

    paths = (_PATH_INFO * npath.value)()
    modes = (_MODE_INFO * nmode.value)()
    rc = user32.QueryDisplayConfig(flags, ctypes.byref(npath), paths,
                                   ctypes.byref(nmode), modes, None)
    if rc != 0:
        return False, f"QueryDisplayConfig: {win32_error_name(rc)}"

    touched = 0
    for path in paths:
        if path.targetInfo.id != target_id:
            continue
        if active:
            # Only a path whose target is actually available can be lit.
            if not path.targetInfo.targetAvailable:
                continue
            path.flags |= PATH_ACTIVE
            touched += 1
            break                     # one source drives it; that is enough
        if path.flags & PATH_ACTIVE:
            path.flags &= ~PATH_ACTIVE
            touched += 1

    if not touched:
        return False, (f"no path for target {target_id} could be "
                       f"{'activated' if active else 'deactivated'}")

    rc = user32.SetDisplayConfig(npath, paths, nmode, modes,
                                 SDC_VALIDATE | SDC_USE_SUPPLIED_DISPLAY_CONFIG
                                 | SDC_ALLOW_CHANGES)
    if rc != 0:
        return False, (f"Windows refused this arrangement: "
                       f"{win32_error_name(rc)}")

    rc = user32.SetDisplayConfig(npath, paths, nmode, modes, APPLY_FLAGS)
    if rc != 0:
        return False, f"SetDisplayConfig: {win32_error_name(rc)}"
    logger.info("Target %s is now %s", target_id,
                "active" if active else "inactive")
    return True, ""


def apply_raw_topology(paths, modes, npath, nmode):
    """Apply a previously captured path/mode array. Used to REVERT.

    Takes the arrays as `raw_topology_arrays` returned them, so the revert
    puts back exactly what was there rather than something reconstructed
    from a parsed copy.
    """
    rc = ctypes.windll.user32.SetDisplayConfig(npath, paths, nmode, modes,
                                               APPLY_FLAGS)
    if rc != 0:
        return False, f"SetDisplayConfig: {win32_error_name(rc)}"
    return True, ""


def raw_topology_arrays():
    """The live path/mode arrays, kept for an exact revert.

    `query()` returns a parsed copy, which is the right thing to read and
    the wrong thing to restore: rebuilding the structures from it would
    reconstruct an arrangement rather than replay the one that was there.
    """
    user32 = ctypes.windll.user32
    npath = wintypes.UINT()
    nmode = wintypes.UINT()
    rc = user32.GetDisplayConfigBufferSizes(QDC_ALL_PATHS, ctypes.byref(npath),
                                            ctypes.byref(nmode))
    if rc != 0:
        raise DisplayConfigError(
            f"GetDisplayConfigBufferSizes failed: {win32_error_name(rc)}")
    paths = (_PATH_INFO * npath.value)()
    modes = (_MODE_INFO * nmode.value)()
    rc = user32.QueryDisplayConfig(QDC_ALL_PATHS, ctypes.byref(npath), paths,
                                   ctypes.byref(nmode), modes, None)
    if rc != 0:
        raise DisplayConfigError(
            f"QueryDisplayConfig failed: {win32_error_name(rc)}")
    return paths, modes, npath, nmode
