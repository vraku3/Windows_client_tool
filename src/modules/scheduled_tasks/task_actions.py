"""Changes to scheduled tasks, each verified by reading the state back. No Qt.

Every function must run on a COM-initialised thread (COMWorker). `connect` is
injectable so the logic tests without touching the real Task Scheduler.
Returns (ok, message): ok is only True when the read-back agrees.
"""
import logging
import time
from typing import Callable, Tuple

logger = logging.getLogger(__name__)


def _connect_default():
    import win32com.client
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    return svc


def split_path(full_path: str) -> Tuple[str, str]:
    """'\\Microsoft\\Windows\\X\\Task' -> ('\\Microsoft\\Windows\\X', 'Task')."""
    folder, _, name = full_path.rpartition("\\")
    return folder or "\\", name


def _task(svc, full_path: str):
    folder, name = split_path(full_path)
    return svc.GetFolder(folder).GetTask(name)


def set_enabled(full_path: str, enabled: bool, connect: Callable = _connect_default) -> Tuple[bool, str]:
    """Enable or disable a task through its own Enabled property.

    Not by re-registering its definition: RegisterTaskDefinition with an
    interactive logon type rewrites the principal, which breaks SYSTEM and
    password-stored tasks.
    """
    word = "enabled" if enabled else "disabled"
    try:
        task = _task(connect(), full_path)
        task.Enabled = enabled
        actual = bool(_task(connect(), full_path).Enabled)
    except Exception as exc:  # COM raises pywintypes.com_error, which is not importable everywhere
        logger.warning("set_enabled(%s, %s) failed: %s", full_path, enabled, exc)
        return False, f"Could not change the task: {exc}"
    if actual != enabled:
        return False, f"Windows accepted the call but the task is still {'enabled' if actual else 'disabled'}."
    return True, f"Task is now {word} (read back)."


def run_now(full_path: str, connect: Callable = _connect_default) -> Tuple[bool, str]:
    """Start the task and confirm the scheduler reports it running (or that it
    already finished: a short task may be done before the read-back)."""
    try:
        task = _task(connect(), full_path)
        before = task.LastRunTime
        task.Run("")
        time.sleep(1.0)
        again = _task(connect(), full_path)
        state, after = again.State, again.LastRunTime
    except Exception as exc:
        logger.warning("run_now(%s) failed: %s", full_path, exc)
        return False, f"Could not start the task: {exc}"
    if state == 4 or str(after) != str(before):
        return True, "Task started (the scheduler reports it running or freshly run)."
    return False, "The scheduler accepted the request but shows no run; check the task's conditions."


def delete(full_path: str, connect: Callable = _connect_default) -> Tuple[bool, str]:
    folder, name = split_path(full_path)
    try:
        svc = connect()
        svc.GetFolder(folder).DeleteTask(name, 0)
    except Exception as exc:
        logger.warning("delete(%s) failed: %s", full_path, exc)
        return False, f"Could not delete the task: {exc}"
    try:
        _task(connect(), full_path)
    except Exception:
        logger.debug("%s no longer resolves after delete, as expected", full_path)
        return True, "Task deleted (read back: it no longer exists)."
    return False, "The task is still there after the delete call."
