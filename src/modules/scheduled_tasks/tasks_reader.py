from dataclasses import dataclass, field
from typing import List, Optional
import logging
logger = logging.getLogger(__name__)


@dataclass
class TaskInfo:
    name: str
    path: str
    status: str      # "Ready" | "Running" | "Disabled" | "Unknown"
    last_run: str
    last_result: str
    next_run: str
    author: str
    triggers: str
    xml: str
    enabled: bool
    last_result_code: Optional[int] = None   # unsigned 32-bit, None if unreadable


@dataclass
class TaskFolder:
    name: str
    path: str
    subfolders: List["TaskFolder"] = field(default_factory=list)


def _fmt_date(dt) -> str:
    try:
        s = str(dt)
        # Task Scheduler reports "never" as 1999-11-30 (or the 1899 COM epoch).
        if s[:4].isdigit() and int(s[:4]) < 2000:
            return "Never"
        return s[:16].replace("T", " ")
    except Exception:
        logger.warning("Ignored Exception formatting date", exc_info=True)
        return ""


def _code(raw) -> Optional[int]:
    try:
        return int(raw) & 0xFFFFFFFF
    except (TypeError, ValueError):
        logger.debug("LastTaskResult not numeric: %r", raw)
        return None


def get_folder_tree() -> TaskFolder:
    """Returns root TaskFolder with nested subfolders. Must be called from COMWorker."""
    import win32com.client
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    root = svc.GetFolder("\\")
    return _build_folder(root, "\\")


def _build_folder(com_folder, path: str) -> TaskFolder:
    tf = TaskFolder(name=com_folder.Name or "\\", path=path)
    try:
        for sub in com_folder.GetFolders(0):
            subpath = path.rstrip("\\") + "\\" + sub.Name
            tf.subfolders.append(_build_folder(sub, subpath))
    except Exception as e:
        logger.warning("Could not enumerate task subfolders for %s: %s", path, e)
    return tf


def get_tasks_in_folder(folder_path: str) -> List[TaskInfo]:
    """Get tasks in a specific folder. Must be called from COMWorker."""
    import win32com.client
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    if folder_path == ALL_FOLDERS:
        return _all_tasks(svc)
    return _tasks_of(svc.GetFolder(folder_path), folder_path)


#: Pseudo folder path meaning "every folder, recursively".
ALL_FOLDERS = "*"


def _all_tasks(svc) -> List[TaskInfo]:
    out: List[TaskInfo] = []

    def walk(folder, path):
        out.extend(_tasks_of(folder, path))
        try:
            for sub in folder.GetFolders(0):
                walk(sub, path.rstrip("\\") + "\\" + sub.Name)
        except Exception as e:
            logger.warning("Could not enumerate task subfolders for %s: %s", path, e)

    walk(svc.GetFolder("\\"), "\\")
    return out


def _tasks_of(folder, folder_path: str) -> List[TaskInfo]:
    tasks = []
    try:
        # Flag 1 = TASK_ENUM_HIDDEN: with 0 the hidden tasks were never listed.
        collection = folder.GetTasks(1)
        for i in range(collection.Count):
            task = collection.Item(i + 1)
            try:
                state_map = {0: "Unknown", 1: "Disabled", 2: "Queued", 3: "Ready", 4: "Running"}
                status = state_map.get(task.State, "Unknown")
                # Triggers summary
                try:
                    trig_count = task.Definition.Triggers.Count
                    if trig_count > 0:
                        trig_type = task.Definition.Triggers.Item(1).Type
                        type_map = {
                            1: "Event", 2: "Time", 3: "Daily", 4: "Weekly", 5: "Monthly",
                            6: "MonthlyDOW", 7: "Idle", 8: "Registration", 9: "Boot",
                            10: "Logon", 11: "SessionStateChange",
                        }
                        triggers = type_map.get(trig_type, f"Type{trig_type}")
                        if trig_count > 1:
                            triggers += f" (+{trig_count - 1})"
                    else:
                        triggers = "None"
                except Exception as e:
                    logger.debug("Could not read triggers for task %s: %s", task.Name if hasattr(task, 'Name') else '?', e)
                    triggers = ""
                # Author
                try:
                    author = task.Definition.RegistrationInfo.Author or ""
                except Exception as e:
                    logger.debug("Could not read author for task: %s", e)
                    author = ""
                tasks.append(TaskInfo(
                    name=task.Name,
                    path=task.Path,
                    status=status,
                    last_run=_fmt_date(task.LastRunTime),
                    last_result=str(task.LastTaskResult),
                    next_run=_fmt_date(task.NextRunTime),
                    author=author,
                    triggers=triggers,
                    xml=task.Xml,
                    enabled=(task.State != 1),
                    last_result_code=_code(task.LastTaskResult),
                ))
            except Exception as e:
                logger.debug("Skipping task due to error: %s", e)
                continue
    except Exception as e:
        logger.warning("Could not enumerate tasks in folder %s: %s", folder_path, e)
    return tasks
