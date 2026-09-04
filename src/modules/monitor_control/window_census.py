r"""How many windows are sitting on each monitor.

This exists so the UI can say "3 windows will move to Dell S2719DGF" *before*
the user unplugs it, and the only hard part is deciding what a window is.

`EnumWindows` hands back several hundred top-level windows on an idle Windows
11 desktop; on the machine this was written for, roughly one in twenty is a
window a person would recognise. Four filters cut it down, and the fourth is
the one that is easy to miss:

* **`IsWindowVisible`** — the obvious one, and on its own nowhere near enough.
* **`WS_EX_TOOLWINDOW`** — floating palettes and helper frames that
  deliberately keep themselves out of the Alt-Tab list.
* **zero-size** — plenty of message-only and placeholder frames are visible
  and 0x0.
* **DWM cloaking** (`DwmGetWindowAttribute`, `DWMWA_CLOAKED` = 14) — a
  suspended UWP app, a background `ApplicationFrameHost`, a virtual desktop
  the user is not looking at, and several of Explorer's own helpers are all
  *visible*, not tool windows, and have real rects. Windows hides them by
  cloaking, which `IsWindowVisible` knows nothing about. Counting them is what
  turns "3 windows" into "9 windows", and the number is the entire product
  here — a census nobody trusts is worse than no census.

**Nothing in this module writes.** It enumerates and reads; moving windows is
`window_layout`'s job and even there it is opt-in.

Every read that can be refused is kept apart from a read that answered:

* a window whose cloak state could not be read is **counted, and listed in
  `undetermined`** — it is not silently dropped (that hides a real window) and
  not silently trusted (that would state a fact we do not have). The caller
  can render "3 windows (1 could not be checked)".
* a window `MonitorFromWindow` refused to place lands in `unattributed`, with
  the reason. It is never assigned to a monitor by guesswork.

Windows is reached through a `probe` object rather than called directly, so
every rule above is testable with no display and no windows.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: WS_EX_TOOLWINDOW — "keep me out of the taskbar and Alt-Tab".
WS_EX_TOOLWINDOW = 0x00000080

#: DWMWA_CLOAKED. Not in the visibility flags; a separate DWM question.
DWMWA_CLOAKED = 14

#: MONITOR_DEFAULTTONEAREST — a window dragged off the desktop still belongs
#: to the monitor it is closest to, which is where it will be found again.
MONITOR_DEFAULTTONEAREST = 0x00000002

#: PROCESS_QUERY_LIMITED_INFORMATION. Enough for the image name, and unlike
#: PROCESS_QUERY_INFORMATION it is granted for most processes unelevated.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

#: Why a window was left out. Exact strings — the tests pin them, and the UI
#: groups on them.
SKIP_INVISIBLE = "not visible"
SKIP_TOOLWINDOW = "tool window (WS_EX_TOOLWINDOW)"
SKIP_ZERO_SIZE = "zero-size"
SKIP_CLOAKED = "cloaked (DWM)"
SKIP_NO_RECT = "window rectangle could not be read"

_UNKNOWN_CLOAK = "cloak state could not be read (DwmGetWindowAttribute failed)"
_UNKNOWN_MONITOR = "MonitorFromWindow could not place this window"


@dataclass(frozen=True)
class WindowInfo:
    """A window that counts, and the monitor it counts towards."""

    hwnd: int
    title: str
    process_id: int
    process_name: Optional[str]
    rect: Tuple[int, int, int, int]
    monitor: int
    #: Empty when `process_name` is a real answer; otherwise why it is None.
    process_name_reason: str = ""
    #: Empty unless a read about this window was refused — see `Census`.
    caveat: str = ""

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


@dataclass(frozen=True)
class SkippedWindow:
    """A window that did not count, and why. Never dropped in silence."""

    hwnd: int
    title: str
    reason: str


@dataclass
class Census:
    """The whole enumeration, in four buckets that add up to what Windows
    returned. `windows + excluded + unattributed` is every enumerated handle;
    `undetermined` is a *view* over `windows`, not a fifth bucket."""

    windows: List[WindowInfo] = field(default_factory=list)
    excluded: List[SkippedWindow] = field(default_factory=list)
    unattributed: List[SkippedWindow] = field(default_factory=list)
    undetermined: List[SkippedWindow] = field(default_factory=list)

    def per_monitor(self) -> Dict[int, int]:
        """HMONITOR → count. A monitor with no windows is simply absent; the
        caller holds the monitor list and asks with `.get(handle, 0)`."""
        counts: Dict[int, int] = {}
        for window in self.windows:
            counts[window.monitor] = counts.get(window.monitor, 0) + 1
        return counts

    def on(self, monitor: int) -> List[WindowInfo]:
        return [w for w in self.windows if w.monitor == monitor]

    def uncertain_on(self, monitor: int) -> int:
        """How many of `on(monitor)` rest on a read that was refused, so the
        UI can append "(N could not be checked)" rather than overstate."""
        uncertain = {s.hwnd for s in self.undetermined}
        return sum(1 for w in self.windows
                   if w.monitor == monitor and w.hwnd in uncertain)


def skip_reason(*, visible: bool, ex_style: int,
                rect: Optional[Tuple[int, int, int, int]],
                cloaked: Optional[bool]) -> Optional[str]:
    """The reason to leave this window out, or None to count it.

    Pure, and the whole filtering policy. `cloaked is None` deliberately does
    NOT skip: an unreadable cloak state is not evidence of cloaking, and
    dropping the window would hide something real. The caller records it as
    undetermined instead.
    """
    if not visible:
        return SKIP_INVISIBLE
    if ex_style & WS_EX_TOOLWINDOW:
        return SKIP_TOOLWINDOW
    if rect is None:
        return SKIP_NO_RECT
    left, top, right, bottom = rect
    if right - left <= 0 or bottom - top <= 0:
        return SKIP_ZERO_SIZE
    if cloaked:
        return SKIP_CLOAKED
    return None


def census(probe=None) -> Census:
    """Enumerate the desktop's windows and sort them into the four buckets."""
    probe = probe or Win32WindowProbe()
    result = Census()

    for hwnd in probe.enum_windows():
        title = probe.title(hwnd)
        cloaked = probe.is_cloaked(hwnd)
        rect = probe.window_rect(hwnd)
        reason = skip_reason(visible=probe.is_visible(hwnd),
                             ex_style=probe.ex_style(hwnd),
                             rect=rect, cloaked=cloaked)
        if reason is not None:
            result.excluded.append(SkippedWindow(hwnd, title, reason))
            continue

        monitor = probe.monitor_of(hwnd)
        if monitor is None:
            result.unattributed.append(
                SkippedWindow(hwnd, title, _UNKNOWN_MONITOR))
            continue

        process_name, process_reason = probe.process_name(hwnd)
        caveat = _UNKNOWN_CLOAK if cloaked is None else ""
        if caveat:
            result.undetermined.append(SkippedWindow(hwnd, title, caveat))
        result.windows.append(WindowInfo(
            hwnd=hwnd, title=title, process_id=probe.process_id(hwnd),
            process_name=process_name, process_name_reason=process_reason,
            rect=rect, monitor=monitor, caveat=caveat))

    return result


def windows_per_monitor(probe=None) -> Dict[int, int]:
    """HMONITOR → how many top-level windows sit on it.

    The short answer. `census()` is the same walk with the exclusions and the
    refused reads kept, which is what a UI that has to explain itself wants.
    """
    return census(probe=probe).per_monitor()


# ── the ctypes edge ────────────────────────────────────────────────────
#
# Thin on purpose: it answers questions about one hwnd and holds no policy.
# Everything above this line is testable with no display.


_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                      wintypes.LPARAM)


class Win32WindowProbe:
    """Asks Windows the questions `census` needs. Read-only, no elevation."""

    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        try:
            self._dwmapi = ctypes.windll.dwmapi
        except OSError as exc:  # pragma: no cover - dwmapi ships with Vista+
            logger.warning("dwmapi unavailable, cloak state unreadable: %s", exc)
            self._dwmapi = None

    def enum_windows(self) -> List[int]:
        handles: List[int] = []

        def collect(hwnd, _lparam):
            handles.append(int(hwnd))
            return True

        if not self._user32.EnumWindows(_EnumWindowsProc(collect), 0):
            error = ctypes.get_last_error()
            # EnumWindows returns FALSE when a callback returns FALSE too, so
            # a zero error code with handles collected is not a failure.
            if error and not handles:
                raise OSError(error, "EnumWindows failed")
        return handles

    def is_visible(self, hwnd: int) -> bool:
        return bool(self._user32.IsWindowVisible(wintypes.HWND(hwnd)))

    def ex_style(self, hwnd: int) -> int:
        GWL_EXSTYLE = -20
        get = getattr(self._user32, "GetWindowLongPtrW", None) or \
            self._user32.GetWindowLongW
        get.restype = ctypes.c_longlong
        return int(get(wintypes.HWND(hwnd), GWL_EXSTYLE)) & 0xFFFFFFFF

    def window_rect(self, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(wintypes.HWND(hwnd),
                                          ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)

    def is_cloaked(self, hwnd: int) -> Optional[bool]:
        """True/False, or None when DWM would not say.

        None is a real outcome, not an error to swallow: `DwmGetWindowAttribute`
        fails for a window that is being destroyed mid-enumeration, and on a
        machine with the DWM composition off.
        """
        if self._dwmapi is None:
            return None
        value = wintypes.DWORD(0)
        hresult = self._dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), wintypes.DWORD(DWMWA_CLOAKED),
            ctypes.byref(value), ctypes.sizeof(value))
        if hresult != 0:
            return None
        return value.value != 0

    def monitor_of(self, hwnd: int) -> Optional[int]:
        handle = self._user32.MonitorFromWindow(wintypes.HWND(hwnd),
                                                MONITOR_DEFAULTTONEAREST)
        return int(handle) if handle else None

    def title(self, hwnd: int) -> str:
        length = self._user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
        return buffer.value

    def process_id(self, hwnd: int) -> int:
        pid = wintypes.DWORD(0)
        self._user32.GetWindowThreadProcessId(wintypes.HWND(hwnd),
                                              ctypes.byref(pid))
        return int(pid.value)

    def process_name(self, hwnd: int) -> Tuple[Optional[str], str]:
        """`(name, reason)`. `(None, why)` when the process would not be read.

        A protected or elevated process denies `OpenProcess` to an ordinary
        user; reporting an empty name for it reads as "no process", so the
        refusal is carried instead.
        """
        pid = self.process_id(hwnd)
        if not pid:
            return None, "no process id for this window"
        handle = self._kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None, (f"OpenProcess refused for pid {pid} "
                          f"(error {ctypes.get_last_error()})")
        try:
            size = wintypes.DWORD(260)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self._kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)):
                return None, (f"QueryFullProcessImageNameW failed for pid "
                              f"{pid} (error {ctypes.get_last_error()})")
            return buffer.value.rsplit("\\", 1)[-1], ""
        finally:
            self._kernel32.CloseHandle(handle)
