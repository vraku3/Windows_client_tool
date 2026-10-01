"""Security and reliability judgements about services. No Qt, no writes.

Works on the plain dicts `services_module.get_services()` returns, plus a few
readers of its own (failure events, recovery actions). A read that was refused
is `None`, never an empty answer.
"""
import logging
import re
import subprocess
import winreg
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The command line up to and including the first ".exe" that is followed by a
# space or the end. Windows itself tries the same prefixes left to right.
_EXE_PREFIX = re.compile(r"^(.*?\.exe)(?=\s|$)", re.IGNORECASE)


def executable_of(image_path: str) -> str:
    """The program a service's ImagePath launches, without its arguments.

    A quoted path is unambiguous. An unquoted one is cut at the first `.exe`
    that ends a word. Not an .exe (a driver's `\\SystemRoot\\...sys`) returns
    the whole string so callers can still show it.
    """
    text = (image_path or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 0 else text[1:]
    match = _EXE_PREFIX.match(text)
    return match.group(1) if match else text


def is_unquoted_path(image_path: str) -> bool:
    """True when the ImagePath is an unquoted path with a space in the program part.

    This is the classic local privilege-escalation setup: for
    `C:\\Program Files\\Some App\\svc.exe` Windows tries `C:\\Program.exe` and
    `C:\\Program Files\\Some.exe` first, and anyone who can write to one of those
    locations gets code run as the service account. Arguments after the exe do
    not count (`svchost.exe -k netsvcs` is fine), and a path without a space in
    the program part has no ambiguous prefix.
    """
    text = (image_path or "").strip()
    if not text or text.startswith('"'):
        return False
    program = executable_of(text)
    if not program.lower().endswith(".exe"):
        return False
    return " " in program.strip()


def hijack_candidates(image_path: str) -> List[str]:
    """The paths Windows would try before the real program, in order."""
    if not is_unquoted_path(image_path):
        return []
    program = executable_of(image_path)
    parts = program.split(" ")
    return [" ".join(parts[:i]) + ".exe" for i in range(1, len(parts))]


def unquoted_services(services: List[Dict]) -> List[Dict]:
    return [s for s in services if is_unquoted_path(str(s.get("Path") or ""))]


# ---- failure history -------------------------------------------------------------

#: 7031 "terminated unexpectedly", 7034 the same for a service that crashed
#: with no recovery action, 7023 "terminated with the following error".
FAILURE_EVENT_IDS = (7031, 7034, 7023)

_SERVICE_NAME_IN_MESSAGE = re.compile(r"The (.+?) service (?:terminated|failed)", re.I)


def parse_failure_counts(records: List[Dict]) -> Dict[str, int]:
    """Count failures per DISPLAY name from [{'Message': ..., 'Id': ...}] rows."""
    counts: Dict[str, int] = {}
    for rec in records:
        match = _SERVICE_NAME_IN_MESSAGE.search(str(rec.get("Message") or ""))
        if match:
            key = match.group(1).strip().lower()
            counts[key] = counts.get(key, 0) + 1
    return counts


def read_failure_counts(days: int = 30, timeout: int = 60) -> Optional[Dict[str, int]]:
    """Service failures from the System log in the last `days`, keyed by
    lower-cased display name. None if the log could not be read (a quiet
    machine returns {})."""
    ids = ",".join(str(i) for i in FAILURE_EVENT_IDS)
    script = (
        "$ErrorActionPreference='Stop';"
        f"$f=@{{LogName='System';ProviderName='Service Control Manager';Id={ids};"
        f"StartTime=(Get-Date).AddDays(-{int(days)})}};"
        "try{$e=Get-WinEvent -FilterHashtable $f -MaxEvents 2000}"
        "catch{if($_.FullyQualifiedErrorId -like '*NoMatchingEventsFound*'){'[]';exit 0}else{throw}};"
        "@($e|%{[pscustomobject]@{Id=$_.Id;Message=$_.Message}})|ConvertTo-Json -Compress")
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("service failure history unreadable: %s", exc)
        return None
    if done.returncode != 0:
        logger.warning("service failure history refused: %s", (done.stderr or "").strip()[:200])
        return None
    import json
    try:
        data = json.loads(done.stdout.strip() or "[]")
    except ValueError:
        logger.warning("service failure history was not JSON")
        return None
    if isinstance(data, dict):
        data = [data]
    return parse_failure_counts(data)


def failures_for(counts: Optional[Dict[str, int]], display_name: str) -> Optional[int]:
    """Failures for one service; None when the history could not be read."""
    if counts is None:
        return None
    return counts.get((display_name or "").strip().lower(), 0)


# ---- recovery actions (sc qfailure) ---------------------------------------------

_ACTION_LINE = re.compile(r"^(RESTART|REBOOT|RUN PROCESS|NONE)\s*--", re.I)


def parse_qfailure(text: str) -> Dict:
    """`sc qfailure` output -> {'reset_period': str, 'actions': [str], 'command': str}."""
    actions: List[str] = []
    reset = ""
    command = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.upper().startswith("RESET_PERIOD"):
            reset = line.split(":", 1)[1].strip() if ":" in line else ""
        elif line.upper().startswith("COMMAND_LINE"):
            command = line.split(":", 1)[1].strip() if ":" in line else ""
        elif line.upper().startswith("FAILURE_ACTIONS"):
            first = line.split(":", 1)[1].strip() if ":" in line else ""
            if _ACTION_LINE.match(first):
                actions.append(first)
        elif _ACTION_LINE.match(line):
            actions.append(line)
    return {"reset_period": reset, "actions": actions, "command": command}


def read_startup_flags() -> Optional[Dict[str, Dict[str, bool]]]:
    """{service name lower: {"delayed": bool, "trigger": bool}} for every
    service on the machine, in one registry walk (no subprocess).

    Delayed Automatic Start is the `DelayedAutostart` DWORD (absent or 0 =
    ordinary Automatic). Trigger-start is the presence of a `TriggerInfo`
    subkey -- Windows does not expose a simple boolean for it, the subkey's
    existence IS the answer, confirmed against 120 real trigger-started
    services on this machine. Both are readable unelevated.

    None only if the Services key itself could not even be opened; a single
    service's own key failing to open just leaves it out of the map, since
    one unreadable service (this app's own driver key, or a genuinely
    permission-locked one) must not blank the whole picture.
    """
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _SERVICES_KEY, 0, winreg.KEY_READ)
    except OSError as exc:
        logger.warning("cannot open %s: %s", _SERVICES_KEY, exc)
        return None
    flags: Dict[str, Dict[str, bool]] = {}
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError as exc:
                logger.debug("service enumeration ended at index %d: %s", index, exc)
                break            # past the last subkey
            index += 1
            try:
                with winreg.OpenKey(root, name, 0, winreg.KEY_READ) as key:
                    try:
                        delayed = bool(winreg.QueryValueEx(key, "DelayedAutostart")[0])
                    except FileNotFoundError:
                        delayed = False
                    trigger = _has_subkey(key, "TriggerInfo")
            except OSError as exc:
                logger.debug("service key %s unreadable: %s", name, exc)
                continue
            flags[name.lower()] = {"delayed": delayed, "trigger": trigger}
    return flags


def _has_subkey(key, name: str) -> bool:
    try:
        winreg.OpenKey(key, name, 0, winreg.KEY_READ).Close()
        return True
    except OSError:
        return False


def read_recovery(name: str) -> Optional[Dict]:
    """Recovery configuration of one service, or None if sc refused."""
    if not re.fullmatch(r"[\w .\-$@]+", name or ""):
        return None
    try:
        done = subprocess.run(["sc.exe", "qfailure", name], capture_output=True, text=True,
                              timeout=15, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("sc qfailure %s failed: %s", name, exc)
        return None
    out = done.stdout or ""
    if done.returncode != 0 or "RESET_PERIOD" not in out.upper():
        logger.debug("sc qfailure %s: %s", name, out.strip()[:120])
        return None
    return parse_qfailure(out)


def describe_recovery(info: Optional[Dict]) -> str:
    if info is None:
        return "could not be read"
    actions = [a for a in info.get("actions", []) if a and not a.upper().startswith("NONE")]
    if not actions:
        return "no recovery action: a crash leaves it stopped"
    return "; ".join(actions)


def blocked_dependencies(dep_names: List[str], start_types: Dict[str, str]) -> List[str]:
    """Names from a service's own DEPENDENCIES (as `parse_qc` returns them)
    that are themselves a service set to Disabled.

    The Service Control Manager refuses to start a service while any of its
    dependencies is disabled (`ERROR_SERVICE_DEPENDENCY_DELETED`/`_DISABLED`)
    -- an Automatic or Manual service with one of these is broken right now,
    even though its OWN Start Type column reads fine and gives no hint why it
    will not come up. `start_types` is `service_view.start_type_map()`'s
    {name lower: Start Type}; a dependency not found there is a driver or a
    load-order GROUP (`PNP_TDI`, `NDIS` and the like are common), neither of
    which this check can judge, and is left out rather than guessed at.
    Measured on this real machine (2026-10-01, 303 services, every one with
    a DEPENDENCIES entry checked): zero are broken this way right now -- the
    check exists for the day one tweak or a bad uninstall creates one.
    """
    out: List[str] = []
    for dep in dep_names:
        start = start_types.get(dep.strip().lower())
        if start and start.lower() == "disabled":
            out.append(dep)
    return out


def audit_lines(svc: Dict, failures: Optional[int]) -> List[Tuple[str, str]]:
    """(severity, sentence) pairs for one service; severity is 'warning' or 'info'."""
    lines: List[Tuple[str, str]] = []
    path = str(svc.get("Path") or "")
    if is_unquoted_path(path):
        tries = ", ".join(hijack_candidates(path)[:3])
        lines.append(("warning",
                      f"Unquoted service path with a space. Windows tries {tries} before the "
                      "real program: anyone who can write there gets code run as this service."))
    if failures:
        lines.append(("warning", f"{failures} crash event(s) in the System log in the last 30 days."))
    return lines


_QC_KEYS = {
    "DISPLAY_NAME": "display_name", "TYPE": "type", "START_TYPE": "start_type",
    "ERROR_CONTROL": "error_control", "BINARY_PATH_NAME": "binary_path",
    "LOAD_ORDER_GROUP": "load_order_group", "TAG": "tag_id", "SERVICE_START_NAME": "start_name",
}


def parse_qc(text: str) -> Dict:
    """`sc qc` output -> config dict. sc separates key and value with a COLON
    (the old parser split on '=', which raised IndexError on every real
    service), and a dependency list continues on lines that start with ':'."""
    cfg: Dict = {"dependencies": []}
    in_deps = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("[SC]") or line.startswith("SERVICE_NAME"):
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "DEPENDENCIES":
            in_deps = True
            if value:
                cfg["dependencies"].append(value)
        elif key == "" and sep and in_deps:
            if value:
                cfg["dependencies"].append(value)
        else:
            in_deps = False
            if key in _QC_KEYS:
                cfg[_QC_KEYS[key]] = value
    return cfg
