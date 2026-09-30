"""Health of the `WinClientTool_UnattendedMaintenance` scheduled task.

Settings shows "Task saved" the moment `schtasks /create` exits 0 — that only
proves the task was REGISTERED, not that any scheduled run since then actually
worked. Measured on this real machine (2026-09-30): the task exists, is
Enabled, and its next run is correctly scheduled — but its most recent
(9/29/2026 12:08 PM) run has `Last Result -2147020576` (0x800710E0, "the
operator or administrator has refused the request"), and nothing in the UI
said so. A green "Task saved." from a week-old click is exactly the kind of
stale confidence this app's own CLAUDE.md warns about elsewhere ("a refusal
is never collapsed into a value") — so this reads the task's actual last-run
outcome fresh, every time the Settings tab becomes visible.

Qt-free by the same convention every other engine/UI split in this app
follows (scan/+store/ in TreeSize, engine/ in File Forensics, etc.) so it can
be exercised without a display.
"""
import ctypes
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

#: Positive SCHED_S_* values `schtasks` prints under "Last Result" that are
#: not failures at all — a raw decimal here reads as some huge error code to
#: anyone who doesn't have 0x00041303 memorized.
_SCHED_RESULT_TEXT = {
    0: "success",
    267009: "currently running",           # 0x00041301 SCHED_S_TASK_RUNNING
    267010: "disabled",                    # 0x00041302 SCHED_S_TASK_DISABLED
    267011: "has never run",               # 0x00041303 SCHED_S_TASK_HAS_NOT_RUN
    267012: "has no more scheduled runs",  # 0x00041304 SCHED_S_TASK_HAS_NO_MORE_RUNS
    267014: "terminated by request",       # 0x00041306 SCHED_S_TASK_TERMINATED
}

# The handful of `schtasks /query` "Key:" lines this module reads. Matched by
# an exact prefix, never a first-colon split — several other keys the same
# output carries (e.g. "Repeat: Every:") contain a colon in the KEY itself,
# which would desync a naive split(":", 1) parser. These five never do.
_KEYS = ("Status", "Scheduled Task State", "Last Run Time", "Last Result", "Next Run Time")


@dataclass
class TaskHealth:
    """`exists` and `error` are deliberately separate from every other field:
    `exists=False` means schtasks positively said the task is not registered;
    `exists=None` means the query itself failed for some OTHER reason (a
    non-zero exit that isn't "not found" — e.g. a permissions refusal on a
    machine where this user can't read another principal's task) and nothing
    below should be read as "no task" in that case."""
    exists: Optional[bool]
    enabled: Optional[bool] = None
    status: str = ""
    last_run: Optional[str] = None
    last_result_code: Optional[int] = None
    last_result_text: str = ""
    next_run: Optional[str] = None
    error: Optional[str] = None


def _decode_task_result(code: int) -> str:
    """A friendly string for a `schtasks` "Last Result" value.

    Known SCHED_S_* codes get their real meaning. An unknown code in the
    0x8007xxxx (Win32) facility falls back to the system's own message for
    that Win32 error, same fallback `core/wu_error_codes.py` uses and for the
    same reason — a bare hex code tells an admin nothing a decoded one
    wouldn't. Anything else is reported as hex rather than guessed.
    """
    if code in _SCHED_RESULT_TEXT:
        return _SCHED_RESULT_TEXT[code]
    unsigned = code & 0xFFFFFFFF
    hex_str = f"0x{unsigned:08X}"
    if (unsigned & 0xFFFF0000) == 0x80070000:
        try:
            msg = ctypes.WinError(unsigned & 0xFFFF).strerror
        except Exception:
            msg = None
        if msg:
            return f"{hex_str} - {msg}"
    return hex_str


def _parse_fields(output: str) -> dict:
    fields = {}
    for line in output.splitlines():
        for key in _KEYS:
            prefix = key + ":"
            if line.startswith(prefix):
                fields[key] = line[len(prefix):].strip()
                break
    return fields


def _clean(value: Optional[str]) -> Optional[str]:
    if not value or value == "N/A":
        return None
    return value


def check_task_health(task_name: str) -> TaskHealth:
    """Query the named scheduled task's current registration and last-run
    outcome via `schtasks /query` — the same CLI this module already uses to
    create/remove/run the task, so no new elevation or COM dependency.

    Measured on this real machine: querying a task this user owns (including
    one running as SYSTEM, `\\Microsoft\\Windows\\SystemRestore\\SR`) needs no
    elevation at all, so this is safe to call from an unelevated Settings tab.
    """
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/fo", "list", "/v"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        logger.warning("schtasks query failed for %s: %s", task_name, e)
        return TaskHealth(exists=None, error=str(e))

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "cannot find the file specified" in stderr.lower() or "cannot find the task" in stderr.lower():
            return TaskHealth(exists=False, status="not found")
        # Any other non-zero exit is a refusal or unexpected failure — never
        # collapsed into "the task doesn't exist", which would tell an admin
        # to go recreate a task that is actually still there.
        return TaskHealth(exists=None, error=stderr or f"schtasks exited {proc.returncode}")

    fields = _parse_fields(proc.stdout)
    state = fields.get("Scheduled Task State", "")
    enabled = True if state == "Enabled" else (False if state == "Disabled" else None)

    result_raw = _clean(fields.get("Last Result"))
    last_result_code: Optional[int] = None
    last_result_text = ""
    if result_raw is not None:
        try:
            last_result_code = int(result_raw)
            last_result_text = _decode_task_result(last_result_code)
        except ValueError:
            last_result_text = result_raw

    return TaskHealth(
        exists=True,
        enabled=enabled,
        status=fields.get("Status", ""),
        last_run=_clean(fields.get("Last Run Time")),
        last_result_code=last_result_code,
        last_result_text=last_result_text,
        next_run=_clean(fields.get("Next Run Time")),
    )
