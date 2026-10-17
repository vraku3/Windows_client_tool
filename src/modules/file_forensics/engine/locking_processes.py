"""Which processes have a specific file open right now.

Wraps `core.procengine.findref.find()` -- the same native NT
handle-enumeration Process Explorer's own Ctrl+F uses -- scoped to one
exact file rather than a free-text search, and filtered to `File`-typed
handle matches only (a DLL/module match is a different, separate
question this app already answers elsewhere).
"""
from dataclasses import dataclass
from typing import List, Tuple

from core.procengine.findref import find
from .device_paths import to_device_path


@dataclass(frozen=True)
class LockingProcess:
    pid: int
    process: str
    type_name: str


def find_locking_processes(path: str) -> Tuple[List[LockingProcess], str]:
    """Returns (processes holding `path` open, an honest summary of what
    could be searched) -- the summary travels with the answer always, not
    only when something was refused, so a caller never has to remember to
    ask for it separately."""
    device_path = to_device_path(path)
    if device_path is None:
        return [], ("The file's path could not be translated to a form "
                    "the handle search understands (not on a local drive?).")

    report = find(device_path, handles=True, modules=False)
    procs = [
        LockingProcess(pid=m.pid, process=m.process, type_name=m.type_name)
        for m in report.matches
        if m.type_name == "File" and is_same_file(m.detail, device_path)
    ]
    return procs, report.summary()


def is_same_file(handle_name: str, device_path: str) -> bool:
    """`find()` matches a SUBSTRING, so `report.log` also finds handles on
    `report.log.old` and `report.log.bak\\x` -- and this answer feeds "Kill
    locking process". Only the file itself, or one of its NTFS streams
    (`file:stream`), counts. NT names compare case-insensitively."""
    name = (handle_name or "").lower()
    target = device_path.lower()
    return name == target or name.startswith(target + ":")
