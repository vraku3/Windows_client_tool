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
- A packaged app is opened as `explorer.exe shell:AppsFolder\\<AUMID>` --
  the shell starts a new explorer for it and returns at once (0.09 s).
"""
from __future__ import annotations

import logging
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


def _call(program: str, args: str, cwd: str, out: List[Optional[str]]) -> None:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        app = _desktop_shell()
        app.ShellExecute(program, args, cwd, "", 1)
        app = None
        out.append(None)
    except Exception as error:  # noqa: BLE001 -- COM raises several unrelated types
        logger.warning("game mode: unelevated launch of %s failed: %s", program, error)
        out.append(f"the shell refused to start it ({error})")
    finally:
        pythoncom.CoUninitialize()


def launch(program: str, args: str = "", cwd: str = "", timeout: float = TIMEOUT_S) -> Optional[str]:
    """None on success, else why it could not be started."""
    out: List[Optional[str]] = []
    worker = threading.Thread(target=_call, args=(program, args, cwd, out), daemon=True,
                              name="game-mode-launch")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        logger.warning("game mode: the shell did not return from starting %s", program)
        return (f"the shell did not return within {int(timeout)} s -- Windows may be showing a "
                "message about it")
    return out[0] if out else "the launch ended without a result"


def launch_packaged(aumid: str, timeout: float = TIMEOUT_S) -> Optional[str]:
    return launch("explorer.exe", "shell:AppsFolder\\" + aumid, "", timeout)
