"""Start a program as the signed-in user, never elevated. Qt-free.

An elevated process's children inherit its token, so this app (usually run
as administrator) cannot simply start OneDrive or WhatsApp again -- they
would come back elevated, and OneDrive refuses to run that way. The shell
(explorer.exe) runs unelevated, so the launch is handed to IT: find the
desktop's folder view through ShellWindows, take its `Application`
(IShellDispatch2) and call ShellExecute there. Verified here 2026-10-16:
Notepad started this way from an elevated Python came up Medium integrity,
not elevated.

Two things measured the hard way, the same day:
- **That ShellExecute runs INSIDE explorer and waits for it.** Given a
  target that does not exist (`shell:AppsFolder\\<an uninstalled app>`),
  explorer shows a modal "not found" box and the call does not return until
  someone dismisses it. So every launch runs on its own thread with a
  timeout: a launch can fail, it can never hang Game Mode OFF.
- A packaged app is opened as the `shell:AppsFolder\\<AUMID>` item, after
  checking the package is still installed (see `launch_packaged`).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

_SHELL_WINDOWS = "{9BA05972-F6A8-11CF-A442-00A0C90A8F39}"
_SWC_DESKTOP = 8
_SWFO_NEEDDISPATCH = 1
TIMEOUT_S = 15.0


def _desktop_shell():
    import pythoncom
    import win32com.client
    from win32com.shell import shell, shellcon
    windows = win32com.client.Dispatch(_SHELL_WINDOWS)
    found = windows.FindWindowSW(shellcon.CSIDL_DESKTOP, None, _SWC_DESKTOP, 0, _SWFO_NEEDDISPATCH)
    desktop = found[0] if isinstance(found, tuple) else found
    provider = desktop._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
    browser = provider.QueryService(shell.SID_STopLevelBrowser, shell.IID_IShellBrowser)
    view = browser.QueryActiveShellView()
    folder_view = view.GetItemObject(shellcon.SVGIO_BACKGROUND, pythoncom.IID_IDispatch)
    return win32com.client.Dispatch(folder_view).Application


#: ShellExecute show commands: normal, and minimized without taking focus.
SHOW_NORMAL = 1
SHOW_MIN_NO_ACTIVE = 7


def desktop_account() -> Optional[str]:
    """Account owning the desktop shell which receives unelevated launches."""
    try:
        import ctypes
        from ctypes import wintypes
        import psutil
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetShellWindow.restype = wintypes.HWND
        user32.GetShellWindow.argtypes = []
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        pid = wintypes.DWORD()
        hwnd = user32.GetShellWindow()
        if not hwnd or not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            return None
        return psutil.Process(pid.value).username()
    except (OSError, psutil.Error) as error:
        logger.warning("Could not read desktop shell account: %s", error)
        return None


def _call(program: str, args: str, cwd: str, out: List[Optional[str]],
          show: int = SHOW_NORMAL) -> None:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        app = _desktop_shell()
        app.ShellExecute(program, args, cwd, "", show)
        app = None
        out.append(None)
    except Exception as error:  # noqa: BLE001 -- COM raises several unrelated types
        logger.warning("game mode: unelevated launch of %s failed: %s", program, error)
        out.append(f"the shell refused to start it ({error})")
    finally:
        pythoncom.CoUninitialize()


def launch(program: str, args: str = "", cwd: str = "", timeout: float = TIMEOUT_S,
           show: int = SHOW_NORMAL) -> Optional[str]:
    """None on success, else why it could not be started."""
    out: List[Optional[str]] = []
    worker = threading.Thread(target=_call, args=(program, args, cwd, out, show), daemon=True,
                              name="game-mode-launch")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        logger.warning("game mode: the shell did not return from starting %s", program)
        return (f"the shell did not return within {int(timeout)} s -- Windows may be showing a "
                "message about it")
    return out[0] if out else "the launch ended without a result"


def package_installed(aumid: str) -> bool:
    """Every installed package has its data folder under %LOCALAPPDATA%\\Packages
    named by its family -- the part of the AUMID before '!'."""
    family = aumid.split("!", 1)[0]
    base = os.environ.get("LOCALAPPDATA", "")
    return bool(family and base and os.path.isdir(os.path.join(base, "Packages", family)))


def launch_packaged(aumid: str, timeout: float = TIMEOUT_S, show: int = SHOW_NORMAL) -> Optional[str]:
    """Checked first, because asking the shell for a package that is not there
    is exactly what raises explorer's modal box. Then the AppsFolder item is
    opened directly by the desktop shell (0.06 s, measured) -- not through
    `explorer.exe <item>`, which leaves an extra explorer process running."""
    if not package_installed(aumid):
        return f"{aumid.split('!', 1)[0]} is not installed any more"
    return launch("shell:AppsFolder\\" + aumid, "", "", timeout, show)
