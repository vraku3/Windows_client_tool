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
        return parse_plans(run(["powercfg", "/list"], timeout=20).stdout)

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

    def _control(self, name: str, stop: bool, wait: float = 25.0) -> Optional[str]:
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

    def close(self, pids: List[int], exe_path: Optional[str], grace: float = 8.0) -> Optional[str]:
        """Ask politely, then end what is left. None when every pid is gone."""
        if exe_path and os.path.basename(exe_path).lower() == "onedrive.exe":
            run([exe_path, "/shutdown"], timeout=20)       # OneDrive's own documented exit
        self._post_close(set(pids))
        if self._wait_gone(pids, grace):
            return None
        import psutil
        for pid in pids:
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                logger.debug("game mode: %s exited on its own before it was ended", pid)
                continue
            except (psutil.Error, OSError) as error:
                return f"could not end process {pid}: {error}"
        return None if self._wait_gone(pids, 5.0) else "still running after it was ended"

    @staticmethod
    def _post_close(pids) -> None:
        import win32con
        import win32gui
        import win32process

        def each(hwnd, _):
            if win32process.GetWindowThreadProcessId(hwnd)[1] in pids:
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                except OSError as error:
                    logger.warning("game mode: WM_CLOSE to %s refused: %s", hwnd, error)
            return True
        win32gui.EnumWindows(each, None)

    @staticmethod
    def _wait_gone(pids: List[int], seconds: float) -> bool:
        import psutil
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not any(psutil.pid_exists(p) for p in pids):
                return True
            time.sleep(0.25)
        return not any(psutil.pid_exists(p) for p in pids)

    def alive(self, pid: int, create_time: int) -> bool:
        return any(p.pid == pid and p.create_time == create_time for p in self.processes())

    def launch(self, program: str, args: str = "", cwd: str = "") -> Optional[str]:
        """Start `program` as the signed-in user, NOT elevated, through the
        shell's own desktop window -- an elevated app's children would be
        elevated too, and OneDrive refuses to run elevated."""
        from . import shell_launch
        return shell_launch.launch(program, args, cwd)

    def launch_aumid(self, aumid: str) -> Optional[str]:
        from . import shell_launch
        return shell_launch.launch_packaged(aumid)

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

    def set_task_enabled(self, path: str, enabled: bool) -> Optional[str]:
        """Flip IRegisteredTask.Enabled -- never re-register, which rewrites
        the principal and breaks SYSTEM/password tasks."""
        task = self._task(path)
        if task is None:
            return "task not found"
        try:
            task.Enabled = enabled
        except Exception as error:  # noqa: BLE001 -- COM refusals arrive as com_error
            return f"refused ({getattr(error, 'excepinfo', None) or error})"
        return None if bool(self._task(path).Enabled) == enabled else "read-back differs"

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
