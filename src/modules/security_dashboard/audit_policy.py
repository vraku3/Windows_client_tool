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
_PRIVILEGE_NOT_HELD = 1314
_NEEDS_ADMIN ="needs administrator rights"
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


_EXACT = {"no auditing": (False, False), "success": (True, False),
          "failure": (False, True), "success and failure": (True, True)}


def _flags(text: str) -> Optional[Setting]:
    return _EXACT.get(text.strip().lower())


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
    text = _inclusion_text(csv_text)
    return None if text is None else _flags(text)


def _inclusion_text(csv_text: str) -> Optional[str]:
    rows = [r for r in csv.reader(io.StringIO(csv_text.lstrip("﻿")))
            if any(c.strip() for c in r)]
    if len(rows) < 2 or len(rows[-1]) <= _INCLUSION_COLUMN:
        return None
    return rows[-1][_INCLUSION_COLUMN]


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
    if rc == _PRIVILEGE_NOT_HELD or _refused(f"{out}\n{err}"):
        return None, _NEEDS_ADMIN
    if rc != 0:
        return None, f"auditpol exited {rc}"
    setting = parse_r(out)
    if setting is None:
        logger.warning("audit_policy: unrecognised auditpol output for %s: %r",
                       sub.name, out[-200:])
        return None, ("auditpol answered with a setting this app does not "
                      f"recognise: {_inclusion_text(out)!r}")
    return setting, None


def switches(success: bool, failure: bool) -> str:
    """The exact `/success:X /failure:Y` pair for a setting -- one of REVERT_PAIRS."""
    def word(on: bool) -> str:
        return "enable" if on else "disable"

    return f"/success:{word(success)} /failure:{word(failure)}"


#: The only four strings a revert may put after the GUID. A captured value is
#: checked against this set before it gets anywhere near a shell.
REVERT_PAIRS = frozenset(switches(s, f) for s in (True, False) for f in (True, False))


def set_command(sub: Subcategory, success: bool, failure: bool) -> str:
    """Constant-argument command built only from the GUID table: sets BOTH flags."""
    return f"auditpol /set /subcategory:{sub.guid} {switches(success, failure)}"


def enable_command(sub: Subcategory) -> str:
    """Turn ON what is wanted and say nothing about the rest.

    `auditpol` leaves a flag it is not given exactly as it is, so a machine
    already auditing Failure keeps Failure when Success is added. Never
    `:disable` -- applying a recommendation must not switch any auditing off.
    """
    parts = [f"auditpol /set /subcategory:{sub.guid}"]
    if sub.want_success:
        parts.append("/success:enable")
    if sub.want_failure:
        parts.append("/failure:enable")
    return " ".join(parts)


def current_pair(sub: Subcategory, run: Optional[Run] = None) -> Optional[str]:
    """The setting right now as one of REVERT_PAIRS, or None if it cannot be read."""
    setting, why = read(sub, run)
    if setting is None:
        logger.info("audit_policy: %s cannot be captured for revert: %s", sub.name, why)
        return None
    pair = switches(*setting)
    return pair if pair in REVERT_PAIRS else None
