"""Read and set the audit subcategories an investigation actually needs.

`auditpol` prints subcategory names in the OS language, so anything that
matches on the text breaks the moment the machine is not English. Every read
here addresses a subcategory by its **GUID** and parses the `/r` CSV, whose
columns are positional; only the Inclusion Setting VALUE is text, and even
that is matched by containing "Success"/"Failure" rather than by equality.

The GUIDs below are Microsoft's documented advanced-audit-policy GUIDs. They
were NOT re-listed on this machine when this was written: `auditpol
/list /subcategory:* /v`, like `/get`, refuses an unelevated caller with
`Error 0x00000522 -- A required privilege is not held by the client`. That
refusal is reported, never guessed at.
"""
import csv
import io
import logging
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: auditpol's "a required privilege is not held by the client".
_ERROR_NO_PRIVILEGE = 0x522

REFUSED = "needs administrator rights"


@dataclass(frozen=True)
class Subcategory:
    guid: str            # "{0CCE922B-69AE-11D9-BED3-505054503030}"
    name: str            # English display name, for the UI only
    want_success: bool
    want_failure: bool
    default: str         # Windows client default


SUBCATEGORIES: Tuple[Subcategory, ...] = (
    Subcategory("{0CCE922B-69AE-11D9-BED3-505054503030}", "Process Creation",
                True, False, "No Auditing"),
    Subcategory("{0CCE9215-69AE-11D9-BED3-505054503030}", "Logon",
                True, True, "Success and Failure"),
    Subcategory("{0CCE9217-69AE-11D9-BED3-505054503030}", "Account Lockout",
                False, True, "Success"),
    Subcategory("{0CCE9237-69AE-11D9-BED3-505054503030}",
                "Security Group Management", True, False, "Success"),
    Subcategory("{0CCE9235-69AE-11D9-BED3-505054503030}",
                "User Account Management", True, True, "Success"),
    Subcategory("{0CCE922F-69AE-11D9-BED3-505054503030}", "Audit Policy Change",
                True, False, "Success"),
)

#: The only Inclusion Setting text that means "nothing audited". A localized
#: equivalent is NOT guessed at: on a non-English machine it is an
#: unrecognised value and `_interpret` answers None, which is honest.
_NONE_MARKERS = ("no auditing", "none")


def default_flags(default: str) -> Tuple[bool, bool]:
    lowered = default.lower()
    return ("success" in lowered, "failure" in lowered)


def _interpret(value: str) -> Optional[Tuple[bool, bool]]:
    """An Inclusion Setting cell -> (success, failure), or None if unknown."""
    lowered = value.strip().lower()
    if not lowered or lowered in _NONE_MARKERS:
        return (False, False)
    success = "success" in lowered
    failure = "failure" in lowered
    if success or failure:
        return (success, failure)
    return None


def parse_r(csv_text: str) -> Optional[Tuple[bool, bool]]:
    """The Inclusion Setting of the first data row of an `auditpol ... /r` CSV."""
    saw_header = False
    for row in csv.reader(io.StringIO(csv_text)):
        if len(row) < 5:
            continue
        value = row[4].strip()
        if not saw_header and value.lower() == "inclusion setting":
            saw_header = True
            continue
        return _interpret(value)
    return None


def _default_run(args: List[str]) -> Tuple[int, str, str]:
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=20, creationflags=_NO_WINDOW,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def read(sub: Subcategory, run: Optional[Callable] = None
         ) -> Tuple[Optional[Tuple[bool, bool]], Optional[str]]:
    """(setting, refusal). A refused read returns (None, reason)."""
    run = run or _default_run
    args = ["auditpol", "/get", f"/subcategory:{sub.guid}", "/r"]
    try:
        rc, out, err = run(args)
    except subprocess.TimeoutExpired:
        logger.warning("auditpol timed out for %s", sub.name)
        return None, "auditpol timed out"
    except OSError as exc:
        logger.warning("auditpol could not be run (%s)", exc)
        return None, f"could not run auditpol: {exc}"

    combined = f"{out}\n{err}".lower()
    if rc != 0:
        if rc == _ERROR_NO_PRIVILEGE or "privilege" in combined \
                or "access is denied" in combined:
            return None, REFUSED
        first = (err or out).strip().splitlines()
        return None, first[0].strip() if first else f"auditpol exited {rc}"

    setting = parse_r(out)
    if setting is None:
        return None, f"unrecognised Inclusion Setting in {sub.name} output"
    return setting, None


def set_command(sub: Subcategory, success: bool, failure: bool) -> str:
    """The `auditpol /set` line for one subcategory, addressed by GUID."""
    s = "enable" if success else "disable"
    f = "enable" if failure else "disable"
    return (f"auditpol /set /subcategory:{sub.guid} "
            f"/success:{s} /failure:{f}")
