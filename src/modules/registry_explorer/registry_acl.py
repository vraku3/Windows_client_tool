"""Who can write to a registry key -- the question regedit answers only
through a multi-click "Permissions..." dialog and this pane never asked at
all. Qt-free, read-only: `GetNamedSecurityInfo` never mutates anything.

Measured on this real machine (unelevated), against
``HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run``:

- **The hive name in the security path is not the `winreg` hive name.**
  `GetNamedSecurityInfo` wants ``MACHINE\\...`` for HKLM, ``USERS\\...`` for
  HKU, ``CLASSES_ROOT\\...`` for HKCR, ``CURRENT_USER\\...`` for HKCU, and --
  the one that does not follow the pattern -- ``CONFIG\\...`` for HKCC, not
  ``CURRENT_CONFIG``. Guessed at first from the other four names and it
  raised ``ERROR_INVALID_PARAMETER`` (87) on every HKCC path tried.
- **Half of a real key's ACEs do not apply to the key itself.** The DACL on
  Run carries 11 entries, but every other one carries `INHERIT_ONLY_ACE`
  (0x8): Windows stores the ACE that will apply to future subkeys right next
  to the one that applies here, and reporting both as "can write here" would
  double every trustee and add rights nothing actually grants on this
  object. Effective access on THIS key excludes any ACE flagged inherit-only.
- **`LookupAccountSid` refuses a well-known SID it cannot resolve** (measured:
  `CREATOR OWNER`, error 1332 "No mapping between account names and security
  IDs was done") even though the SID is a completely ordinary, well-known
  one. A small local table covers the handful of well-known SIDs that show up
  on registry ACLs; anything else falls back to the raw SID string rather
  than guessing a name or dropping the entry.
- **A refused read (`HKLM\\SAM\\SAM`, unelevated) is `ERROR_ACCESS_DENIED`
  through the exact same call** -- reported as its own state, never as
  "nobody can write here".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# HKEY_* name (as used elsewhere in this module) -> the prefix
# GetNamedSecurityInfo expects in its object-name string. HKEY_CURRENT_CONFIG
# is the one exception to the otherwise-consistent "drop HKEY_" pattern.
_SECURITY_PREFIX = {
    "HKEY_LOCAL_MACHINE": "MACHINE",
    "HKEY_CURRENT_USER": "CURRENT_USER",
    "HKEY_CLASSES_ROOT": "CLASSES_ROOT",
    "HKEY_USERS": "USERS",
    "HKEY_CURRENT_CONFIG": "CONFIG",
}

# Well-known SIDs LookupAccountSid has been observed to refuse outright
# (error 1332) despite being perfectly ordinary and common on registry ACLs.
_WELL_KNOWN_SID_NAMES = {
    "S-1-1-0": "Everyone",
    "S-1-3-0": "CREATOR OWNER",
    "S-1-3-1": "CREATOR GROUP",
    "S-1-5-18": "NT AUTHORITY\\SYSTEM",
}

# winnt.h registry-key access bits that constitute "can change something
# here": setting a value, creating/deleting a subkey, deleting the key
# itself, or rewriting who is allowed to (WRITE_DAC/WRITE_OWNER). The two
# GENERIC_* bits are how "Full Control" actually appears on a raw mask.
KEY_SET_VALUE = 0x0002
KEY_CREATE_SUB_KEY = 0x0004
DELETE = 0x00010000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
GENERIC_ALL = 0x10000000
GENERIC_WRITE = 0x40000000
WRITE_MASK = (KEY_SET_VALUE | KEY_CREATE_SUB_KEY | DELETE | WRITE_DAC
              | WRITE_OWNER | GENERIC_ALL | GENERIC_WRITE)

# ACE flags (winnt.h): an ACE marked inherit-only does not grant (or deny)
# access on the object carrying it -- only on subkeys created under it later.
_INHERIT_ONLY_ACE = 0x8
# ACE types (winnt.h)
_ACCESS_DENIED_ACE_TYPE = 1


def to_security_path(full_path: str) -> Optional[str]:
    """"HKEY_LOCAL_MACHINE\\SOFTWARE\\..." -> "MACHINE\\SOFTWARE\\...", or
    None for a hive GetNamedSecurityInfo has no name for."""
    parts = full_path.strip().strip("\\").split("\\", 1)
    prefix = _SECURITY_PREFIX.get(parts[0].upper())
    if prefix is None:
        return None
    return prefix if len(parts) == 1 else f"{prefix}\\{parts[1]}"


@dataclass
class Writer:
    trustee: str
    allowed: bool   # False = an explicit DENY ACE for write access
    mask: int


@dataclass
class WriteAccessResult:
    owner: Optional[str] = None
    writers: List[Writer] = field(default_factory=list)
    refused: Optional[str] = None


def is_available() -> tuple[bool, str]:
    try:
        __import__("win32security")
    except ImportError:
        return False, "pywin32 is not installed, so key permissions cannot be read."
    return True, ""


def _lookup_name(sid, lookup: Callable) -> str:
    import win32security

    try:
        name, domain, _kind = lookup(None, sid)
        return f"{domain}\\{name}" if domain else name
    except Exception:                                   # noqa: BLE001
        sid_string = win32security.ConvertSidToStringSid(sid)
        return _WELL_KNOWN_SID_NAMES.get(sid_string, sid_string)


def describe_write_access(full_path: str, *,
                           get_security_info: Optional[Callable] = None,
                           lookup_account_sid: Optional[Callable] = None) -> WriteAccessResult:
    """Owner + every trustee whose EFFECTIVE (non-inherit-only) access on this
    key includes a write-capable right, allow or deny. `refused` carries the
    reason when the DACL itself could not be read -- never collapsed into
    "no one can write here"."""
    security_path = to_security_path(full_path)
    if security_path is None:
        return WriteAccessResult(refused=f"no security-name mapping for hive in {full_path!r}")

    try:
        import win32security
    except ImportError:
        return WriteAccessResult(refused="pywin32 is not installed")

    get_info = get_security_info or win32security.GetNamedSecurityInfo
    lookup = lookup_account_sid or win32security.LookupAccountSid
    obj_type = win32security.SE_REGISTRY_KEY
    info_flags = win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION

    try:
        sd = get_info(security_path, obj_type, info_flags)
    except Exception as e:                               # noqa: BLE001
        return WriteAccessResult(refused=str(e))

    owner_name: Optional[str] = None
    try:
        owner_sid = sd.GetSecurityDescriptorOwner()
        owner_name = _lookup_name(owner_sid, lookup)
    except Exception as e:                               # noqa: BLE001
        logger.warning("describe_write_access: could not read owner of %s: %s", full_path, e)

    writers: List[Writer] = []
    try:
        dacl = sd.GetSecurityDescriptorDacl()
    except Exception as e:                               # noqa: BLE001
        return WriteAccessResult(owner=owner_name,
                                  refused=f"owner readable but DACL was not: {e}")
    if dacl is None:
        # A None DACL means "everyone" per Windows' own semantics -- distinct
        # from a DACL we failed to fetch, so it is not folded into `refused`.
        return WriteAccessResult(owner=owner_name, writers=writers)

    for i in range(dacl.GetAceCount()):
        try:
            (ace_type, ace_flags), mask, sid = dacl.GetAce(i)
        except Exception as e:                           # noqa: BLE001
            logger.warning("describe_write_access: ACE %d on %s unreadable: %s", i, full_path, e)
            continue
        if ace_flags & _INHERIT_ONLY_ACE:
            continue  # does not apply to this key, only to future subkeys
        if not (mask & WRITE_MASK):
            continue
        trustee = _lookup_name(sid, lookup)
        writers.append(Writer(trustee, allowed=(ace_type != _ACCESS_DENIED_ACE_TYPE), mask=mask))

    return WriteAccessResult(owner=owner_name, writers=writers)
