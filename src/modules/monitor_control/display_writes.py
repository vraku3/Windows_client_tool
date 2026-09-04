r"""Everything in Monitor Control that changes a display.

Deliberately one file. `display_config` and `display_modes` say what is and
what is possible, and both promise in their own docstrings to be read-only;
keeping that promise means the dangerous half lives here, where it can be
reviewed in one place.

A resolution or refresh rate a monitor cannot show leaves the user looking
at "no signal", and the control that would undo it is on the screen that
just went dark. So this refuses more readily than it acts:

* **A mode the device does not enumerate is refused, not attempted.** A
  driver will often accept a plausible-looking mode and then show nothing.
* **No mode list means no permission.** An enumeration that could not be
  read is "we do not know", never "go ahead".
* **`DISP_CHANGE_RESTART` is not success.** It means the mode was accepted
  and is not in effect; reporting it as done leaves someone staring at the
  old mode wondering what happened.
* **Deactivating the last active display is refused.** Windows will do it,
  and the result is a machine with no visible output.
* **Multi-monitor changes are staged and committed once.** Applying each
  device as it is set produces a visible cascade and can transiently leave
  two monitors sharing an origin.

Nothing here is called without going through `_apply_guard`, which
snapshots first and reverts on a timeout.
"""
from __future__ import annotations

import ctypes
import logging
from typing import List, Optional, Sequence, Tuple

from modules.monitor_control import display_config as dc
from modules.monitor_control import display_modes as dm

logger = logging.getLogger(__name__)

# -- ChangeDisplaySettingsEx -------------------------------------------------

CDS_UPDATEREGISTRY = 0x00000001
CDS_NORESET = 0x10000000
CDS_SET_PRIMARY = 0x00000010

#: Staged, so a multi-monitor change can be committed in one go.
STAGE_FLAGS = CDS_UPDATEREGISTRY | CDS_NORESET
COMMIT_FLAGS = 0

DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000
DM_POSITION = 0x00000020
DM_DISPLAYORIENTATION = 0x00000080

#: DISP_CHANGE_*. Named, because a bare -2 in a log is not a reason.
_CHANGE_RESULTS = {
    0: "DISP_CHANGE_SUCCESSFUL",
    1: "DISP_CHANGE_RESTART (accepted, but NOT in effect until a restart)",
    -1: "DISP_CHANGE_FAILED",
    -2: "DISP_CHANGE_BADMODE (the display cannot show this mode)",
    -3: "DISP_CHANGE_NOTUPDATED (could not write it to the registry)",
    -4: "DISP_CHANGE_BADFLAGS",
    -5: "DISP_CHANGE_BADPARAM",
    -6: "DISP_CHANGE_BADDUALVIEW",
}


def change_result_name(code: int) -> str:
    return _CHANGE_RESULTS.get(code, f"unknown result {code}")


def change_succeeded(code: int) -> bool:
    """Only 0. `DISP_CHANGE_RESTART` is explicitly not success."""
    return code == 0


# -- is this mode real? ------------------------------------------------------


def mode_is_offered(modes: Sequence, width: int, height: int, refresh: float,
                    tolerance: float = 0.6) -> Tuple[bool, str]:
    """`(ok, reason)` — is this exact mode in the device's own list?

    `tolerance` covers 59.94 against 60: they name one mode to a person and
    the enumeration holds only one of them.
    """
    if not modes:
        return False, ("this display's mode list could not be read, so the "
                       "mode was not attempted")
    at_resolution = [m for m in modes
                     if m.width == width and m.height == height]
    if not at_resolution:
        return False, f"this display does not offer {width}x{height}"
    for mode in at_resolution:
        if mode.refresh_hz is None:
            continue
        if abs(mode.refresh_hz - refresh) <= tolerance:
            return True, ""
    offered = ", ".join(f"{m.refresh_hz:g}" for m in at_resolution
                        if m.refresh_hz is not None)
    return False, (f"this display does not offer {refresh:g} Hz at "
                   f"{width}x{height} (it offers {offered or 'nothing'})")


# -- writing a mode ----------------------------------------------------------


def _devmode_for(width: int, height: int, refresh: float):
    devmode = dm._DEVMODEW()
    devmode.dmSize = ctypes.sizeof(dm._DEVMODEW)
    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmDisplayFrequency = int(round(refresh))
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    return devmode


def stage_mode(gdi_device_name: str, width: int, height: int,
               refresh: float) -> int:
    """Write one device's mode to the registry WITHOUT applying it."""
    devmode = _devmode_for(width, height, refresh)
    return ctypes.windll.user32.ChangeDisplaySettingsExW(
        ctypes.c_wchar_p(gdi_device_name), ctypes.byref(devmode), None,
        STAGE_FLAGS, None)


def commit_staged_modes() -> int:
    """Apply everything staged, together."""
    return ctypes.windll.user32.ChangeDisplaySettingsExW(
        None, None, None, COMMIT_FLAGS, None)


def apply_mode(gdi_device_name: str, width: int, height: int, refresh: float,
               modes: Optional[Sequence] = None) -> Tuple[bool, str]:
    """Set one display's mode, refusing anything it does not offer."""
    available = modes if modes is not None else dm.modes_for(gdi_device_name)
    ok, reason = mode_is_offered(available, width, height, refresh)
    if not ok:
        logger.info("Refusing %dx%d@%g on %s: %s",
                    width, height, refresh, gdi_device_name, reason)
        return False, reason

    staged = stage_mode(gdi_device_name, width, height, refresh)
    if not change_succeeded(staged):
        return False, (f"staging {width}x{height}@{refresh:g} failed: "
                       f"{change_result_name(staged)}")
    committed = commit_staged_modes()
    if not change_succeeded(committed):
        return False, (f"committing {width}x{height}@{refresh:g} failed: "
                       f"{change_result_name(committed)}")
    logger.info("Set %s to %dx%d@%g", gdi_device_name, width, height, refresh)
    return True, ""


def apply_modes(changes: Sequence[Tuple[str, int, int, float]]
                ) -> Tuple[bool, str]:
    """Several displays at once: stage them all, then one commit.

    Refuses the WHOLE batch if any single mode is not offered — a partially
    applied arrangement is worse than none, because the user then has to
    work out which half happened.
    """
    for device, width, height, refresh in changes:
        ok, reason = mode_is_offered(dm.modes_for(device), width, height,
                                     refresh)
        if not ok:
            return False, f"{device}: {reason}"

    for device, width, height, refresh in changes:
        staged = stage_mode(device, width, height, refresh)
        if not change_succeeded(staged):
            return False, (f"{device}: staging failed: "
                           f"{change_result_name(staged)}")
    committed = commit_staged_modes()
    if not change_succeeded(committed):
        return False, f"commit failed: {change_result_name(committed)}"
    return True, ""


# -- activating and deactivating a display ----------------------------------


def can_set_target_active(paths: Sequence, target_id: int,
                          active: bool) -> Tuple[bool, str]:
    """`(ok, reason)` for turning one monitor on or off.

    The refusal that matters: turning off the last active display. Windows
    permits it and the result is a machine showing nothing at all.
    """
    known = {p.target_id for p in paths}
    if target_id not in known:
        return False, f"target {target_id} is not in the display topology"

    for_target = [p for p in paths if p.target_id == target_id]
    currently_active = any(p.active for p in for_target)
    available = any(p.available for p in for_target)

    if active:
        if currently_active:
            return True, "that display is already in use"
        if not available:
            return False, ("that display is not available — it is not "
                           "connected, or the GPU is not driving its input")
        return True, ""

    if not currently_active:
        return True, "that display is already not in use"
    active_targets = {p.target_id for p in paths if p.active}
    if active_targets <= {target_id}:
        return False, ("that is the only display in use — turning it off "
                       "would leave the machine with no visible output")
    return True, ""


def set_target_active(target_id: int, active: bool) -> Tuple[bool, str]:
    """Add or remove one monitor from the desktop.

    Reads the current topology, flips `DISPLAYCONFIG_PATH_ACTIVE` on that
    target's paths, VALIDATES the result, and only then applies it.
    """
    topology = dc.query(all_paths=True)
    ok, reason = can_set_target_active(topology.paths, target_id, active)
    if not ok:
        logger.info("Refusing to set target %s active=%s: %s",
                    target_id, active, reason)
        return False, reason
    if reason:                                # an "already" no-op
        return True, reason
    return dc.apply_target_active(target_id, active)


# -- arrangement, i.e. what Win+P offers ------------------------------------

#: SDC_TOPOLOGY_*. Alternatives, never combined: Windows treats these as one
#: choice, and OR-ing two is not "either of these", it is invalid.
SDC_TOPOLOGY_INTERNAL = 0x00000001
SDC_TOPOLOGY_CLONE = 0x00000002
SDC_TOPOLOGY_EXTEND = 0x00000004
SDC_TOPOLOGY_EXTERNAL = 0x00000008

ARRANGEMENTS = {
    "internal": SDC_TOPOLOGY_INTERNAL,
    "clone": SDC_TOPOLOGY_CLONE,
    "extend": SDC_TOPOLOGY_EXTEND,
    "external": SDC_TOPOLOGY_EXTERNAL,
}

#: Human labels, in the order Win+P shows them.
ARRANGEMENT_LABELS = [
    ("internal", "Primary only"),
    ("clone", "Duplicate"),
    ("extend", "Extend"),
    ("external", "Second screen only"),
]


def can_set_arrangement(name: str) -> Tuple[bool, str]:
    """`(ok, reason)`. An unknown name is refused rather than guessed."""
    if name not in ARRANGEMENTS:
        known = ", ".join(sorted(ARRANGEMENTS))
        return False, f"unknown arrangement {name!r} (known: {known})"
    return True, ""


def set_arrangement(name: str) -> Tuple[bool, str]:
    """Switch between extend / duplicate / one-screen-only.

    Uses the topology flags rather than shelling out to DisplaySwitch.exe:
    the flag form returns a result that can be reported, where the exe
    returns before Windows has finished and tells you nothing.

    SDC_TOPOLOGY_* is passed WITHOUT SDC_USE_SUPPLIED_DISPLAY_CONFIG — they
    are two different ways of saying what to apply, and combining them is
    ERROR_INVALID_PARAMETER.
    """
    ok, reason = can_set_arrangement(name)
    if not ok:
        return False, reason

    flag = ARRANGEMENTS[name]
    user32 = ctypes.windll.user32
    rc = user32.SetDisplayConfig(0, None, 0, None,
                                 flag | dc.SDC_APPLY | dc.SDC_VALIDATE)
    if rc != 0:
        return False, (f"Windows will not arrange the displays that way: "
                       f"{dc.win32_error_name(rc)}")
    rc = user32.SetDisplayConfig(0, None, 0, None,
                                 flag | dc.SDC_APPLY | dc.SDC_SAVE_TO_DATABASE)
    if rc != 0:
        return False, f"SetDisplayConfig: {dc.win32_error_name(rc)}"
    logger.info("Display arrangement set to %s", name)
    return True, ""
