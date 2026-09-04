r"""Capture where every window is, and put them back.

The point of this module is the twenty seconds after a monitor comes back:
Windows has already piled every window onto whatever display survived, and
dragging thirty of them back is the actual cost of unplugging a display. So a
layout is captured before, and replayed after.

**`SetWindowPlacement`, never `MoveWindow`.** This is the one rule the whole
restore path is built around, and it is not a style preference:

A maximised window has *two* rectangles. On screen it fills the monitor; it
also remembers the rect it will return to when un-maximised. `WINDOWPLACEMENT`
carries both — `showCmd` (`SW_SHOWMAXIMIZED`) plus `rcNormalPosition` (the
restored rect) — and `SetWindowPlacement` writes both back. `MoveWindow` can
carry exactly one rect and no state. Handed a maximised window it un-maximises
it into the rect you gave it, so replaying the *screen* rect produces a window
that is not maximised but happens to be monitor-sized, whose restore-down does
nothing visible; replaying the *restored* rect drops the maximised state
entirely. There is no rect that makes `MoveWindow` correct here.

Snapped windows are the same problem wearing a disguise. Windows records no
"snapped" state anywhere: a snapped window reports `SW_SHOWNORMAL` with an
on-screen rect that disagrees with `rcNormalPosition`, and that disagreement is
the only signal there is (`WindowRecord.is_snapped` reads it). What has to
survive a restore is the *restored* rect, which again only
`SetWindowPlacement` can express.

**Monitors are named by EDID, not by index.** A layout is only worth having
across a topology change, and `HMONITOR` handles are per-session while
`\\.\DISPLAY2` renumbers when a cable moves. Each record carries the
`profiles.MonitorIdentity` key of the monitor it was on; a window whose
monitor is not present is **refused, by name**, never dropped onto whatever
display happens to be there.

**A monitor that came back somewhere else takes its windows with it.**
Unplug the left monitor and the right one becomes primary at x=0 — the same
physical panel, 2560 pixels to the left. Replaying absolute coordinates would
scatter windows onto empty desktop, so `plan_restore` translates each
placement by the monitor's origin delta. Translation only: the offset is added
to both corners, so a window never changes size.

`restore()` **does not write unless `apply=True`.** The default is a plan.

No Qt here, and Windows is reached through a `probe` object, so every rule
above is testable with no display and without moving a single real window.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from modules.monitor_control import profiles as profiles_mod
from modules.monitor_control import window_census as census_mod

logger = logging.getLogger(__name__)

#: WINDOWPLACEMENT.showCmd values that matter here.
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_SHOWNOACTIVATE = 4
SW_SHOWMINNOACTIVE = 7
SW_RESTORE = 9

#: WPF_ASYNCWINDOWPLACEMENT — set on restore so a window belonging to a hung
#: or busy process does not block the whole replay.
WPF_ASYNCWINDOWPLACEMENT = 0x0004

#: The only restore method. Named so a test can assert nothing else appears —
#: see the module docstring for why `MoveWindow` is not an option.
SET_WINDOW_PLACEMENT = "SetWindowPlacement"

_NO_MONITOR = "this window was never attributed to a monitor"


@dataclass(frozen=True)
class WindowPlacement:
    """`WINDOWPLACEMENT`, with the fields that carry the state."""

    show_cmd: int
    flags: int
    min_position: Tuple[int, int]
    max_position: Tuple[int, int]
    #: `rcNormalPosition` — where the window goes when it is neither
    #: maximised nor minimised. This is the rect a restore must preserve.
    normal_rect: Tuple[int, int, int, int]

    @property
    def is_maximized(self) -> bool:
        return self.show_cmd == SW_SHOWMAXIMIZED

    @property
    def is_minimized(self) -> bool:
        return self.show_cmd in (SW_SHOWMINIMIZED, SW_SHOWMINNOACTIVE)

    def translated(self, offset: Tuple[int, int]) -> "WindowPlacement":
        """The same placement, moved. Both corners shift by the same amount,
        so the window keeps its size exactly."""
        dx, dy = offset
        if (dx, dy) == (0, 0):
            return self
        left, top, right, bottom = self.normal_rect
        return WindowPlacement(
            show_cmd=self.show_cmd, flags=self.flags,
            min_position=_shift(self.min_position, dx, dy),
            max_position=_shift(self.max_position, dx, dy),
            normal_rect=(left + dx, top + dy, right + dx, bottom + dy))

    def to_dict(self) -> Dict[str, Any]:
        return {"show_cmd": self.show_cmd, "flags": self.flags,
                "min_position": list(self.min_position),
                "max_position": list(self.max_position),
                "normal_rect": list(self.normal_rect)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WindowPlacement":
        return cls(show_cmd=int(data.get("show_cmd", SW_SHOWNORMAL)),
                   flags=int(data.get("flags", 0)),
                   min_position=tuple(data.get("min_position", (-1, -1))),
                   max_position=tuple(data.get("max_position", (-1, -1))),
                   normal_rect=tuple(data.get("normal_rect", (0, 0, 0, 0))))


def _shift(point: Tuple[int, int], dx: int, dy: int) -> Tuple[int, int]:
    """Move a point, leaving the `(-1, -1)` "unset" marker alone.

    `ptMinPosition` and `ptMaxPosition` use -1 for "Windows will work it out".
    Translating that produces a real coordinate near the top-left corner,
    which is a placement instruction where none was intended.
    """
    if point == (-1, -1):
        return point
    return (point[0] + dx, point[1] + dy)


@dataclass(frozen=True)
class MonitorGeometry:
    """A monitor as the desktop sees it, keyed on EDID."""

    key: str
    name: str
    #: `\\.\DISPLAYn` at capture time. Recorded for diagnostics; nothing
    #: matches on it, because it renumbers.
    device: str
    bounds: Tuple[int, int, int, int]
    work_area: Tuple[int, int, int, int]
    primary: bool

    @property
    def origin(self) -> Tuple[int, int]:
        return (self.bounds[0], self.bounds[1])

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "name": self.name, "device": self.device,
                "bounds": list(self.bounds), "work_area": list(self.work_area),
                "primary": self.primary}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MonitorGeometry":
        return cls(key=data.get("key", ""), name=data.get("name", ""),
                   device=data.get("device", ""),
                   bounds=tuple(data.get("bounds", (0, 0, 0, 0))),
                   work_area=tuple(data.get("work_area", (0, 0, 0, 0))),
                   primary=bool(data.get("primary", False)))


@dataclass(frozen=True)
class WindowRecord:
    """One window, its placement, and which monitor it was on."""

    hwnd: int
    title: str
    process_name: Optional[str]
    process_id: int
    placement: WindowPlacement
    #: `GetWindowRect` — where the window actually was on screen. Kept apart
    #: from `placement.normal_rect` because their disagreement is the only
    #: way to detect a snapped window.
    rect: Tuple[int, int, int, int]
    monitor_key: Optional[str]
    #: Empty when `monitor_key` is a real answer; otherwise why it is None.
    monitor_reason: str = ""

    @property
    def is_snapped(self) -> bool:
        """A normal window whose on-screen rect is not its restored rect.

        Windows stores no snap flag. This is the whole detection, and it is
        why `rect` is captured alongside the placement.
        """
        return (self.placement.show_cmd in (SW_SHOWNORMAL, SW_SHOWNOACTIVATE)
                and self.rect != self.placement.normal_rect)

    def to_dict(self) -> Dict[str, Any]:
        return {"hwnd": self.hwnd, "title": self.title,
                "process_name": self.process_name,
                "process_id": self.process_id,
                "placement": self.placement.to_dict(), "rect": list(self.rect),
                "monitor_key": self.monitor_key,
                "monitor_reason": self.monitor_reason}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WindowRecord":
        return cls(hwnd=int(data.get("hwnd", 0)), title=data.get("title", ""),
                   process_name=data.get("process_name"),
                   process_id=int(data.get("process_id", 0)),
                   placement=WindowPlacement.from_dict(data.get("placement", {})),
                   rect=tuple(data.get("rect", (0, 0, 0, 0))),
                   monitor_key=data.get("monitor_key"),
                   monitor_reason=data.get("monitor_reason", ""))


@dataclass(frozen=True)
class WindowLayout:
    """Every qualifying window, and the monitors they were spread across."""

    captured_at: str
    monitors: Dict[str, MonitorGeometry] = field(default_factory=dict)
    windows: List[WindowRecord] = field(default_factory=list)

    def on(self, key: str) -> List[WindowRecord]:
        return [w for w in self.windows if w.monitor_key == key]

    def counts(self) -> Dict[str, int]:
        """EDID key → window count, which is what a "3 windows will move to
        Dell S2719DGF" warning is made of."""
        counts: Dict[str, int] = {}
        for window in self.windows:
            if window.monitor_key:
                counts[window.monitor_key] = counts.get(window.monitor_key, 0) + 1
        return counts

    def to_dict(self) -> Dict[str, Any]:
        return {"captured_at": self.captured_at,
                "monitors": {k: g.to_dict() for k, g in self.monitors.items()},
                "windows": [w.to_dict() for w in self.windows]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WindowLayout":
        return cls(
            captured_at=data.get("captured_at", ""),
            monitors={k: MonitorGeometry.from_dict(v)
                      for k, v in (data.get("monitors") or {}).items()},
            windows=[WindowRecord.from_dict(w) for w in data.get("windows", [])])


# ══ the restore plan ═══════════════════════════════════════════════════

@dataclass(frozen=True)
class RestoreAction:
    """One window that can go back, and exactly how."""

    hwnd: int
    title: str
    method: str
    placement: WindowPlacement
    monitor_key: str
    #: How far the monitor has moved since capture. `(0, 0)` when it has not.
    offset: Tuple[int, int]
    reason: str = ""


@dataclass(frozen=True)
class RestoreRefusal:
    """One window that will not be moved, and why. Never silent."""

    hwnd: int
    title: str
    reason: str


@dataclass(frozen=True)
class RestorePlan:
    actions: List[RestoreAction] = field(default_factory=list)
    refusals: List[RestoreRefusal] = field(default_factory=list)
    failures: List[RestoreRefusal] = field(default_factory=list)
    applied: bool = False

    @property
    def total(self) -> int:
        return len(self.actions) + len(self.refusals)


def plan_restore(layout: WindowLayout,
                 present: Mapping[str, MonitorGeometry]) -> RestorePlan:
    """What a restore would do. Pure — nothing here touches a window.

    A window is refused when its monitor is not in `present`, or when it was
    never attributed to one. Everything else becomes a `SetWindowPlacement`
    action, translated by however far its monitor has moved.
    """
    actions: List[RestoreAction] = []
    refusals: List[RestoreRefusal] = []

    for window in layout.windows:
        key = window.monitor_key
        if not key:
            refusals.append(RestoreRefusal(
                window.hwnd, window.title,
                window.monitor_reason or _NO_MONITOR))
            continue

        target = present.get(key)
        if target is None:
            captured = layout.monitors.get(key)
            name = captured.name if captured else key
            refusals.append(RestoreRefusal(
                window.hwnd, window.title,
                f"the monitor this window was on is not connected: "
                f"{name} [{key}]"))
            continue

        captured = layout.monitors.get(key)
        if captured is None:
            offset = (0, 0)
            note = (f"the captured geometry for {key} is missing; the window "
                    f"is replayed at its absolute position")
        else:
            offset = (target.origin[0] - captured.origin[0],
                      target.origin[1] - captured.origin[1])
            note = ("" if offset == (0, 0) else
                    f"{target.name} moved by {offset} since capture")

        actions.append(RestoreAction(
            hwnd=window.hwnd, title=window.title, method=SET_WINDOW_PLACEMENT,
            placement=window.placement.translated(offset), monitor_key=key,
            offset=offset, reason=note))

    return RestorePlan(actions=actions, refusals=refusals)


def restore(layout: WindowLayout,
            present: Optional[Mapping[str, MonitorGeometry]] = None,
            probe=None, apply: bool = False) -> RestorePlan:
    """Put the windows back — but only when `apply=True`.

    The default is a dry run that returns the plan and writes nothing. That is
    deliberate: this is the one function in the module that changes what is on
    the user's screen, and a caller that has not said so explicitly gets a
    description instead.

    Each action is `SetWindowPlacement`, and only that (see the module
    docstring). A window that has gone away between capture and restore, or
    whose process refuses the call, is recorded in `failures` — the replay
    continues, because one dead window is not a reason to abandon the other
    twenty-nine.
    """
    probe = probe or Win32LayoutProbe()
    if present is None:
        present = probe.monitors_by_key()

    plan = plan_restore(layout, present)
    if not apply:
        return plan

    failures: List[RestoreRefusal] = []
    for action in plan.actions:
        placement = WindowPlacement(
            show_cmd=action.placement.show_cmd,
            flags=action.placement.flags | WPF_ASYNCWINDOWPLACEMENT,
            min_position=action.placement.min_position,
            max_position=action.placement.max_position,
            normal_rect=action.placement.normal_rect)
        try:
            ok = probe.set_placement(action.hwnd, placement)
        except OSError as exc:
            failures.append(RestoreRefusal(action.hwnd, action.title, str(exc)))
            continue
        if not ok:
            failures.append(RestoreRefusal(
                action.hwnd, action.title,
                "SetWindowPlacement refused (the window may have closed)"))

    logger.info("Restored %d window(s), refused %d, failed %d",
                len(plan.actions) - len(failures), len(plan.refusals),
                len(failures))
    return RestorePlan(actions=plan.actions, refusals=plan.refusals,
                       failures=failures, applied=True)


# ══ capture ════════════════════════════════════════════════════════════

def capture(probe=None) -> WindowLayout:
    """Where every qualifying window is right now. Read-only.

    "Qualifying" is `window_census`' definition — visible, not a tool window,
    not zero-size, not cloaked — so the layout holds the same windows the
    census counted and the two can never disagree.
    """
    probe = probe or Win32LayoutProbe()
    monitors = probe.monitors()
    result = census_mod.census(probe=probe)

    windows: List[WindowRecord] = []
    for window in result.windows:
        geometry = monitors.get(window.monitor)
        placement = probe.placement(window.hwnd)
        if placement is None:
            logger.warning("GetWindowPlacement refused for hwnd %s (%r)",
                           window.hwnd, window.title)
            continue
        windows.append(WindowRecord(
            hwnd=window.hwnd, title=window.title,
            process_name=window.process_name, process_id=window.process_id,
            placement=placement, rect=window.rect,
            monitor_key=geometry.key if geometry else None,
            monitor_reason=("" if geometry else
                            f"monitor handle {window.monitor} has no EDID "
                            f"identity in this topology")))

    return WindowLayout(
        captured_at=datetime.now().isoformat(timespec="seconds"),
        monitors={g.key: g for g in monitors.values()}, windows=windows)


def current_monitors(probe=None) -> Dict[str, MonitorGeometry]:
    """EDID key → geometry, as the desktop is right now."""
    probe = probe or Win32LayoutProbe()
    return probe.monitors_by_key()


# ══ the ctypes edge ════════════════════════════════════════════════════

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", _POINT),
                ("ptMaxPosition", _POINT), ("rcNormalPosition", wintypes.RECT)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128),
                ("DeviceKey", wintypes.WCHAR * 128)]


MONITORINFOF_PRIMARY = 0x00000001

_MonitorEnumProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
    ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)


class Win32LayoutProbe(census_mod.Win32WindowProbe):
    r"""The census probe plus placement and monitor identity.

    Bridging `HMONITOR` to an EDID key takes two hops, because Windows keeps
    the two display APIs apart: `GetMonitorInfoW` gives `\\.\DISPLAYn`, then
    `EnumDisplayDevicesW(that, 0, ..., EDD_GET_DEVICE_INTERFACE_NAME)` gives
    the `\\?\DISPLAY#...` device path, which is the same string the CCD API
    reports for the target. That is the join — measured byte-identical on this
    machine.
    """

    def __init__(self):
        super().__init__()
        self._identities: Optional[Dict[str, profiles_mod.MonitorIdentity]] = None

    # ── placement ──

    def placement(self, hwnd: int) -> Optional[WindowPlacement]:
        raw = _WINDOWPLACEMENT()
        raw.length = ctypes.sizeof(_WINDOWPLACEMENT)
        if not self._user32.GetWindowPlacement(wintypes.HWND(hwnd),
                                               ctypes.byref(raw)):
            return None
        rect = raw.rcNormalPosition
        return WindowPlacement(
            show_cmd=raw.showCmd, flags=raw.flags,
            min_position=(raw.ptMinPosition.x, raw.ptMinPosition.y),
            max_position=(raw.ptMaxPosition.x, raw.ptMaxPosition.y),
            normal_rect=(rect.left, rect.top, rect.right, rect.bottom))

    def set_placement(self, hwnd: int, placement: WindowPlacement) -> bool:
        """The one write in this package. Reached only from `restore(apply=True)`."""
        raw = _WINDOWPLACEMENT()
        raw.length = ctypes.sizeof(_WINDOWPLACEMENT)
        raw.flags = placement.flags
        raw.showCmd = placement.show_cmd
        raw.ptMinPosition.x, raw.ptMinPosition.y = placement.min_position
        raw.ptMaxPosition.x, raw.ptMaxPosition.y = placement.max_position
        (raw.rcNormalPosition.left, raw.rcNormalPosition.top,
         raw.rcNormalPosition.right, raw.rcNormalPosition.bottom) = \
            placement.normal_rect
        return bool(self._user32.SetWindowPlacement(wintypes.HWND(hwnd),
                                                    ctypes.byref(raw)))

    # ── monitors ──

    def _identity_by_device_path(self
                                 ) -> Dict[str, profiles_mod.MonitorIdentity]:
        if self._identities is None:
            self._identities = {
                (i.device_path or "").lower(): i
                for i in profiles_mod.live_identities()}
        return self._identities

    def monitors(self) -> Dict[int, MonitorGeometry]:
        """HMONITOR → geometry, for the monitors currently on the desktop."""
        handles: List[int] = []

        def collect(handle, _hdc, _rect, _lparam):
            handles.append(int(handle))
            return True

        self._user32.EnumDisplayMonitors(None, None,
                                         _MonitorEnumProc(collect), 0)

        identities = self._identity_by_device_path()
        result: Dict[int, MonitorGeometry] = {}
        for handle in handles:
            info = _MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
            if not self._user32.GetMonitorInfoW(wintypes.HMONITOR(handle),
                                                ctypes.byref(info)):
                logger.warning("GetMonitorInfoW failed for HMONITOR %s", handle)
                continue
            device_path = self._device_path_for(info.szDevice)
            identity = identities.get((device_path or "").lower())
            if identity is None:
                # No EDID identity means no stable key. Recording the display
                # name as a key would produce something that looks stable and
                # is not, so the monitor is skipped and the windows on it are
                # reported unattributed by `capture`.
                logger.warning("No EDID identity for %s (%s) — windows on it "
                               "cannot be restored by identity",
                               info.szDevice, device_path)
                continue
            monitor, work = info.rcMonitor, info.rcWork
            result[handle] = MonitorGeometry(
                key=identity.key, name=identity.friendly_name or identity.key,
                device=info.szDevice,
                bounds=(monitor.left, monitor.top, monitor.right, monitor.bottom),
                work_area=(work.left, work.top, work.right, work.bottom),
                primary=bool(info.dwFlags & MONITORINFOF_PRIMARY))
        return result

    def monitors_by_key(self) -> Dict[str, MonitorGeometry]:
        return {g.key: g for g in self.monitors().values()}

    def _device_path_for(self, display_name: str) -> Optional[str]:
        device = _DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
        if not self._user32.EnumDisplayDevicesW(
                display_name, 0, ctypes.byref(device),
                profiles_mod.EDD_GET_DEVICE_INTERFACE_NAME):
            return None
        return device.DeviceID
