"""What each important Windows event log keeps, and for how long.

Qt-free. Size alone is not the metric -- COVERAGE (now - oldest event) is:
a full 15 MB PowerShell log can hold one day while a 20 MB System log holds
six weeks. A refused read is reported as ``refused`` with its reason, never
as an empty or absent log.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

IMPORTANT_LOGS = (
    "Security",
    "System",
    "Application",
    "Microsoft-Windows-PowerShell/Operational",
    "Microsoft-Windows-TaskScheduler/Operational",
)
SYSMON_LOG = "Microsoft-Windows-Sysmon/Operational"

_TIMEOUT_S = 20
_NOT_FOUND_RC = 15007
_ACCESS_DENIED_RC = 5
_NEEDS_ADMIN = "needs administrator rights"
_TIMED_OUT = "wevtutil timed out"

Run = Callable[[List[str]], Tuple[int, str, str]]


@dataclass
class LogStatus:
    name: str
    present: Optional[bool]  # False = channel does not exist; None = could not ask
    enabled: Optional[bool] = None
    max_bytes: Optional[int] = None
    mode: Optional[str] = None  # "circular" | "archive" | "retain"
    oldest: Optional[datetime] = None  # UTC
    refused: Optional[str] = None  # reason when the read was refused

    def coverage_days(self, now: datetime) -> Optional[float]:
        """Days of history kept, or None when the oldest event is not known."""
        if self.oldest is None:
            return None
        return (now - self.oldest).total_seconds() / 86400.0


def _default_run(args: List[str]) -> Tuple[int, str, str]:
    p = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=_TIMEOUT_S, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return p.returncode, p.stdout or "", p.stderr or ""


def parse_gl(text: str) -> Dict[str, str]:
    """`wevtutil gl` output -> flat {key: value} (sections are flattened)."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key:
            out.setdefault(key.strip(), value.strip())
    return out


_SYSTEM_TIME = re.compile(r"SystemTime='([^']+)'")
_FRACTION = re.compile(r"\.(\d+)")


def parse_oldest(xml_text: str) -> Optional[datetime]:
    """TimeCreated SystemTime of the first <Event>, as UTC; None if absent."""
    if "<Event" not in xml_text:
        return None
    m = _SYSTEM_TIME.search(xml_text)
    if not m:
        return None
    raw = m.group(1)
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    raw = _FRACTION.sub(lambda f: "." + f.group(1)[:6].ljust(6, "0"), raw, count=1)
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        logger.warning("log_readiness: unparseable event time %r", m.group(1))
        return None


def _is_access_denied(rc: int, text: str) -> bool:
    return rc == _ACCESS_DENIED_RC or "access is denied" in text.lower()


def _is_not_found(rc: int, text: str) -> bool:
    low = text.lower()
    return (rc in (_NOT_FOUND_RC, _NOT_FOUND_RC & 0xFF)
            or "channel could not be found" in low or "channel was not found" in low)


def _ask(run: Run, args: List[str]) -> Tuple[Optional[int], str, Optional[str]]:
    """Run wevtutil; returns (rc, combined text, refusal). Never raises."""
    try:
        rc, out, err = run(args)
    except (subprocess.TimeoutExpired, TimeoutError):
        return None, "", _TIMED_OUT
    except OSError as e:
        logger.warning("log_readiness: wevtutil could not run: %s", e)
        return None, "", f"wevtutil could not run: {e}"
    return rc, (out or "") + "\n" + (err or ""), None


def _mode(info: Dict[str, str]) -> str:
    if info.get("autoBackup", "").lower() == "true":
        return "archive"
    if info.get("retention", "").lower() == "true":
        return "retain"
    return "circular"


def _max_bytes(info: Dict[str, str]) -> Optional[int]:
    try:
        return int(info["maxSize"])
    except (KeyError, ValueError):
        return None


def _read_oldest(name: str, run: Run, st: LogStatus) -> None:
    rc, text, refusal = _ask(run, ["wevtutil", "qe", name, "/c:1", "/rd:false", "/f:xml"])
    if refusal:
        st.refused = refusal
    elif "<Event" not in text and _is_access_denied(rc or 0, text):
        # an event's own message may say "access is denied"; only a reply
        # with no event in it is a refusal, whatever the exit status
        st.refused = _NEEDS_ADMIN
    elif rc != 0:
        st.refused = f"wevtutil failed: {text.strip()[:120]}"
    else:
        st.oldest = parse_oldest(text)


def _read_gl(name: str, run: Run) -> Tuple[Optional[Dict[str, str]], Optional[LogStatus]]:
    """`(config, None)` from `wevtutil gl`, or `(None, status saying why not)`."""
    rc, text, refusal = _ask(run, ["wevtutil", "gl", name])
    if refusal:
        return None, LogStatus(name, None, refused=refusal)
    if _is_access_denied(rc or 0, text):
        return None, LogStatus(name, None, refused=_NEEDS_ADMIN)
    if rc != 0:
        if _is_not_found(rc or 0, text):
            return None, LogStatus(name, False)
        return None, LogStatus(name, None, refused=f"wevtutil failed: {text.strip()[:120]}")
    return parse_gl(text), None


def read_setting(name: str, key: str, run: Optional[Run] = None) -> Optional[str]:
    """One raw `wevtutil gl` value (e.g. "maxSize", "enabled"), or None when the
    log is absent, the read was refused, or the key is not there. Configuration
    only -- no event query. Callers validate the text before using it."""
    info, _ = _read_gl(name, run or _default_run)
    return None if info is None else info.get(key)


def read_log(name: str, run: Optional[Run] = None) -> LogStatus:
    """Configuration and real coverage of one log. Never raises."""
    run = run or _default_run
    info, failed = _read_gl(name, run)
    if info is None:
        return failed
    st = LogStatus(name, True, enabled=info.get("enabled", "").lower() == "true",
                   max_bytes=_max_bytes(info), mode=_mode(info))
    _read_oldest(name, run, st)
    return st


def read_all(run: Optional[Run] = None) -> List[LogStatus]:
    return [read_log(n, run) for n in IMPORTANT_LOGS]


def sysmon_status(run: Optional[Run] = None) -> LogStatus:
    return read_log(SYSMON_LOG, run)
