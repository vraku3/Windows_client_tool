"""Audit-policy subcategories, addressed by GUID rather than by name.

`auditpol` prints localized names, so nothing here matches on them: a
subcategory is its GUID, and the setting is read from the `/r` CSV. The GUIDs
were re-listed on this machine with `auditpol /list /subcategory:* /v`, which
answers unelevated; reading a setting does not (error 0x522) and is reported
as a refusal, never as "not audited". No PyQt6.
"""
import csv
import io
import logging
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TIMEOUT_S = 15
_NEEDS_ADMIN = "needs administrator rights"
_INCLUSION_COLUMN = 4  # Machine, Target, Subcategory, GUID, Inclusion, Exclusion

Run = Callable[[List[str]], Tuple[int, str, str]]
Setting = Tuple[bool, bool]


@dataclass(frozen=True)
class Subcategory:
    guid: str
    name: str  # English display name, for the UI only
    want_success: bool
    want_failure: bool
    default: str  # "No Auditing" | "Success" | "Failure" | "Success and Failure"


SUBCATEGORIES = (
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


def _flags(text: str) -> Optional[Setting]:
    low = text.strip().lower()
    if low == "no auditing":
        return (False, False)
    success, failure = "success" in low, "failure" in low
    return (success, failure) if (success or failure) else None


def default_flags(sub: Subcategory) -> Setting:
    flags = _flags(sub.default)
    if flags is None:
        raise ValueError(f"{sub.name}: unknown default {sub.default!r}")
    return flags


def parse_r(csv_text: str) -> Optional[Setting]:
    """(success, failure) from the `/r` CSV, by column position.

    The header is localized, so the Inclusion Setting column is the fifth.
    Text that is not one of the four known settings is None, never a guess.
    """
    rows = [r for r in csv.reader(io.StringIO(csv_text)) if r]
    if len(rows) < 2 or len(rows[-1]) <= _INCLUSION_COLUMN:
        return None
    return _flags(rows[-1][_INCLUSION_COLUMN])


def _default_run(args: List[str]) -> Tuple[int, str, str]:
    p = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=_TIMEOUT_S, creationflags=subprocess.CREATE_NO_WINDOW)
    return p.returncode, p.stdout or "", p.stderr or ""


def _refused(text: str) -> bool:
    low = text.lower()
    return ("0x00000522" in low or "privilege" in low
            or "access is denied" in low)


def read(sub: Subcategory,
         run: Optional[Run] = None) -> Tuple[Optional[Setting], Optional[str]]:
    """(setting, None) or (None, why it could not be read). Never raises."""
    args = ["auditpol", "/get", f"/subcategory:{sub.guid}", "/r"]
    try:
        rc, out, err = (run or _default_run)(args)
    except (subprocess.TimeoutExpired, TimeoutError):
        return None, "auditpol timed out"
    except OSError as e:
        logger.warning("audit_policy: auditpol could not run: %s", e)
        return None, f"auditpol could not run: {e}"
    if _refused(f"{out}\n{err}"):
        return None, _NEEDS_ADMIN
    setting = parse_r(out)
    if setting is None:
        logger.warning("audit_policy: unrecognised auditpol output for %s (rc=%s)",
                       sub.name, rc)
        return None, f"auditpol answered with a setting this app does not recognise (exit {rc})"
    return setting, None


def set_command(sub: Subcategory, success: bool, failure: bool) -> str:
    """Constant-argument command built only from the GUID table."""
    def word(on: bool) -> str:
        return "enable" if on else "disable"

    return (f"auditpol /set /subcategory:{sub.guid} "
            f"/success:{word(success)} /failure:{word(failure)}")


def on_flags(sub: Subcategory) -> Setting:
    """What is wanted, plus whatever Windows already enables by default."""
    d_success, d_failure = default_flags(sub)
    return (sub.want_success or d_success, sub.want_failure or d_failure)
