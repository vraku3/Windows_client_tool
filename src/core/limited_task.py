"""One-shot interactive task at LIMITED run level, as in Game Mode's shell restart.

Call on a COM-initialized worker. No persisted task or password.
"""
import logging
import subprocess
import time
import uuid
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
TASK_PREFIX = "WinClientTool_OneShot_"


def _prune_old_tasks(folder) -> None:
    """Remove only stale one-shot registrations in the root task folder."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        entries = list(folder.GetTasks(1))  # snapshot, including hidden registrations
        for entry in entries:
            try:
                name = str(entry.Name)
                if not name.startswith(TASK_PREFIX):
                    continue
                registered = datetime.fromisoformat(str(entry.Definition.RegistrationInfo.Date).replace("Z", "+00:00"))
                if registered.astimezone(timezone.utc) < cutoff:
                    folder.DeleteTask(name, 0)
                    logger.info("Deleted stale one-shot task %s (registered %s)", name, registered)
            except Exception:
                logger.warning("Could not inspect/delete a stale one-shot task", exc_info=True)
    except Exception:
        logger.warning("Could not enumerate stale one-shot tasks", exc_info=True)


def _current_user_sid() -> str:
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid, _attributes = win32security.GetTokenInformation(token, win32security.TokenUser)
        return win32security.ConvertSidToStringSid(sid)
    finally:
        token.Close()


def run_limited(executable: str, args: list, timeout_s: float = 600) -> tuple:
    """(last result, reason); None means unreadable/timed out, never success.

    Delete the registration even on failure. Do not stop an installer when
    waiting expires: removing a running task's registration leaves it running.
    """
    import win32com.client
    import pythoncom
    pythoncom.CoInitialize()
    name = TASK_PREFIX + uuid.uuid4().hex
    folder = None
    registered = False
    launch_attempted = False
    try:
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        folder = service.GetFolder("\\")
        _prune_old_tasks(folder)
        task = service.NewTask(0)
        task.RegistrationInfo.Date = datetime.now(timezone.utc).isoformat()
        sid = _current_user_sid()
        task.Principal.UserId = sid
        task.Principal.LogonType = 3     # TASK_LOGON_INTERACTIVE_TOKEN
        task.Principal.RunLevel = 0     # TASK_RUNLEVEL_LUA (LIMITED)
        task.Settings.Enabled = True
        task.Settings.Hidden = True
        task.Settings.AllowDemandStart = True
        task.Settings.DisallowStartIfOnBatteries = False
        task.Settings.StopIfGoingOnBatteries = False
        task.Settings.ExecutionTimeLimit = "PT0S"
        action = task.Actions.Create(0)  # TASK_ACTION_EXEC
        action.Path = executable
        action.Arguments = subprocess.list2cmdline(args)
        entry = folder.RegisterTaskDefinition(name, task, 2, sid, None, 3)
        registered = True
        # Run can dispatch successfully and then lose its COM reply.
        launch_attempted = True
        entry.Run(None)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            # Query the registered task: a completed IRunningTask instance
            # may already have disappeared when Refresh() is called.
            if int(entry.State) not in (2, 4):  # queued / running
                result = int(entry.LastTaskResult)
                if result not in (0x41301, 0x41303):
                    return result, ""
            time.sleep(1)
        return None, "STILL RUNNING: installer still running after 10 minutes (unelevated task timed out)"
    except Exception as exc:
        logger.warning("Limited task result unavailable: %s", exc, exc_info=True)
        prefix = "STILL RUNNING: launched, completion unknown; " if launch_attempted else "failed before launch: "
        return None, f"{prefix}unelevated task result unreadable: {exc}"
    finally:
        if registered:
            try:
                folder.DeleteTask(name, 0)
            except Exception:
                logger.warning("Could not delete one-shot task %s", name, exc_info=True)
        pythoncom.CoUninitialize()
