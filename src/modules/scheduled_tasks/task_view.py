"""What the Scheduled Tasks tab can say about a task. No Qt.

Works on `tasks_reader.TaskInfo` rows. Everything derived from the task's XML
is parsed here (the reader already carries the XML), so no extra COM calls.
A field the XML does not carry is None/"" -- never a guess.
"""
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"

#: HRESULTs / exit codes a task's LastTaskResult commonly holds.
RESULT_WORDS: Dict[int, str] = {
    0x0: "Completed successfully",
    0x1: "The program returned exit code 1 (an error, by convention)",
    0x2: "File not found (the program or a file it needs is missing)",
    0x3: "Path not found",
    0x5: "Access denied",
    0xA: "Bad environment",
    0x41300: "Ready to run at its next scheduled time",
    0x41301: "Running right now",
    0x41302: "Disabled",
    0x41303: "Has not run yet",
    0x41304: "No more runs are scheduled",
    0x41305: "Not scheduled",
    0x41306: "Terminated (by the user, or it hit its time limit)",
    0x41307: "No valid triggers (all expired or disabled)",
    0x41308: "Event triggers do not have a next run time",
    0x8004130F: "No account information could be found",
    0x80041310: "Unknown or unsupported task property",
    0x80041314: "The task registration is corrupt",
    0x80041315: "The Task Scheduler service is not running",
    0x8004131F: "An instance is already running",
    0x80041320: "The task will not run at the scheduled time (missed, not on battery/AC, or no network)",
    0x80041324: "The task did not run when its time came (missed, e.g. machine was off)",
    0x800704C7: "Cancelled by the user",
    0x800704DD: "The user is not logged on to the network",
    0x8007010B: "The working directory does not exist",
    0x80040154: "Class not registered (a COM component the task needs is missing)",
    0x80070002: "File not found (the program or a file it needs is missing)",
    0x80070005: "Access denied",
    0x80070020: "A file is in use by another process",
    0x80070420: "The service is already running",
    0x800710E0: "The operator or administrator has refused the request",
    0x8007052E: "Logon failure: wrong user name or password",
    0x8007052F: "Logon failure: account restriction",
    0x80070533: "Logon failure: the account is disabled",
    0x8007052B: "Logon failure: the password must be changed",
    0xC000013A: "Terminated with Ctrl+C",
    0xC0000005: "The program crashed (access violation)",
    0xC0000135: "A DLL the program needs was not found",
    0xC0000142: "A DLL failed to initialise",
    0xC000004E: "The program was blocked",
    0xFFFD0000: "Not a valid Win32 program",
}

_SUCCESS_CLASS = range(0x41300, 0x41320)


def to_code(raw) -> Optional[int]:
    """A LastTaskResult as an unsigned 32-bit int; None if it is not a number."""
    try:
        return int(str(raw).strip(), 0) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None


def describe_result(raw) -> str:
    """'0x80070002: File not found ...' -- the code and what it means."""
    code = to_code(raw)
    if code is None:
        return "Unknown result"
    words = RESULT_WORDS.get(code)
    if words is None:
        if code <= 0xFFFF:
            words = f"The program exited with code {code}"
        elif code & 0xFFFF0000 == 0x80070000:
            words = f"Windows error {code & 0xFFFF} (see `net helpmsg {code & 0xFFFF}`)"
        else:
            words = "No description for this code"
    return f"0x{code:X}: {words}"


def failed(raw) -> bool:
    """A last result that says the task did not do its job.

    0 is success; 0x4130x are Task Scheduler's own informational codes (ready,
    running, not run yet, ...). 0x41306 (terminated) is a stop, not a fault,
    but it is not success either, so it is reported as failed.
    """
    code = to_code(raw)
    if code is None or code == 0:
        return False
    if code in _SUCCESS_CLASS and code != 0x41306:
        return False
    return True


def never_ran(raw) -> bool:
    return to_code(raw) == 0x41303


# ---- XML ------------------------------------------------------------------------

_TRIGGER_WORDS = {
    "LogonTrigger": "At logon", "BootTrigger": "At startup", "TimeTrigger": "One time",
    "CalendarTrigger": "On a schedule", "EventTrigger": "On an event",
    "IdleTrigger": "When idle", "RegistrationTrigger": "When registered",
    "SessionStateChangeTrigger": "On session change",
}
_WELL_KNOWN_SIDS = {"S-1-5-18": "SYSTEM", "S-1-5-19": "LOCAL SERVICE",
                    "S-1-5-20": "NETWORK SERVICE", "S-1-5-32-544": "Administrators",
                    "S-1-5-32-545": "Users", "S-1-5-4": "Logged-on users",
                    "S-1-5-11": "Authenticated Users", "S-1-1-0": "Everyone",
                    "S-1-5-32-546": "Guests"}
_SID_CACHE: Dict[str, str] = {}


def resolve_principal(who: str) -> str:
    """A SID as a readable name: well-known table first, then the local account
    database. Anything unresolvable is returned unchanged (the SID), not blanked."""
    if who in _WELL_KNOWN_SIDS:
        return _WELL_KNOWN_SIDS[who]
    if not who.startswith("S-1-5-21-"):
        return who
    if who not in _SID_CACHE:
        try:
            import win32security
            sid = win32security.ConvertStringSidToSid(who)
            name, domain, _kind = win32security.LookupAccountSid(None, sid)
            _SID_CACHE[who] = f"{domain}\\{name}" if domain else name
        except Exception as exc:  # pywintypes.error when the SID is not known here
            logger.debug("SID %s did not resolve: %s", who, exc)
            _SID_CACHE[who] = who
    return _SID_CACHE[who]


@dataclass
class TaskDetails:
    run_as: str = ""          # user or group the task runs as
    run_level: str = ""       # "HighestAvailable" or "LeastPrivilege"
    logon_type: str = ""
    commands: List[Tuple[str, str]] = field(default_factory=list)   # (program, arguments)
    triggers: List[str] = field(default_factory=list)
    hidden: bool = False
    parsed: bool = False


def _text(node, path: str) -> str:
    found = node.find(path) if node is not None else None
    return (found.text or "").strip() if found is not None else ""


def parse_details(xml: str) -> TaskDetails:
    """Principal, actions and triggers out of a task's XML; parsed=False if it will not parse."""
    if not xml or not xml.strip():
        return TaskDetails()
    try:
        # The COM Xml property says encoding="UTF-16" on a str; ET rejects that.
        root = ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", xml.strip()))
    except ET.ParseError as exc:
        logger.debug("task XML did not parse: %s", exc)
        return TaskDetails()
    d = TaskDetails(parsed=True)
    principal = root.find(f"{_NS}Principals/{_NS}Principal")
    if principal is not None:
        who = _text(principal, f"{_NS}UserId") or _text(principal, f"{_NS}GroupId")
        d.run_as = resolve_principal(who)
        d.run_level = _text(principal, f"{_NS}RunLevel")
        d.logon_type = _text(principal, f"{_NS}LogonType")
    for ex in root.findall(f"{_NS}Actions/{_NS}Exec"):
        d.commands.append((_text(ex, f"{_NS}Command"), _text(ex, f"{_NS}Arguments")))
    for com in root.findall(f"{_NS}Actions/{_NS}ComHandler"):
        d.commands.append((f"COM handler {_text(com, f'{_NS}ClassId')}", ""))
    triggers = root.find(f"{_NS}Triggers")
    if triggers is not None:
        for trig in triggers:
            tag = trig.tag.replace(_NS, "")
            if _text(trig, f"{_NS}Enabled").lower() == "false":
                continue
            d.triggers.append(_TRIGGER_WORDS.get(tag, tag))
    d.hidden = _text(root.find(f"{_NS}Settings"), f"{_NS}Hidden").lower() == "true"
    return d


def program_path(command: str) -> str:
    """The command with environment variables expanded and quotes removed."""
    return os.path.expandvars((command or "").strip().strip('"'))


def program_missing(details: TaskDetails) -> Optional[bool]:
    """True if the first Exec action's program is not on disk; None if there is
    none to check (COM handler, unparsed XML) or it is a bare name found via PATH."""
    if not details.commands:
        return None
    command = program_path(details.commands[0][0])
    if not command or command.startswith("COM handler"):
        return None
    if os.path.isabs(command):
        return not os.path.exists(command)
    import shutil
    return shutil.which(command) is None


# ---- filters --------------------------------------------------------------------

def is_microsoft(task) -> bool:
    path = (getattr(task, "path", "") or "").lower()
    return path.startswith("\\microsoft\\")


def _details(task) -> TaskDetails:
    cached = getattr(task, "_details", None)
    if cached is None:
        cached = parse_details(getattr(task, "xml", ""))
        try:
            task._details = cached
        except AttributeError:
            logger.debug("task object does not take a cache attribute")
    return cached


def details_of(task) -> TaskDetails:
    return _details(task)


def _is_disabled(t) -> bool:
    return t.status == "Disabled"


def _runs_as_system(t) -> bool:
    return _details(t).run_as.upper() in ("SYSTEM", "NT AUTHORITY\\SYSTEM")


def _at_logon_or_boot(t) -> bool:
    return any(x in ("At logon", "At startup") for x in _details(t).triggers)


def _missing(t) -> bool:
    return bool(program_missing(_details(t)))


def _highest(t) -> bool:
    return _details(t).run_level == "HighestAvailable"


FILTERS: Tuple[Tuple[str, str, Callable], ...] = (
    ("all", "All", lambda t: True),
    ("failed", "Failed", lambda t: failed(t.last_result_code)),
    ("never", "Never ran", lambda t: never_ran(t.last_result_code)),
    ("running", "Running", lambda t: t.status == "Running"),
    ("disabled", "Disabled", _is_disabled),
    ("nonms", "Third-party", lambda t: not is_microsoft(t)),
    ("system", "SYSTEM", _runs_as_system),
    ("logon", "Logon/boot", _at_logon_or_boot),
    ("highest", "Elevated", _highest),
    ("missing", "Missing exe", _missing),
)
_BY_KEY = {k: fn for k, _, fn in FILTERS}


def passes(key: str, task) -> bool:
    return _BY_KEY.get(key, _BY_KEY["all"])(task)


def filter_counts(tasks: Iterable) -> Dict[str, int]:
    tasks = list(tasks)
    return {k: sum(1 for t in tasks if fn(t)) for k, _, fn in FILTERS}


def matches(task, needle: str) -> bool:
    """Name, path, author, or the command/arguments contain `needle`."""
    needle = (needle or "").strip().lower()
    if not needle:
        return True
    d = _details(task)
    hay = [task.name, task.path, task.author, d.run_as]
    for prog, args in d.commands:
        hay += [prog, args]
    return any(needle in (h or "").lower() for h in hay)


def visible(tasks: Iterable, key: str, needle: str) -> List:
    return [t for t in tasks if passes(key, t) and matches(t, needle)]


def detail_text(task) -> str:
    """Everything worth reading about one task, as plain text."""
    d = _details(task)
    lines = [
        f"{task.name}   ({task.path})",
        f"State:       {task.status}",
        f"Last run:    {task.last_run}",
        f"Last result: {describe_result(task.last_result_code if task.last_result_code is not None else task.last_result)}",
        f"Next run:    {task.next_run or 'none'}",
        f"Triggers:    {', '.join(d.triggers) or task.triggers or 'none'}",
        f"Runs as:     {d.run_as or 'unknown'}"
        + (f"   ({d.run_level})" if d.run_level else ""),
    ]
    for prog, args in d.commands:
        lines.append(f"Runs:        {prog} {args}".rstrip())
    missing = program_missing(d)
    if missing:
        lines.append("WARNING:     the program does not exist on disk.")
    if not is_microsoft(task) and _runs_as_system(task):
        lines.append("NOTE:        third-party task running as SYSTEM.")
    if d.hidden:
        lines.append("NOTE:        the task is marked Hidden.")
    if not d.parsed:
        lines.append("(The task's XML could not be read, so principal/actions are unknown.)")
    return "\n".join(lines)
