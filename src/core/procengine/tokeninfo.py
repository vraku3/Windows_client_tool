r"""A process's access token, as Process Explorer's Security tab shows it.

`details.py` already reads three facts from the token (user, integrity,
elevated) for the table. An incident responder opening one process wants
the rest of it: every group with its flags -- above all which ones are
DENY-ONLY, because that is what makes a filtered admin token not an admin
-- and every privilege with whether it is ENABLED, which is the question
"can this process open any other process on the machine" turns on.

Measured on this machine, unelevated (2026-10-09):

- **The token answers for 198 of 339 processes** with only
  `PROCESS_QUERY_LIMITED_INFORMATION` + `TOKEN_QUERY` (141 refused the
  process open, 1 refused the token). The Security tab this replaces
  opened with `PROCESS_QUERY_INFORMATION`, which more processes refuse,
  and then told every refusal "(Requires elevated privileges)".
- **An unelevated caller CAN read its own user's ELEVATED tokens.** Six
  processes here run Full-elevation (`TokenElevationType` 2) and all six
  were readable -- the token's DACL grants the owner SID, and the split
  admin token shares it. So a High-integrity process in our session is not
  a blind spot.
- **Four of those six hold `SeDebugPrivilege` ENABLED**: two Gigabyte
  utilities (`GCC.exe`, `GBT_DL_LIB.exe`), two `pythonw.exe` and Task
  Manager. An enabled SeDebugPrivilege opens every process on the machine,
  protected ones aside -- which is why `POWERFUL_PRIVILEGES` exists: it is
  the short list worth reading first, stated as capability, not as guilt.
- **Limited tokens list `BUILTIN\Administrators` as `0x10` -- USE FOR DENY
  ONLY.** Shown as plain group membership, every UAC-filtered process of an
  admin user reads as running "as Administrators", which it is not.

A refusal is reported with its reason, never as an empty group list: a
token with no groups does not exist.

Qt-free; `pywin32` for the structured reads, ctypes for the one class
pywin32 does not implement (`TokenIsAppContainer`, 29 -- measured: it
raises NotImplementedError).
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008

# SID_AND_ATTRIBUTES.Attributes for groups (winnt.h).
SE_GROUP_MANDATORY = 0x00000001
SE_GROUP_ENABLED_BY_DEFAULT = 0x00000002
SE_GROUP_ENABLED = 0x00000004
SE_GROUP_OWNER = 0x00000008
SE_GROUP_USE_FOR_DENY_ONLY = 0x00000010
SE_GROUP_INTEGRITY = 0x00000020
SE_GROUP_INTEGRITY_ENABLED = 0x00000040
SE_GROUP_RESOURCE = 0x20000000
SE_GROUP_LOGON_ID = 0xC0000000

# LUID_AND_ATTRIBUTES.Attributes for privileges.
SE_PRIVILEGE_ENABLED_BY_DEFAULT = 0x00000001
SE_PRIVILEGE_ENABLED = 0x00000002

_TokenIsAppContainer = 29

ELEVATION_TYPES = {1: "Default", 2: "Full", 3: "Limited"}

#: Privileges that, ENABLED, let a process reach past its own account:
#: open any process, act as the OS, load kernel code, read or overwrite
#: any file regardless of its ACL, or mint/assume tokens. Present but
#: disabled they are a step away, which the table still shows; enabled is
#: what the summary counts.
POWERFUL_PRIVILEGES = frozenset({
    "SeDebugPrivilege",
    "SeTcbPrivilege",
    "SeLoadDriverPrivilege",
    "SeBackupPrivilege",
    "SeRestorePrivilege",
    "SeTakeOwnershipPrivilege",
    "SeAssignPrimaryTokenPrivilege",
    "SeCreateTokenPrivilege",
    "SeImpersonatePrivilege",
    "SeSystemEnvironmentPrivilege",
    "SeManageVolumePrivilege",
    "SeRelabelPrivilege",
})


@dataclass(frozen=True)
class TokenGroup:
    name: str
    sid: str
    attributes: int

    @property
    def deny_only(self) -> bool:
        return bool(self.attributes & SE_GROUP_USE_FOR_DENY_ONLY)

    def flags(self) -> str:
        """Process Explorer's wording for the attribute bits."""
        return describe_group_flags(self.attributes)


@dataclass(frozen=True)
class TokenPrivilege:
    name: str
    attributes: int

    @property
    def enabled(self) -> bool:
        return bool(self.attributes & SE_PRIVILEGE_ENABLED)

    @property
    def default_enabled(self) -> bool:
        return bool(self.attributes & SE_PRIVILEGE_ENABLED_BY_DEFAULT)

    @property
    def powerful(self) -> bool:
        return self.name in POWERFUL_PRIVILEGES


@dataclass
class TokenReport:
    """What the token said, and what it would not say.

    `error` set means the token could not be opened at all and every other
    field is unknown. Individual classes that fail leave their own field
    `None` and add a line to `gaps` -- one refused class does not erase the
    others.
    """

    pid: int
    error: Optional[str] = None
    user: Optional[str] = None
    user_sid: Optional[str] = None
    session: Optional[int] = None
    elevation_type: Optional[str] = None
    virtualization_allowed: Optional[bool] = None
    virtualization_enabled: Optional[bool] = None
    app_container: Optional[bool] = None
    groups: Optional[List[TokenGroup]] = None
    privileges: Optional[List[TokenPrivilege]] = None
    gaps: List[str] = field(default_factory=list)

    @property
    def readable(self) -> bool:
        return self.error is None

    def enabled_powerful(self) -> List[str]:
        """The powerful privileges this token has switched ON."""
        return sorted(p.name for p in (self.privileges or ())
                      if p.enabled and p.powerful)

    def deny_only_groups(self) -> List[str]:
        return [g.name for g in (self.groups or ()) if g.deny_only]


def describe_group_flags(attributes: int) -> str:
    """'Mandatory, Owner' / 'Deny' / 'Integrity' / 'Logon SID' ..."""
    if attributes & SE_GROUP_INTEGRITY:
        return "Integrity"
    parts = []
    if attributes & SE_GROUP_USE_FOR_DENY_ONLY:
        parts.append("Deny")
    if attributes & SE_GROUP_MANDATORY:
        parts.append("Mandatory")
    if attributes & SE_GROUP_OWNER:
        parts.append("Owner")
    if (attributes & SE_GROUP_LOGON_ID) == SE_GROUP_LOGON_ID:
        parts.append("Logon SID")
    if attributes & SE_GROUP_RESOURCE:
        parts.append("Resource")
    if not parts and not attributes & SE_GROUP_ENABLED:
        parts.append("Disabled")
    return ", ".join(parts)


# ---- the read ---------------------------------------------------------

_name_cache: Dict[str, str] = {}


def read_token(pid: int) -> TokenReport:
    """Everything the token of `pid` will tell an unelevated caller.

    Never raises. Measured ~3.6 ms a process here, most of it SID name
    lookups, which are cached per SID for the life of the process.
    """
    try:
        import pywintypes
        import win32api
        import win32security
    except ImportError as error:  # pragma: no cover - pywin32 is required
        return TokenReport(pid=pid, error=f"pywin32 unavailable: {error}")

    try:
        process = win32api.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except pywintypes.error as error:
        return TokenReport(pid=pid, error=_why(error, "the process"))
    try:
        token = win32security.OpenProcessToken(process, TOKEN_QUERY)
    except pywintypes.error as error:
        return TokenReport(pid=pid, error=_why(error, "its token"))
    finally:
        win32api.CloseHandle(process)
    try:
        return _read_open_token(pid, token, win32security, pywintypes)
    finally:
        win32api.CloseHandle(token)


def _read_open_token(pid, token, win32security, pywintypes) -> TokenReport:
    report = TokenReport(pid=pid)

    def ask(kind, label):
        try:
            return win32security.GetTokenInformation(token, kind)
        except pywintypes.error as error:
            report.gaps.append(f"{label}: {_why(error, label)}")
            return None

    user = ask(win32security.TokenUser, "user")
    if user is not None:
        report.user_sid = win32security.ConvertSidToStringSid(user[0])
        report.user = _sid_name(user[0], report.user_sid, win32security)
    report.session = ask(win32security.TokenSessionId, "session")
    elevation = ask(win32security.TokenElevationType, "elevation type")
    if elevation is not None:
        report.elevation_type = ELEVATION_TYPES.get(elevation,
                                                    f"type {elevation}")
    allowed = ask(win32security.TokenVirtualizationAllowed, "virtualization")
    enabled = ask(win32security.TokenVirtualizationEnabled, "virtualization")
    report.virtualization_allowed = None if allowed is None else bool(allowed)
    report.virtualization_enabled = None if enabled is None else bool(enabled)
    report.app_container = _is_app_container(token)

    groups = ask(win32security.TokenGroups, "groups")
    if groups is not None:
        report.groups = [_group(sid, attrs, win32security)
                         for sid, attrs in groups]
    privileges = ask(win32security.TokenPrivileges, "privileges")
    if privileges is not None:
        report.privileges = sorted(
            (_privilege(luid, attrs, win32security)
             for luid, attrs in privileges),
            key=lambda p: p.name.lower())
    return report


def _group(sid, attributes, win32security) -> TokenGroup:
    text = win32security.ConvertSidToStringSid(sid)
    return TokenGroup(name=_sid_name(sid, text, win32security), sid=text,
                      attributes=attributes & 0xFFFFFFFF)


def _privilege(luid, attributes, win32security) -> TokenPrivilege:
    try:
        name = win32security.LookupPrivilegeName(None, luid)
    except Exception as error:  # noqa: BLE001 - name it by its LUID instead
        logger.debug("LookupPrivilegeName(%r) failed: %s", luid, error)
        name = f"LUID {luid}"
    return TokenPrivilege(name=name, attributes=attributes & 0xFFFFFFFF)


def _sid_name(sid, text: str, win32security) -> str:
    """DOMAIN\\name for a SID, or the SID string where it has none.

    Logon SIDs (S-1-5-5-x-y) and many capability SIDs have no account name;
    LookupAccountSid failing for them is the normal case, so the SID itself
    is the honest label.
    """
    cached = _name_cache.get(text)
    if cached is not None:
        return cached
    try:
        name, domain, _kind = win32security.LookupAccountSid(None, sid)
        label = f"{domain}\\{name}" if domain else name
    except Exception as error:  # noqa: BLE001 - unnamed SIDs are normal
        logger.debug("No account name for %s: %s", text, error)
        label = text
    _name_cache[text] = label or text
    return _name_cache[text]


def _is_app_container(token) -> Optional[bool]:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    value = wintypes.DWORD(0)
    size = wintypes.DWORD(0)
    ok = advapi32.GetTokenInformation(
        int(token), _TokenIsAppContainer, ctypes.byref(value),
        ctypes.sizeof(value), ctypes.byref(size))
    if not ok:
        logger.debug("TokenIsAppContainer refused: %d", ctypes.get_last_error())
        return None
    return bool(value.value)


def _why(error, what: str) -> str:
    text = (getattr(error, "strerror", None) or str(error)).rstrip(".")
    code = getattr(error, "winerror", None)
    return f"{what} could not be opened — {text}" + (
        f" (error {code})" if code else "")


def summarize(report: TokenReport) -> List[Tuple[str, str]]:
    """The header lines of the Security view, as (label, value) pairs."""
    if not report.readable:
        return [("Token", report.error or "unreadable")]

    def known(value, fmt=str):
        return "—" if value is None else fmt(value)

    def yes_no(value):
        return "Yes" if value else "No"

    virt = "—"
    if report.virtualization_allowed is not None:
        virt = ("Enabled" if report.virtualization_enabled else
                "Allowed, off" if report.virtualization_allowed else
                "Not allowed")
    powerful = report.enabled_powerful()
    return [
        ("User", known(report.user)),
        ("User SID", known(report.user_sid)),
        ("Session", known(report.session)),
        ("Elevation", known(report.elevation_type)),
        ("Virtualization", virt),
        ("AppContainer", known(report.app_container, yes_no)),
        ("Powerful privileges enabled",
         ", ".join(powerful) if powerful else
         ("none" if report.privileges is not None else "—")),
    ]
