"""Whether Task Scheduler's own per-run history log is turned on, and turning
it on. No Qt.

Confirmed live on this real machine (2026-09-29): `wevtutil gl
"Microsoft-Windows-TaskScheduler/Operational"` reports `enabled: false`.
Windows ships this channel OFF by default, so the single LastRunTime /
LastTaskResult pair `tasks_reader.py` already shows is the ONLY per-task
signal this app (or Task Scheduler's own GUI) can show until someone turns
this channel on -- there is no history to read, recover, or reconstruct
before that moment. Turning it on is exactly what Task Scheduler's own GUI
calls "Enable All Tasks History" (Action menu); this module does the same
thing via `wevtutil sl ... /e:true`, verified by reading the channel's own
config back afterward rather than trusting the exit code -- `wevtutil`, like
every other refusing-while-clean tool this codebase has measured (`dism`,
`netsh`, `Get-BitLockerVolume`...), is not assumed to have done what it was
asked.

Deliberately does NOT implement a per-task filtered reader of this channel:
that would need this app to name the exact EventData field Windows renders
each event's task name under (commonly documented as "TaskName", but that
could not be verified against a real, populated event on this machine --
elevation was not available in the session that wrote this, and the channel
was empty regardless since it starts disabled). Guessing that field name and
shipping a filter against the guess risks the exact failure mode this
codebase's own history warns about hardest: a wrong filter returns zero
results, which reads identically to "no history yet" and would be a false
"nothing happened" for a task that actually ran and failed. Once the log is
on, Task Scheduler's own per-task History tab (`taskschd.msc`, already one
click away via this module's "Task Scheduler" button) is the verified way
to read it.
"""
import logging
import subprocess
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

#: The channel Task Scheduler's own GUI calls "Enable All Tasks History".
HISTORY_LOG = "Microsoft-Windows-TaskScheduler/Operational"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _parse_enabled(text: str) -> Optional[bool]:
    """The `enabled:` line of `wevtutil gl` output -- True/False, or None if
    the line is not there at all (a changed wevtutil output format, not a
    real answer)."""
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("enabled:"):
            value = line.split(":", 1)[1].strip().lower()
            if value in ("true", "false"):
                return value == "true"
    return None


def log_enabled(run: Callable = subprocess.run) -> Optional[bool]:
    """True/False the channel's own configuration; None if the read itself
    failed (wevtutil missing, the call refused, unparsable output) -- never
    collapsed into "disabled". Read-only, needs no elevation."""
    try:
        done = run(["wevtutil", "gl", HISTORY_LOG], capture_output=True, text=True,
                    timeout=15, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("wevtutil gl %s failed: %s", HISTORY_LOG, exc)
        return None
    if done.returncode != 0:
        logger.warning("wevtutil gl %s refused: %s", HISTORY_LOG, (done.stderr or "").strip()[:200])
        return None
    return _parse_enabled(done.stdout)


def enable_log(run: Callable = subprocess.run) -> Tuple[bool, str]:
    """Turn the channel on, verified by reading its configuration back.

    `wevtutil sl ... /e:true` needs elevation, and this module's own tab
    (`requires_admin = False`) may be open without it -- a refusal here is a
    normal, expected outcome for an unelevated session, reported plainly
    rather than raised.
    """
    try:
        done = run(["wevtutil", "sl", HISTORY_LOG, "/e:true"], capture_output=True, text=True,
                    timeout=15, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("wevtutil sl %s /e:true failed: %s", HISTORY_LOG, exc)
        return False, f"Could not run wevtutil: {exc}"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        reason = detail[0] if detail else f"exit code {done.returncode}"
        return False, f"Windows refused ({reason}); this needs an elevated instance of the app."
    actual = log_enabled(run=run)
    if actual is not True:
        return False, "wevtutil accepted the call but the channel still reads as off."
    return True, ("Task history logging is now on (read back). Every task's run history from "
                  "now on shows in Task Scheduler's own History tab -- nothing before this "
                  "moment was recorded, so there is nothing earlier to recover.")
