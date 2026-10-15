"""What each log an investigation needs actually keeps, and for how long.

An event log's `maxSize` is not the metric that matters. `System` and
`Application` on this machine are at Windows' 20 MB default and still reach
back six weeks; `Microsoft-Windows-PowerShell/Operational` is also at its
default and its oldest event was ONE day old, because Script Block Logging
fills it. The number an admin needs is COVERAGE -- how much history the log
holds right now -- and it is `now - oldest event`, readable from the log
itself.

Everything here is Qt-free and runs `wevtutil` through an injectable `run`
so the parsing is tested without the machine. A read that Windows refused (the
`Security` log and `auditpol` need administrator rights) is reported as a
refusal with the reason, never as "0 days" or "empty" -- the same rule the
Security Dashboard already follows.
"""
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: The logs a first look at an unknown client wants: Windows' three core logs
#: plus the two that record what actually ran (PowerShell, Task Scheduler).
IMPORTANT_LOGS = (
    "Security",
    "System",
    "Application",
    "Microsoft-Windows-PowerShell/Operational",
    "Microsoft-Windows-TaskScheduler/Operational",
)

#: wevtutil's exit code when the channel does not exist on this edition.
_ERROR_CHANNEL_NOT_FOUND = 15007
#: wevtutil's exit code when the caller is not allowed to read the channel.
_ERROR_ACCESS_DENIED = 5

_NOT_FOUND_MARKERS = (
    "could not be found", "was not found", "no such channel",
    "could not find the log",
)
_DENIED_MARKERS = ("access is denied", "access denied", "unauthorized")

_REFUSED_ADMIN = "needs administrator rights"
_REFUSED_TIMEOUT = "wevtutil timed out"

_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)")


@dataclass
class LogStatus:
    name: str
    #: False = the channel does not exist on this edition; None = could not ask.
    present: Optional[bool] = None
    enabled: Optional[bool] = None
    max_bytes: Optional[int] = None
    #: "circular" (overwrite), "archive" (autoBackup), "retain" (retention, no
    #: backup).
    mode: Optional[str] = None
    #: TimeCreated of the oldest retained event, in UTC.
    oldest: Optional[datetime] = None
    #: Why the read was refused (access denied, timeout), when it was.
    refused: Optional[str] = None

    def coverage_days(self, now: datetime) -> Optional[float]:
        """How many days of history the log holds, or None if unknown."""
        if self.oldest is None:
            return None
        return (now - self.oldest).total_seconds() / 86400.0


def parse_gl(text: str) -> Dict[str, str]:
    """Flatten `wevtutil gl` output into one dict of key -> value.

    `gl` prints a small tree; the values we want (`enabled`, `retention`,
    `autoBackup`, `maxSize`) are one per line, indented under a section. The
    section headers themselves have no value and come out empty; callers look
    values up by name.
    """
    fields: Dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key and key not in fields:
            fields[key] = value.strip()
    return fields


def _parse_timestamp(raw: str) -> Optional[datetime]:
    """A wevtutil SystemTime (`...-...T..:..:..[.fffffff]Z`) as a datetime."""
    match = _TS_RE.match(raw.strip())
    if not match:
        return None
    base, fraction, rest = match.groups()
    if fraction:
        fraction = fraction[:7]          # ".5310000" -> ".531000" (µs)
    if rest in ("Z", "z"):
        rest = "+00:00"
    try:
        parsed = datetime.fromisoformat(base + (fraction or "") + rest)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_oldest(xml_text: str) -> Optional[datetime]:
    """TimeCreated SystemTime of the first <Event>, or None if there is none."""
    match = re.search(r"SystemTime='([^']+)'", xml_text)
    if match is None:
        match = re.search(r'SystemTime="([^"]+)"', xml_text)
    if match is None:
        return None
    return _parse_timestamp(match.group(1))


def _default_run(args: List[str]) -> Tuple[int, str, str]:
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=20, creationflags=_NO_WINDOW,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _call(run: Callable, args: List[str]) -> Tuple[Optional[int], str, str, Optional[str]]:
    """Run wevtutil, turning a timeout or a missing binary into a reason.

    Returns (returncode, stdout, stderr, failure); `failure` is a refusal
    reason and returncode is None when the process itself did not answer.
    """
    try:
        rc, out, err = run(args)
    except subprocess.TimeoutExpired:
        logger.warning("wevtutil timed out: %s", " ".join(args))
        return None, "", "", _REFUSED_TIMEOUT
    except OSError as exc:
        logger.warning("wevtutil could not be run (%s): %s", exc, " ".join(args))
        return None, "", "", f"could not run wevtutil: {exc}"
    return rc, out or "", err or "", None


def _refusal_from(rc: int, err: str) -> str:
    """What a non-zero wevtutil exit means to the caller."""
    text = err.lower()
    if rc == _ERROR_ACCESS_DENIED or any(m in text for m in _DENIED_MARKERS):
        return _REFUSED_ADMIN
    first = err.strip().splitlines()
    return first[0].strip() if first else f"wevtutil exited {rc}"


def read_log(name: str, run: Optional[Callable] = None) -> LogStatus:
    """Everything known about one channel; never raises."""
    run = run or _default_run
    rc, out, err, failure = _call(run, ["wevtutil", "gl", name])
    if failure is not None:
        return LogStatus(name=name, refused=failure)
    if rc != 0:
        text = err.lower()
        if rc == _ERROR_CHANNEL_NOT_FOUND or any(m in text for m in _NOT_FOUND_MARKERS):
            return LogStatus(name=name, present=False)
        return LogStatus(name=name, refused=_refusal_from(rc, err))

    fields = parse_gl(out)
    status = LogStatus(name=name, present=True)
    enabled = fields.get("enabled", "").lower()
    if enabled in ("true", "false"):
        status.enabled = enabled == "true"
    try:
        status.max_bytes = int(fields.get("maxSize", ""))
    except ValueError:
        status.max_bytes = None
    auto_backup = fields.get("autoBackup", "").lower() == "true"
    retention = fields.get("retention", "").lower() == "true"
    if auto_backup:
        status.mode = "archive"
    elif retention:
        status.mode = "retain"
    else:
        status.mode = "circular"

    status.oldest = _read_oldest(name, run, status)
    return status


def _read_oldest(name: str, run: Callable, status: LogStatus) -> Optional[datetime]:
    """Oldest retained event; sets `status.refused` if the read was refused."""
    rc, out, err, failure = _call(
        run, ["wevtutil", "qe", name, "/c:1", "/rd:false", "/f:xml"])
    if failure is not None:
        status.refused = failure
        return None
    if rc != 0:
        status.refused = _refusal_from(rc, err)
        return None
    return parse_oldest(out)


def read_all(run: Optional[Callable] = None) -> List[LogStatus]:
    return [read_log(name, run=run) for name in IMPORTANT_LOGS]


def sysmon_status(run: Optional[Callable] = None) -> LogStatus:
    """Sysmon's channel: present only where Sysmon was deliberately installed."""
    return read_log("Microsoft-Windows-Sysmon/Operational", run=run)
