"""One scan: every kind of app, recommended, with what failed said out loud.

Qt-free. The phases are separate so the list can appear before the slow
facts (storage, winget) are in -- the manual's "apps appear as they are
found; Storage fills in a few seconds later".
"""
from __future__ import annotations

import logging
import os
import winreg
from typing import Callable, List, Optional, Tuple

from . import desktop_apps, model as m, orphans, recommend, windows_apps

logger = logging.getLogger(__name__)


class ScanResult:
    def __init__(self) -> None:
        self.rows: List[m.AppRecord] = []
        self.problems: List[str] = []       # sources that could not be read, with why
        self.store: Optional[windows_apps.StoreView] = None


def scan(progress: Optional[Callable[[int, str], None]] = None) -> ScanResult:
    """Every app. A source that cannot be read is a problem in the result,
    and its rows are missing -- never an empty source passed off as fine."""
    out = ScanResult()
    say = progress or (lambda _n, _t: None)
    try:
        windows, store = windows_apps.read_windows_apps()
        out.rows += windows
        out.store = store
        if not store.readable:
            out.problems.append("The all-users app store could not be read, so installable "
                                "Windows apps are not listed.")
    except windows_apps.ReadRefused as e:
        out.problems.append(f"Windows apps could not be read: {e}")
        store = windows_apps.StoreView({}, set(), {}, {}, readable=False)
    say(len(out.rows), "Windows apps")
    try:
        out.rows += desktop_apps.read_desktop_apps()
    except OSError as e:
        out.problems.append(f"Desktop apps could not be read: {e}")
    say(len(out.rows), "desktop apps")
    installed = windows_apps.families_installed(out.rows)
    if out.store is not None:
        out.rows += orphans.find_orphaned_data(installed, store)
    recommend.apply(out.rows)
    say(len(out.rows), "done")
    return out


def _count(hive, path: str) -> int:
    try:
        with winreg.OpenKey(hive, path) as key:
            return winreg.QueryInfoKey(key)[0] * 1_000_003 + winreg.QueryInfoKey(key)[2] % 1_000_003
    except OSError:
        return -1


def fingerprint() -> Tuple[int, ...]:
    """A cheap answer to "did anything get installed or removed?" -- subkey
    counts and last-write times of the places installs register, plus the
    package data folder count. ~1 ms; a rescan is ~5 s."""
    store = windows_apps.STORE_KEY
    sid = _current_sid()
    parts = [
        _count(winreg.HKEY_LOCAL_MACHINE, store + r"\Applications"),
        _count(winreg.HKEY_LOCAL_MACHINE, store + "\\" + sid) if sid else 0,
        _count(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        _count(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        _count(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    try:
        parts.append(len(os.listdir(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages"))))
    except OSError:
        parts.append(-1)
    return tuple(parts)


def _current_sid() -> str:
    try:
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return win32security.ConvertSidToStringSid(sid)
    except Exception as e:                  # pywin32 missing or token refused
        logger.debug("could not read the current SID: %s", e)
        return ""
