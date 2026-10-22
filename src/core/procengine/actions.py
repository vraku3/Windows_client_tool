r"""Acting on a process, and reporting honestly whether it worked.

**Every action is verified, never assumed.** That is the rule the Apps tab
already paid for -- `Remove-AppxPackage` and `winget uninstall` both exit 0
while removing nothing -- and it applies just as hard here.
`psutil.Process(pid).kill()` returns without raising well before the process
is gone, and against a protected process it can return without raising and
the process never goes at all. Reporting that as success is how a process
manager tells you it killed something that is still running.

So `end_process` waits for the process to actually disappear, and says so if
it did not.

Every function answers with a `Result`, never a bare boolean: a failure that
cannot explain itself is not much better than a silent one, and these
messages go straight into a dialog someone has to act on.

Qt-free, like the rest of the engine.
"""
import ctypes
import logging
import math
import os
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import List

import psutil

logger = logging.getLogger(__name__)

PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

#: How long to wait for a killed process to actually leave the table. Killing
#: is asynchronous: the kernel tears the process down after the call returns.
KILL_TIMEOUT = 5.0

#: Processes that exist but can never be ended, whatever the permissions.
#: Attempting it produces a confusing refusal rather than an honest one.
UNKILLABLE = {0: "the System Idle Process", 4: "the System process"}

#: A ShellExecuteW return of 32 or below is an error code (31 = no app
#: association, 5 = access denied, 1223 = the user cancelled the UAC prompt).
SE_ERR_MAX = 32

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# Prototypes are declared for the same reason as in `details.py`: ctypes
# assumes a function returns c_int, so an undeclared HANDLE return is
# TRUNCATED TO 32 BITS on x64. The version of this code being replaced calls
# `ctypes.windll.kernel32.OpenProcess` with no restype and has that bug.
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                  wintypes.DWORD]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_ntdll.NtSuspendProcess.restype = ctypes.c_long
_ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
_ntdll.NtResumeProcess.restype = ctypes.c_long
_ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
_shell32.ShellExecuteW.restype = wintypes.HINSTANCE
_shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, ctypes.c_int]

if sys.platform == "win32":
    PRIORITY_LEVELS = {
        "idle": psutil.IDLE_PRIORITY_CLASS,
        "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
        "normal": psutil.NORMAL_PRIORITY_CLASS,
        "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
        "high": psutil.HIGH_PRIORITY_CLASS,
        "realtime": psutil.REALTIME_PRIORITY_CLASS,
    }
else:  # pragma: no cover - this app is Windows-only
    PRIORITY_LEVELS = {}

#: What each level is called in a menu.
PRIORITY_LABELS = [
    ("realtime", "Realtime"),
    ("high", "High"),
    ("above_normal", "Above normal"),
    ("normal", "Normal"),
    ("below_normal", "Below normal"),
    ("idle", "Low"),
]


@dataclass(frozen=True)
class Result:
    """Whether it worked, and what to tell the person if it did not."""

    ok: bool
    message: str = ""

    def __bool__(self) -> bool:
        return self.ok


def is_running(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        # A zombie is not running: on Windows this is a process whose handle
        # is still open somewhere but which has exited.
        return process.status() != psutil.STATUS_DEAD
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return _exists(pid)


def _exists(pid: int) -> bool:
    """A last resort for a process we cannot query but may still exist."""
    return psutil.pid_exists(pid)


def creation_seconds(ticks: int):
    """Snapshot FILETIME to psutil's Unix seconds, without guessing missing times."""
    if not ticks or ticks < 116444736000000000:
        return None
    return (ticks - 116444736000000000) / 10_000_000


def _checked_process(pid, create_time):
    """Open and validate the selected incarnation before any side effect."""
    try:
        process = psutil.Process(pid)
        if create_time is not None:
            actual = process.create_time()
            if (not math.isfinite(create_time) or create_time <= 0
                    or abs(actual - create_time) > 0.01):
                return None, Result(False, f"process {pid} has exited; a different process now has this PID")
        return process, None
    except psutil.NoSuchProcess:
        return None, Result(False, f"Process {pid} is no longer running.")
    except psutil.AccessDenied:
        return None, Result(False, "Access denied while checking process identity — try running as administrator.")


def _handle_identity_error(handle, pid, create_time):
    """Close the PID-reuse gap between psutil's check and OpenProcess."""
    if create_time is None:
        return None
    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not _kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                     ctypes.byref(kernel), ctypes.byref(user)):
        return Result(False, f"Could not verify process {pid}: {_reason(ctypes.get_last_error())}")
    actual = creation_seconds((created.dwHighDateTime << 32) | created.dwLowDateTime)
    if actual is None or abs(actual - create_time) > 0.01:
        return Result(False, f"process {pid} has exited; a different process now has this PID")
    return None


@dataclass(frozen=True)
class RestartContext:
    exe: str
    command_line: str
    elevated: bool
    account: str


def prepare_restart(pid, create_time=None):
    """Read the launch account and elevation before asking or ending anything."""
    process, error = _checked_process(pid, create_time)
    if error is not None:
        return None, error
    from .details import resolve
    facts = resolve(pid)
    if facts.elevated is None:
        return None, Result(False, "Could not read process elevation; Restart refused.")
    exe, command_line = _launch_facts(process)
    if not exe:
        return None, Result(False, f"Could not read the executable path of process {pid} to restart it.")
    if facts.elevated:
        # ShellExecute runas uses the caller's interactive account/UAC credentials.
        try:
            account = psutil.Process(os.getpid()).username()
        except psutil.Error as error:
            return None, Result(False, f"Could not read the relaunch account; Restart refused: {error}")
    else:
        # The desktop shell owns the unelevated token, which may be another account
        # when this app was started with alternate administrator credentials.
        from core.shell_launch import desktop_account
        account = desktop_account()
        if not account:
            return None, Result(False, "Could not read the desktop shell account; Restart refused.")
    return RestartContext(exe, command_line, facts.elevated, account), None


def end_process(pid: int, timeout: float = KILL_TIMEOUT, *, create_time=None) -> Result:
    """Kill `pid` and wait for it to be gone.

    The wait is the point. Without it this returns success the instant the
    kill is *requested*, which for a protected or hung process is a lie.
    """
    if pid in UNKILLABLE:
        return Result(False, f"{UNKILLABLE[pid].capitalize()} cannot be ended.")
    process, error = _checked_process(pid, create_time)
    if error is not None:
        return error

    try:
        process.kill()
    except psutil.NoSuchProcess:
        return Result(False, f"Process {pid} is no longer running.")
    except psutil.AccessDenied:
        return Result(False, "Access denied — try running as administrator.")
    except Exception as error:  # noqa: BLE001 - the reason is the product
        return Result(False, str(error))

    try:
        process.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        return Result(
            False,
            f"Process {pid} did not exit within {timeout:.0f} seconds.")
    except psutil.NoSuchProcess:
        logger.warning("end_process: the process could not be ended", exc_info=True)
        pass

    if is_running(pid):
        return Result(False, f"Process {pid} is still running.")
    return Result(True, "")


def end_process_tree(pid: int, timeout: float = KILL_TIMEOUT, *, create_time=None,
                     members=None) -> Result:
    """Kill `pid` and every descendant.

    Children first, so the parent cannot spawn replacements while its
    offspring are being killed. The list is taken ONCE up front: walking it
    live races against the very processes being ended.

    **A member that is already gone counts as ended.** Killing a child often
    takes its parent with it -- a launcher or shim exits when the process it
    wrapped dies, which is exactly what `.venv/Scripts/python.exe` does -- so
    by the time the root's turn comes it may already have left. The goal is
    "none of this tree is running", and that goal is met. Reporting it as a
    failure told the user a kill had failed when everything was dead.
    """
    if pid in UNKILLABLE:
        return Result(False, f"{UNKILLABLE[pid].capitalize()} cannot be ended.")
    process, error = _checked_process(pid, create_time)
    if error is not None:
        return error
    if not is_running(pid):
        return Result(False, f"Process {pid} is no longer running.")
    try:
        identity = process.create_time() if create_time is None else create_time
    except psutil.Error as error:
        return Result(False, f"Could not verify process {pid}: {error}")

    from .snapshot import descendants_of, system_processes

    try:
        if members is None:
            rows = system_processes()
            root = next((row for row in rows if row.pid == pid), None)
            root_time = creation_seconds(root.create_time) if root is not None else None
            if root_time is None or abs(root_time - identity) > 0.01:
                return Result(False, f"process {pid} has exited; a different process now has this PID")
            descendants = descendants_of(pid, rows)
            wanted = set(descendants + [pid])
            members = {row.pid: creation_seconds(row.create_time) for row in rows
                       if row.pid in wanted}
        else:
            descendants = [member for member in members if member != pid]
    except OSError as error:
        return Result(False, f"Could not read the process tree: {error}")

    failures: List[str] = []
    for child_pid in descendants:
        child_identity = members.get(child_pid)
        result = (end_process(child_pid, timeout=timeout, create_time=child_identity)
                  if child_identity is not None else Result(False, "Could not read process identity."))
        if not result.ok and is_running(child_pid):
            failures.append(f"PID {child_pid}: {result.message}")

    if is_running(pid):
        result = (end_process(pid, timeout=timeout, create_time=identity)
                  if identity is not None else Result(False, "Could not read process identity."))
        if not result.ok and is_running(pid):
            failures.append(f"PID {pid}: {result.message}")

    if failures:
        # Partial success is not success. Naming the survivors is what lets
        # someone do something about them.
        return Result(False, "; ".join(failures))
    return Result(True, "")


def suspend_process(pid: int, *, create_time=None) -> Result:
    return _suspend_or_resume(pid, _ntdll.NtSuspendProcess, "suspend", create_time)


def resume_process(pid: int, *, create_time=None) -> Result:
    return _suspend_or_resume(pid, _ntdll.NtResumeProcess, "resume", create_time)


def _suspend_or_resume(pid: int, call, verb: str, create_time=None) -> Result:
    process, error = _checked_process(pid, create_time)
    if error is not None:
        return error
    access = PROCESS_SUSPEND_RESUME | (0x1000 if create_time is not None else 0)
    handle = _kernel32.OpenProcess(access, False, pid)
    if not handle:
        return Result(False, f"Could not open process {pid} to {verb} it: "
                             f"{_reason(ctypes.get_last_error())}")
    try:
        error = _handle_identity_error(handle, pid, create_time)
        if error is not None:
            return error
        status = call(handle)
    finally:
        _kernel32.CloseHandle(handle)
    if status != 0:
        return Result(False,
                      f"Could not {verb} process {pid} "
                      f"(status 0x{status & 0xFFFFFFFF:08X}).")
    return Result(True, "")


def set_priority(pid: int, level: str, *, create_time=None) -> Result:
    if level not in PRIORITY_LEVELS:
        return Result(False, f"Unknown priority '{level}'.")
    try:
        process, error = _checked_process(pid, create_time)
        if error is not None:
            return error
        process.nice(PRIORITY_LEVELS[level])
        return Result(True, "")
    except psutil.NoSuchProcess:
        return Result(False, f"Process {pid} is no longer running.")
    except psutil.AccessDenied:
        return Result(False, "Access denied — try running as administrator.")
    except Exception as error:  # noqa: BLE001
        return Result(False, str(error))


def set_affinity(pid: int, cores: List[int], *, create_time=None) -> Result:
    if not cores:
        # A process pinned to no cores would never be scheduled again.
        return Result(False, "Choose at least one processor core.")
    available = set(range(os.cpu_count() or 1))
    unknown = [core for core in cores if core not in available]
    if unknown:
        return Result(False,
                      f"This machine has no core {', '.join(map(str, unknown))}.")
    try:
        process, error = _checked_process(pid, create_time)
        if error is not None:
            return error
        process.cpu_affinity(list(cores))
        return Result(True, "")
    except psutil.NoSuchProcess:
        return Result(False, f"Process {pid} is no longer running.")
    except psutil.AccessDenied:
        return Result(False, "Access denied — try running as administrator.")
    except Exception as error:  # noqa: BLE001
        return Result(False, str(error))


def create_dump(pid: int, path: str, full: bool = True, *, create_time=None) -> Result:
    """Write a minidump of `pid` to `path`.

    Verified by reading the file back: `MiniDumpWriteDump` can return
    success having written a truncated file when the target dies mid-write.
    """
    process, error = _checked_process(pid, create_time)
    if error is not None:
        return error
    handle = _kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return Result(False, f"Could not open process {pid}: "
                             f"{_reason(ctypes.get_last_error())}")
    try:
        error = _handle_identity_error(handle, pid, create_time)
        if error is not None:
            return error
        return _write_dump(handle, pid, path, full)
    finally:
        _kernel32.CloseHandle(handle)


def _write_dump(handle, pid: int, path: str, full: bool) -> Result:
    try:
        import win32con
        import win32file
    except ImportError:  # pragma: no cover
        return Result(False, "pywin32 is not available.")

    dbghelp = ctypes.WinDLL("dbghelp")
    dbghelp.MiniDumpWriteDump.restype = wintypes.BOOL
    dbghelp.MiniDumpWriteDump.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

    # MiniDumpWithFullMemory is what makes the dump useful in a debugger;
    # MiniDumpNormal produces a file that cannot show you a variable.
    dump_type = 0x00000002 if full else 0x00000000

    try:
        target = win32file.CreateFile(
            path, win32con.GENERIC_WRITE, 0, None, win32con.CREATE_ALWAYS,
            win32con.FILE_ATTRIBUTE_NORMAL, None)
    except Exception as error:  # noqa: BLE001
        return Result(False, f"Could not create {path}: {error}")

    try:
        ok = dbghelp.MiniDumpWriteDump(
            handle, pid, int(target.handle), dump_type, None, None, None)
        if not ok:
            return Result(False, f"Could not write the dump: "
                                 f"{_reason(ctypes.get_last_error())}")
    finally:
        target.Close()

    return _verify_dump(path)


def _verify_dump(path: str) -> Result:
    """A dump that is not a dump is a failure, whatever the API said."""
    try:
        with open(path, "rb") as handle:
            signature = handle.read(4)
        size = os.path.getsize(path)
    except OSError as error:
        return Result(False, f"The dump could not be read back: {error}")
    if signature != b"MDMP":
        return Result(False, "The file written is not a minidump.")
    if size < 4096:
        return Result(False, f"The dump is only {size} bytes; it is truncated.")
    return Result(True, "")


def restart_process(pid: int, timeout: float = KILL_TIMEOUT, *, create_time=None,
                    context=None) -> Result:
    """End `pid` and start it again the same way it was running.

    "Restart" only means what it can honestly deliver: the old process is
    ended and verified gone, and its executable is launched again with the
    same command line. Whether the new instance runs AS the same account
    or with the same window state is Windows' decision, not ours to claim
    -- a service or a protected process refuses here with a reason rather
    than pretending.

    The relaunch is verified only as far as ShellExecute answers: a code
    at or below 32 is an error (5 = access denied, 31 = no association),
    anything above it means the launch was accepted. Whether the new
    process survives is not something a single call can know, so this
    does not claim it.
    """
    if pid in UNKILLABLE:
        return Result(False, f"{UNKILLABLE[pid].capitalize()} cannot be "
                             f"restarted.")
    process, error = _checked_process(pid, create_time)
    if error is not None:
        return error
    try:
        identity = process.create_time() if create_time is None else create_time
    except psutil.Error as error:
        return Result(False, f"Could not verify process {pid}: {error}")
    current, error = prepare_restart(pid, identity)
    if error is not None:
        return error
    if context is not None and context != current:
        return Result(False, "The relaunch account, elevation or command changed; confirm Restart again.")
    ended = end_process(pid, timeout=timeout, create_time=identity)
    if not ended.ok:
        return Result(False, f"Could not end process {pid}: {ended.message}")

    if not current.elevated:
        from core.shell_launch import launch
        failure = launch(current.exe, current.command_line)
        return Result(failure is None, failure or "")
    return _launch(current.exe, current.command_line, runas=True)


def run_as(pid: int, *, create_time=None) -> Result:
    """Start the process's executable elevated, leaving `pid` alone.

    "Run as administrator": a new, elevated instance of the same program.
    The original process keeps running -- that is the point of the
    distinction from restart. The launch goes through ShellExecute's
    `runas` verb, so Windows itself raises the UAC prompt; a code of 1223
    from ShellExecute means the user declined the prompt, which is their
    answer, not a failure we should relabel.
    """
    if pid in UNKILLABLE:
        return Result(False, f"{UNKILLABLE[pid].capitalize()} cannot be "
                             f"run elevated.")
    process, error = _checked_process(pid, create_time)
    if error is not None:
        return error

    exe, command_line = _launch_facts(process)
    if not exe:
        return Result(False, f"Could not read the executable path of "
                             f"process {pid} to run it elevated.")

    return _launch(exe, command_line, runas=True)


def _launch_facts(process):
    """`(exe, command_line)` reproducing how `process` was started.

    The command line is rebuilt from psutil's parsed argv via
    `list2cmdline`, which restores the quoting Windows needs -- the naive
    " ".join() of admin_utils.py is exactly what breaks paths with spaces.
    """
    try:
        exe = process.exe()
    except (psutil.AccessDenied, psutil.NoSuchProcess) as error:
        logger.debug("Could not read exe for restart/run-as: %s", error)
        return None, ""
    try:
        parts = process.cmdline()
    except (psutil.AccessDenied, psutil.NoSuchProcess) as error:
        logger.debug("Could not read command line for restart/run-as: %s",
                     error)
        parts = []
    if not parts:
        return exe, ""
    # cmdline() is argv INCLUDING the program itself; ShellExecute takes
    # the file separately and wants only the arguments.
    if os.path.normcase(parts[0]) == os.path.normcase(exe):
        parts = parts[1:]
    return exe, subprocess.list2cmdline(parts)


def _launch(exe: str, command_line: str, runas: bool) -> Result:
    """ShellExecuteW, `open` or `runas`, and translate the verdict."""
    verb = "runas" if runas else None
    result = _shell32.ShellExecuteW(None, verb, exe, command_line or None,
                                    None, 1)
    code = int(result)
    if code > SE_ERR_MAX:
        return Result(True, "")
    if code == 1223:
        return Result(False, "The elevation prompt was cancelled.")
    if code == 5:
        return Result(False, "Access is denied — try running as "
                             "administrator.")
    text = ctypes.WinError(code).strerror if code else "unknown error"
    return Result(False, f"The launch failed: {text or f'error {code}'}.")


def _reason(code: int) -> str:
    if not code:
        return "unknown error"
    try:
        return ctypes.WinError(code).strerror or f"error {code}"
    except Exception:  # noqa: BLE001
        return f"error {code}"
