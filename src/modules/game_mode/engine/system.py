"""Every Windows call Game Mode makes, behind one small class. Qt-free.

`session.py` decides; this only reads and writes. Tests drive `session`
with a fake that has the same methods, so none of the decisions need a
real service manager. Every method that can be refused says so -- an error
string or `None` for "could not read" -- and never answers a refusal with
a value.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.run import run

logger = logging.getLogger(__name__)

_GUID = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
                   r"\s+\((.+?)\)")

PRIORITY_NAMES = {0x40: "Idle", 0x4000: "Below Normal", 0x20: "Normal", 0x8000: "Above Normal",
                  0x80: "High", 0x100: "Realtime"}
BELOW_NORMAL = 0x4000
NORMAL = 0x20


@dataclass(frozen=True)
class ServiceState:
    #: None = the state could not be read (see `error`).
    running: Optional[bool]
    start_type: str = ""
    error: Optional[str] = None


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    name: str
    handles: int
    session: int
    create_time: int


def parse_plans(text: str) -> Dict[str, str]:
    """`powercfg /list` -> {guid: name}."""
    return {m.group(1).lower(): m.group(2) for m in _GUID.finditer(text)}


def split_command_line(cmdline: str) -> Tuple[str, str]:
    """A process command line -> (program, the arguments as written)."""
    text = (cmdline or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end], text[end + 1:].strip()
    program, _, rest = text.partition(" ")
    return program, rest.strip()


class WinSystem:
    """The real machine."""

    # ---- identity ------------------------------------------------------------------------------
    def is_admin(self) -> bool:
        from core.admin_utils import is_admin
        return bool(is_admin())

    def cpu_name(self) -> str:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError as error:
            logger.warning("game mode: cannot read the CPU name: %s", error)
            return ""

    # ---- power plans ---------------------------------------------------------------------------
    def plans(self) -> Dict[str, str]:
        res = run(["powercfg", "/list"], timeout=20)
        plans = parse_plans(res.stdout)
        if res.returncode != 0 or not plans:
            reason = (res.stderr or res.stdout).strip() or f"powercfg exited {res.returncode}"
            logger.warning("game mode: power plans unreadable: %s", reason)
            raise OSError(f"power plans unreadable: {reason}")
        return plans

    def active_plan(self) -> Optional[str]:
        found = parse_plans(run(["powercfg", "/getactivescheme"], timeout=20).stdout)
        return next(iter(found), None)

    def set_plan(self, guid: str) -> Optional[str]:
        res = run(["powercfg", "/setactive", guid], timeout=20)
        if res.returncode != 0:
            return (res.stderr or res.stdout).strip() or f"powercfg exited {res.returncode}"
        return None

    # ---- services ------------------------------------------------------------------------------
    def service(self, name: str) -> Optional[ServiceState]:
        """None = not installed on this machine."""
        import pywintypes
        import win32service
        try:
            scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        except pywintypes.error as error:
            return ServiceState(None, error=error.strerror)
        try:
            try:
                svc = win32service.OpenService(scm, name, win32service.SERVICE_QUERY_STATUS
                                               | win32service.SERVICE_QUERY_CONFIG)
            except pywintypes.error as error:
                if error.winerror == 1060:          # ERROR_SERVICE_DOES_NOT_EXIST
                    return None
                return ServiceState(None, error=error.strerror)
            try:
                status = win32service.QueryServiceStatusEx(svc)["CurrentState"]
                start = win32service.QueryServiceConfig(svc)[1]
            finally:
                win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)
        return ServiceState(status == win32service.SERVICE_RUNNING,
                            {2: "Automatic", 3: "Manual", 4: "Disabled"}.get(start, str(start)))

    def running_dependents(self, name: str) -> List[str]:
        import pywintypes
        import win32service
        try:
            scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            svc = win32service.OpenService(scm, name, win32service.SERVICE_ENUMERATE_DEPENDENTS)
            deps = win32service.EnumDependentServices(svc, win32service.SERVICE_ACTIVE)
            win32service.CloseServiceHandle(svc)
            win32service.CloseServiceHandle(scm)
            return [d[0] for d in deps]
        except pywintypes.error as error:
            logger.warning("game mode: dependents of %s unreadable: %s", name, error)
            return []

    def stop_service(self, name: str) -> Optional[str]:
        return self._control(name, stop=True)

    def start_service(self, name: str) -> Optional[str]:
        return self._control(name, stop=False)

    def _control(self, name: str, stop: bool, wait: float = 12.0) -> Optional[str]:
        import pywintypes
        import win32service
        want = win32service.SERVICE_STOPPED if stop else win32service.SERVICE_RUNNING
        access = (win32service.SERVICE_STOP if stop else win32service.SERVICE_START) \
            | win32service.SERVICE_QUERY_STATUS
        try:
            scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            svc = win32service.OpenService(scm, name, access)
        except pywintypes.error as error:
            return error.strerror
        try:
            try:
                if stop:
                    win32service.ControlService(svc, win32service.SERVICE_CONTROL_STOP)
                else:
                    win32service.StartService(svc, None)
            except pywintypes.error as error:
                if error.winerror not in (1056, 1062):  # already running / not started
                    return error.strerror
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if win32service.QueryServiceStatusEx(svc)["CurrentState"] == want:
                    return None
                time.sleep(0.25)
            return f"did not {'stop' if stop else 'start'} within {int(wait)} s"
        finally:
            win32service.CloseServiceHandle(svc)
            win32service.CloseServiceHandle(scm)

    # ---- processes -----------------------------------------------------------------------------
    def processes(self) -> List[Proc]:
        from core.procengine.ntquery import system_processes
        return [Proc(p.pid, p.ppid, p.name, p.handles, p.session, p.create_time)
                for p in system_processes() if p.pid]

    def details(self, pid: int) -> Tuple[Optional[str], Optional[str], Optional[bool]]:
        """(path, command line, elevated) -- None where refused."""
        from core.procengine.details import resolve
        d = resolve(pid)
        return d.path, d.cmdline, d.elevated

    def aumid(self, pid: int) -> Optional[str]:
        """A packaged app's AppUserModelID (how it is reopened), or None."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            length = wintypes.UINT(256)
            buf = ctypes.create_unicode_buffer(256)
            rc = kernel32.GetApplicationUserModelId(handle, ctypes.byref(length), buf)
            return buf.value if rc == 0 and buf.value else None
        finally:
            kernel32.CloseHandle(handle)

    def priority(self, pid: int) -> Optional[int]:
        import psutil
        try:
            return int(psutil.Process(pid).nice())
        except (psutil.Error, OSError) as error:
            logger.warning("game mode: priority of %s unreadable: %s", pid, error)
            return None

    def set_priority(self, pid: int, value: int) -> Optional[str]:
        import psutil
        try:
            psutil.Process(pid).nice(value)
            return None
        except (psutil.Error, OSError) as error:
            return str(error) or type(error).__name__

    def close(self, identities: List[Tuple[int, int]], exe_path: Optional[str],
              grace: float = 8.0) -> Optional[str]:
        """Close only the recorded process instances."""
        return self.close_many([(identities, exe_path)], grace)[0]

    def close_many(self, jobs: List[Tuple[List[Tuple[int, int]], Optional[str]]],
                   grace: float = 8.0) -> List[Optional[str]]:
        """Ask every recorded process to close together, then end matching survivors."""
        from . import shell_launch
        from . import catalog
        for identities, exe_path in jobs:
            if not self._matching(identities) or not exe_path:
                continue
            if self._trusted_onedrive(exe_path):
                error = shell_launch.launch(exe_path, "/shutdown")
                if error:
                    logger.warning("game mode: OneDrive shutdown failed: %s", error)
                continue
            # Programs that only hide on WM_CLOSE (Steam) are asked to quit by
            # their own command, unelevated, from the path they are running from.
            quit_args = catalog.QUIT_ARGS.get(os.path.basename(exe_path).lower())
            if quit_args:
                error = shell_launch.launch(exe_path, quit_args)
                if error:
                    logger.warning("game mode: %s %s failed: %s", exe_path, quit_args, error)
        identities = [identity for group, _path in jobs for identity in group]
        self._post_close(self._matching(identities))
        self._wait_gone(identities, grace)
        results = [self._end_left(group) for group, _path in jobs]
        self._wait_gone(identities, 5.0)
        return [err or ("still running after it was ended" if self._matching(group) else None)
                for err, (group, _path) in zip(results, jobs)]

    @staticmethod
    def _trusted_onedrive(path: Optional[str]) -> bool:
        if not path or os.path.basename(path).lower() != "onedrive.exe":
            return False
        real = os.path.normcase(os.path.realpath(path))
        for variable, folder in (("ProgramFiles", "Microsoft OneDrive"),
                                 ("LOCALAPPDATA", os.path.join("Microsoft", "OneDrive"))):
            base = os.environ.get(variable)
            if base:
                allowed = os.path.normcase(os.path.realpath(os.path.join(base, folder)))
                if real.startswith(allowed.rstrip(os.sep) + os.sep):
                    return True
        return False

    def _matching(self, identities: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        current = {(p.pid, p.create_time) for p in self.processes()}
        return [identity for identity in identities if identity in current]

    def _end_left(self, identities: List[Tuple[int, int]]) -> Optional[str]:
        import psutil
        for pid, created in identities:
            if not self.alive(pid, created):
                continue
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                logger.debug("game mode: %s closed politely or exited on its own", pid)
            except (psutil.Error, OSError) as error:
                return f"could not end process {pid}: {error}"
        return None

    def _post_close(self, identities: List[Tuple[int, int]]) -> None:
        import win32con
        import win32gui
        import win32process
        # One process snapshot for the whole enumeration: checking alive() per
        # window took a full NtQuerySystemInformation for each of hundreds of windows.
        live_pids = {pid for pid, _created in self._matching(identities)}

        import pywintypes

        def each(hwnd, _):
            # A window can close between EnumWindows listing it and the
            # PostMessage (error 1400, measured 2026-10-19). pywintypes.error is
            # NOT an OSError, so it escaped and aborted Game Mode ON halfway.
            try:
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                if pid in live_pids:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except (pywintypes.error, OSError) as error:
                logger.debug("game mode: WM_CLOSE to %s skipped: %s", hwnd, error)
            return True
        win32gui.EnumWindows(each, None)

    def _wait_gone(self, identities: List[Tuple[int, int]], seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._matching(identities):
                return True
            time.sleep(0.25)
        return not self._matching(identities)

    def fullscreen_pids(self) -> set:
        """The process owning the foreground window when that window covers
        its whole monitor -- a game already running, which nothing may close.
        The desktop itself (Progman/WorkerW) does not count."""
        import win32api
        import win32gui
        import win32process
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or win32gui.GetClassName(hwnd) in ("Progman", "WorkerW", "Shell_TrayWnd"):
                return set()
            rect = win32gui.GetWindowRect(hwnd)
            monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, 2))["Monitor"]
            if tuple(rect) != tuple(monitor):
                return set()
            return {win32process.GetWindowThreadProcessId(hwnd)[1]}
        except Exception as error:  # noqa: BLE001 -- pywin32 raises pywintypes.error
            logger.warning("game mode: foreground window unreadable: %s", error)
            return set()

    def wifi_connected(self) -> Optional[bool]:
        """Is a Wi-Fi adapter up? NdisPhysicalMedium 9 is Native 802.11 --
        language-independent, unlike adapter names. None = could not read."""
        from .optimise import powershell_exe     # absolute path: see its docstring
        try:
            exe = powershell_exe()
        except OSError as error:
            logger.warning("game mode: Wi-Fi state unreadable: %s", error)
            return None
        res = run([exe, "-NoProfile", "-Command",
                   "@(Get-NetAdapter -Physical -ErrorAction Stop | Where-Object "
                   "{ $_.NdisPhysicalMedium -eq 9 -and $_.Status -eq 'Up' }).Count"], timeout=30)
        text = (res.stdout or "").strip()
        if res.returncode != 0 or not text.isdigit():
            logger.warning("game mode: Wi-Fi state unreadable: %s", (res.stderr or text)[:200])
            return None
        return int(text) > 0

    def restart_explorer(self, session: int, wait: float = 15.0) -> Optional[str]:
        """End every explorer.exe in `session`; Windows restarts the shell
        (AutoRestartShell=1). If it has not within `wait`, start it as the
        signed-in user through a one-shot scheduled task (never elevated)."""
        old = self.end_explorer(session)
        if isinstance(old, str):
            return old
        return self.ensure_shell(old, session, wait)

    def end_explorer(self, session: int):
        """End every explorer.exe in `session` (never another user's). The
        set of pids ended, or the error text."""
        import psutil
        old = {p.pid for p in self.processes() if p.name.lower() == "explorer.exe" and p.session == session}
        for pid in old:
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                logger.debug("game mode: explorer %s already gone", pid)
            except (psutil.Error, OSError) as error:
                return f"could not end explorer {pid}: {error}"
        # TerminateProcess is asynchronous: wait until they are really gone,
        # or a caller deleting Explorer's caches races the dying process.
        procs = []
        for pid in old:
            try:
                procs.append(psutil.Process(pid))
            except psutil.NoSuchProcess:
                logger.debug("game mode: explorer %s already exited", pid)
        psutil.wait_procs(procs, timeout=5)
        return old

    def ensure_shell(self, old: set, session: int, wait: float = 15.0) -> Optional[str]:
        """Wait for Windows to restart the shell; start it unelevated if not."""
        if self._shell_back(old, session, wait):
            time.sleep(3.0)                      # let the taskbar and tray settle
            return None
        logger.warning("game mode: the shell did not restart by itself; starting it")
        task = "WinClientTool_GameMode_Explorer"
        run(["schtasks", "/create", "/tn", task, "/tr", "explorer.exe", "/sc", "once", "/st", "00:00",
             "/it", "/rl", "LIMITED", "/f"], timeout=30)
        run(["schtasks", "/run", "/tn", task], timeout=30)
        back = self._shell_back(old, session, wait)
        run(["schtasks", "/delete", "/tn", task, "/f"], timeout=30)
        return None if back else "the shell did not come back -- press Ctrl+Shift+Esc, File > Run, explorer"

    def _shell_back(self, old: set, session: int, wait: float) -> bool:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if any(p.name.lower() == "explorer.exe" and p.session == session and p.pid not in old
                   for p in self.processes()):
                return True
            time.sleep(0.5)
        return False

    def alive(self, pid: int, create_time: int) -> bool:
        return any(p.pid == pid and p.create_time == create_time for p in self.processes())

    def launch(self, program: str, args: str = "", cwd: str = "", quiet: bool = False) -> Optional[str]:
        """Start `program` as the signed-in user, NOT elevated, through the
        shell's own desktop window -- an elevated app's children would be
        elevated too, and OneDrive refuses to run elevated. `quiet`: minimized,
        without taking focus (it was minimized or in the tray before)."""
        from . import shell_launch
        show = shell_launch.SHOW_MIN_NO_ACTIVE if quiet else shell_launch.SHOW_NORMAL
        return shell_launch.launch(program, args, cwd, show=show)

    def launch_aumid(self, aumid: str, quiet: bool = False) -> Optional[str]:
        from . import shell_launch
        show = shell_launch.SHOW_MIN_NO_ACTIVE if quiet else shell_launch.SHOW_NORMAL
        return shell_launch.launch_packaged(aumid, show=show)

    @staticmethod
    def _main_windows(pids):
        """Visible, unowned top-level windows of `pids`: [(hwnd, pid)]."""
        import pywintypes
        import win32con
        import win32gui
        import win32process
        found = []

        def each(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                    return True
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                if pid in pids and win32gui.GetWindowText(hwnd):
                    found.append((hwnd, pid))
            except (pywintypes.error, OSError) as error:
                logger.debug("game mode: window %s skipped: %s", hwnd, error)
            return True
        win32gui.EnumWindows(each, None)
        return found

    def has_visible_window(self, pids) -> Optional[bool]:
        """Any visible top-level window, including untitled or owned windows.
        None means unreadable: a preloaded viewer must then be left alone."""
        import win32gui
        import win32process
        found = False
        wanted = set(pids)

        def each(hwnd, _):
            nonlocal found
            if (win32gui.IsWindowVisible(hwnd)
                    and win32process.GetWindowThreadProcessId(hwnd)[1] in wanted):
                found = True
            return True

        try:
            win32gui.EnumWindows(each, None)
        except Exception as error:  # noqa: BLE001 -- COM/window enumeration can fail
            logger.warning("game mode: visible windows unreadable: %s", error)
            return None
        return found

    def window_state(self, pids) -> str:
        """How the program looked before it was closed: "normal" (a window on
        screen), "minimized" (only minimized windows) or "hidden" (tray only)."""
        import win32gui
        try:
            windows = self._main_windows(set(pids))
        except Exception as error:  # noqa: BLE001 -- never block closing over this
            logger.warning("game mode: window state unreadable: %s", error)
            return "normal"
        if not windows:
            return "hidden"
        return "minimized" if all(win32gui.IsIconic(h) for h, _p in windows) else "normal"

    def keep_minimized(self, exes, seconds: float = 15.0) -> None:
        """For `seconds`, minimize -- without activating -- each new main window
        of a program in `exes`. Teams/WhatsApp ignore the show command they are
        launched with, so a quiet reopen alone still brings them to the front."""
        import pywintypes
        import win32con
        import win32gui
        names = {e.lower() for e in exes}
        if not names:
            return
        handled = set()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            pids = {p.pid for p in self.processes() if p.name.lower() in names}
            for hwnd, _pid in self._main_windows(pids):
                if hwnd in handled:
                    continue
                handled.add(hwnd)
                try:
                    if not win32gui.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOWMINNOACTIVE)
                except pywintypes.error as error:
                    logger.debug("game mode: could not minimize %s: %s", hwnd, error)
            time.sleep(0.5)

    # ---- registry ------------------------------------------------------------------------------
    def reg_get(self, path: str, name: str, hive: str = "HKCU") -> Tuple[Optional[bool], object, int]:
        """(exists, value, type); exists None = refused."""
        try:
            with winreg.OpenKey(_HIVES[hive], path) as key:
                value, kind = winreg.QueryValueEx(key, name)
                return True, value, kind
        except FileNotFoundError:
            return False, None, 0
        except OSError as error:
            logger.warning("game mode: %s\\%s\\%s unreadable: %s", hive, path, name, error)
            return None, None, 0

    def reg_set(self, path: str, name: str, value, kind: int, hive: str = "HKCU") -> Optional[str]:
        try:
            with winreg.CreateKeyEx(_HIVES[hive], path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, name, 0, kind, value)
            return None
        except OSError as error:
            return str(error)

    def reg_delete(self, path: str, name: str, hive: str = "HKCU") -> Optional[str]:
        try:
            with winreg.OpenKey(_HIVES[hive], path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            return None
        except FileNotFoundError:
            return None
        except OSError as error:
            return str(error)

    def broadcast_setting_change(self, area: str) -> None:
        """Tell running programs a setting changed (transparency takes effect
        at once). Bounded: a hung window costs at most a second."""
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        result = ctypes.c_size_t()
        user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, ctypes.c_wchar_p(area), 0x0002, 1000,
                                   ctypes.byref(result))

    # ---- window animations ---------------------------------------------------------------------
    def animations(self) -> Optional[bool]:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        on = wintypes.BOOL()
        if not user32.SystemParametersInfoW(_SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(on), 0):
            logger.warning("game mode: animation setting unreadable (%s)", ctypes.get_last_error())
            return None
        return bool(on.value)

    def set_animations(self, on: bool) -> Optional[str]:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if not user32.SystemParametersInfoW(_SPI_SETCLIENTAREAANIMATION, 0, ctypes.c_void_p(int(on)),
                                            _SPIF_UPDATEINIFILE | _SPIF_SENDCHANGE):
            return f"SystemParametersInfo refused ({ctypes.get_last_error()})"
        return None

    # ---- service names (patterns) --------------------------------------------------------------
    def service_names(self, pattern: str) -> List[str]:
        """Installed services matching `pattern` (fnmatch, any case)."""
        if "*" not in pattern:
            return [pattern] if self.service(pattern) is not None else []
        import fnmatch
        import pywintypes
        import win32service
        try:
            scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
            try:
                found = win32service.EnumServicesStatusEx(scm, 0x3FF, win32service.SERVICE_STATE_ALL)
            finally:
                win32service.CloseServiceHandle(scm)
        except pywintypes.error as error:
            logger.warning("game mode: services could not be listed: %s", error)
            return []
        low = pattern.lower()
        return sorted(s["ServiceName"] for s in found if fnmatch.fnmatch(s["ServiceName"].lower(), low))

    # ---- scheduled tasks -----------------------------------------------------------------------
    def tasks(self) -> List[Tuple[str, bool]]:
        """Every scheduled task as (path, enabled), hidden ones included."""
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        out: List[Tuple[str, bool]] = []
        stack = [service.GetFolder("\\")]
        while stack:
            folder = stack.pop()
            out.extend((t.Path, bool(t.Enabled)) for t in folder.GetTasks(1))
            stack.extend(folder.GetFolders(0))
        return out

    def task_enabled(self, path: str) -> Optional[bool]:
        task = self._task(path)
        return None if task is None else bool(task.Enabled)

    def task_running(self, path: str) -> Optional[bool]:
        try:
            task = self._task(path)
            return None if task is None else task.State == 4  # TASK_STATE_RUNNING
        except Exception as error:  # noqa: BLE001 -- Task Scheduler COM refusals
            logger.warning("game mode: task %s state unreadable: %s", path, error)
            return None

    def stop_task(self, path: str) -> Optional[str]:
        """Stop a current run; enabling the task again restores its triggers."""
        try:
            task = self._task(path)
            if task is None:
                return "task not found"
            task.Stop(0)
            deadline = time.monotonic() + 3.0
            while task.State == 4:  # Stop can return S_FALSE while the instance keeps running.
                if time.monotonic() >= deadline:
                    return "the current run is still running"
                time.sleep(0.1)
        except Exception as error:  # noqa: BLE001 -- Task Scheduler COM refusals
            return f"refused ({getattr(error, 'excepinfo', None) or error})"
        return None

    def set_task_enabled(self, path: str, enabled: bool) -> Optional[str]:
        """Flip IRegisteredTask.Enabled -- never re-register, which rewrites
        the principal and breaks SYSTEM/password tasks."""
        try:
            task = self._task(path)
            if task is None:
                return "task not found"
            task.Enabled = enabled
            current = self._task(path)
            return None if current is not None and bool(current.Enabled) == enabled else "read-back differs"
        except Exception as error:  # noqa: BLE001 -- COM refusals arrive as com_error
            return f"refused ({getattr(error, 'excepinfo', None) or error})"

    @staticmethod
    def _task(path: str):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        folder, _, name = path.rpartition("\\")
        try:
            return service.GetFolder(folder or "\\").GetTask(name)
        except Exception as error:  # noqa: BLE001
            logger.warning("game mode: task %s not readable: %s", path, error)
            return None


_HIVES = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
_SPI_GETCLIENTAREAANIMATION, _SPI_SETCLIENTAREAANIMATION = 0x1042, 0x1043
_SPIF_UPDATEINIFILE, _SPIF_SENDCHANGE = 0x01, 0x02
