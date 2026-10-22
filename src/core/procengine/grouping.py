r"""Task Manager's Apps / Background / Windows split.

The Processes tab does not show a flat list -- it shows three groups, and the
grouping is the whole reason that tab is readable when the Details tab is
not. `chrome.exe` twelve times under "Google Chrome" is one row someone can
reason about.

**What makes something an App is a visible window, not a guess about its
name.** Task Manager asks the window manager; anything else is a heuristic
that gets `explorer.exe` wrong on one machine and `Code.exe` wrong on the
next. So this enumerates top-level windows once per refresh and asks which
pid owns each -- one `EnumWindows` pass, measured below.

Everything left over splits into Windows processes and background processes
on two signals: the token where it can be read (`SYSTEM`, `LOCAL SERVICE`,
`NETWORK SERVICE`), and session 0 where it cannot. The second is what makes
this work for an ordinary user -- 135 of this machine's 284 processes refuse
their token, every system process among them, so on the token alone the
Windows group came out empty. Neither signal is the PATH, so a user-installed
service running as SYSTEM is filed under Windows and a user's own program in
System32 is not.

Qt-free: `EnumWindows` is a plain Win32 call, and none of this needs a
widget.
"""
import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

APPS = "Apps"
BACKGROUND = "Background processes"
WINDOWS = "Windows processes"

#: The order the groups appear in, which is Task Manager's.
GROUP_ORDER = (APPS, BACKGROUND, WINDOWS)

#: Accounts whose processes are "Windows processes" rather than "Background".
#: Matched on the account name alone, so a domain prefix does not defeat it.
_SYSTEM_ACCOUNTS = {
    "SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE", "LOCALSYSTEM",
}

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.restype = wintypes.BOOL
_user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]

GW_OWNER = 4


@dataclass
class Group:
    """One heading and the rows under it."""

    name: str
    rows: List = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)


@dataclass
class AppEntry:
    """One app: the process that owns the window, plus its siblings.

    A browser is a dozen processes and one app. Rolling them up is what makes
    the tab readable, and the totals are summed across all of them so the
    number beside "Google Chrome" is what Chrome actually costs.
    """

    title: str
    pid: int
    members: List = field(default_factory=list)
    window_titles: List[str] = field(default_factory=list)


def windowed_pids() -> Dict[int, List[str]]:
    """Pids that own a visible top-level window, and those windows' titles.

    One `EnumWindows` pass. Owned windows (dialogs, tool windows) are skipped:
    counting them would list an app once per open dialog.
    """
    found: Dict[int, List[str]] = {}

    def visit(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            if _user32.GetWindow(hwnd, GW_OWNER):
                # An owned window belongs to another window's app.
                return True
            length = _user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                # No caption: a message-only or invisible helper window, not
                # something a person would call an app.
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buffer, length + 1)
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                found.setdefault(pid.value, []).append(buffer.value)
        except Exception:  # noqa: BLE001
            # This runs inside a callback invoked by Windows; an exception
            # escaping here crosses an ABI boundary, which is not survivable.
            return True
        return True

    try:
        _user32.EnumWindows(_ENUM_PROC(visit), 0)
    except OSError as error:
        logger.warning("Could not enumerate windows: %s", error)
    return found


def is_windows_process(info) -> bool:
    """True for a process belonging to Windows rather than to a person.

    Two signals, and the second is not a fallback so much as the one that
    carries the load:

    1. **The token**, where we can read it: a process running as SYSTEM,
       LOCAL SERVICE or NETWORK SERVICE. On the token and not the path, so a
       user-installed service running as SYSTEM lands here and a user's own
       program in System32 does not.
    2. **Session 0**, which needs no permission at all. Session 0 is the
       non-interactive session Windows reserves for services and system
       processes; nothing a person launched runs there.

    The second exists because the first is unavailable to an ordinary user
    for most of the machine. Measured here: unelevated, 135 of 284 processes
    refuse their token, and every system process is among them -- so on the
    token alone the "Windows processes" group came out EMPTY and all of it
    piled into Background. The session id arrives free with the bulk syscall
    and cannot be refused.

    A process that is neither still falls to Background, which is where Task
    Manager puts what it cannot attribute.
    """
    user = getattr(info.details, "user", None)
    if user:
        account = user.split("\\")[-1].strip().upper()
        if account in _SYSTEM_ACCOUNTS:
            return True
    return getattr(info.raw, "session", None) == 0


def group_processes(snapshot, windows: Optional[Dict[int, List[str]]] = None):
    """Split a snapshot into Apps, Background processes and Windows processes.

    `windows` is injectable so the split can be tested without a desktop.
    """
    if windows is None:
        windows = windowed_pids()

    apps: List[AppEntry] = []
    claimed: Set[int] = set()

    #: One entry per program, not per window. Two Explorer windows are two
    #: entries otherwise, each claiming to be "Windows Explorer".
    merged: Dict[str, AppEntry] = {}

    for pid, titles in sorted(windows.items()):
        info = snapshot.by_pid.get(pid)
        if info is None:
            # The window's process ended between the two reads.
            continue
        if pid in claimed:
            # Already absorbed as a sibling of an app seen earlier.
            continue
        members = set(_family(snapshot, pid)) - claimed
        claimed.update(members)

        key = (info.details.path or info.name).lower()
        entry = merged.get(key)
        if entry is None:
            entry = AppEntry(title=_app_title(info), pid=pid)
            merged[key] = entry
            apps.append(entry)
        entry.members.extend(snapshot.by_pid[member] for member in members
                             if member in snapshot.by_pid)
        entry.window_titles.extend(titles)

    background, system = [], []
    for pid, info in snapshot.by_pid.items():
        if pid in claimed:
            continue
        (system if is_windows_process(info) else background).append(info)

    return [
        Group(APPS, sorted(apps, key=lambda entry: entry.title.lower())),
        Group(BACKGROUND, sorted(background, key=_by_name)),
        Group(WINDOWS, sorted(system, key=_by_name)),
    ]


def _family(snapshot, pid: int) -> Set[int]:
    """A windowed process and the sibling processes of the SAME PROGRAM.

    Chrome is one app and twenty-six processes; listing them separately is
    what makes the Processes tab as unreadable as the Details tab.

    **Descent alone is the wrong rule, and the real machine says so loudly.**
    Anything launched from the shell inherits `explorer.exe` as its parent,
    so rolling up descendants made Explorer absorb 60 processes and 6.6 GB
    -- Steam, WhatsApp and Visual Studio filed under Windows Explorer. Task
    Manager groups by app identity, not by the process tree.

    So a member has to be BOTH a descendant and the same program, matched on
    the image path. Where the path was refused the name stands in: it is
    weaker, but two processes of the same name descending from one windowed
    process are the same app in every case that matters here.
    """
    from .snapshot import descendants_of

    rows = [info.raw for info in snapshot.by_pid.values()]
    try:
        candidates = descendants_of(pid, rows)
    except Exception:  # noqa: BLE001
        candidates = []

    head = snapshot.by_pid.get(pid)
    family = {pid}
    if head is None:
        return family
    for candidate in candidates:
        other = snapshot.by_pid.get(candidate)
        if other is not None and _same_program(head, other):
            family.add(candidate)
    return family


def _same_program(one, other) -> bool:
    """Whether two processes are the same program.

    On the image path where both are readable -- the only signal that
    survives two programs sharing a name. Otherwise on the executable name,
    which is what is left when the path was refused.
    """
    first, second = one.details.path, other.details.path
    if first and second:
        return first.lower() == second.lower()
    return one.name.lower() == other.name.lower()


def _app_title(info) -> str:
    """What to call the app: its description if it has one, else its name.

    "Google Chrome" beats "chrome.exe" for a list someone reads, and the
    description is where Windows itself keeps that name.
    """
    return getattr(info.details, "description", None) or info.name


def _by_name(info) -> str:
    return info.name.lower()


def totals(rows) -> Dict[str, float]:
    """Summed CPU, memory and disk across a group of processes.

    Unmeasured rates contribute nothing rather than zero -- summing `None` as
    0 would understate an app whose processes have only just appeared, which
    is exactly when someone is looking at it.
    """
    total = {"cpu": 0.0, "memory": 0, "disk": 0.0}
    for info in rows:
        if info.rates.cpu_percent is not None:
            total["cpu"] += info.rates.cpu_percent
        total["memory"] += info.raw.working_set_private
        for field_name in ("read_bps", "write_bps"):
            value = getattr(info.rates, field_name)
            if value is not None:
                total["disk"] += value
    return total
