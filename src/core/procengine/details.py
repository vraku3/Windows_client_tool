r"""The per-process facts that cost a handle open.

Path, command line, user, integrity, elevation, architecture, description --
none arrive with the bulk syscall, and each one needs the process opened.
Doing that for every process every tick is exactly the 668 ms
`ntquery.py` exists to avoid.

So they are COLD: resolved once, cached for the life of the process, keyed by
`(pid, create_time)` because Windows reuses pids and serving the dead
process's path for its successor is how a process manager shows the wrong
program's name.

**A refusal is not an answer.** The rule the Security Dashboard, Group Policy
and the tweak engine all follow, applied here: a value we could not read is
`None` with a reason beside it, never `""`. A blank cell reads as "this
process has no path"; "access denied" reads as what actually happened. This
matters more than usual here because plenty of processes on a normal machine
are unreadable to an unelevated caller, and a pane full of blanks looks
broken rather than restricted.

Qt-free, like the rest of the engine.
"""
import ctypes
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
ERROR_INSUFFICIENT_BUFFER = 122

# GetTokenInformation classes.
_TokenUser = 1
_TokenIsAppContainer = 29
_TokenElevation = 20
_TokenIntegrityLevel = 25

# The RID at the top of an integrity-level SID.
_INTEGRITY_LEVELS = [
    (0x0000, "Untrusted"),
    (0x1000, "Low"),
    (0x2000, "Medium"),
    (0x2100, "Medium Plus"),
    (0x3000, "High"),
    (0x4000, "System"),
    (0x5000, "Protected"),
]

_IMAGE_FILE_MACHINE = {
    0x8664: "x64",
    0x014C: "x86",
    0xAA64: "ARM64",
    0x01C4: "ARM",
}

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

# Every prototype is declared, and the pointer-returning ones are not
# optional: ctypes assumes a function returns `c_int`, so on x64 an
# undeclared pointer return is TRUNCATED TO 32 BITS. It does not fail
# loudly -- `GetSidSubAuthority` handed back a half pointer and the process
# died with an access violation inside a test run. Anything returning a
# handle or a pointer needs its restype set here.
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                  wintypes.DWORD]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD)]
_kernel32.IsWow64Process2.restype = wintypes.BOOL
_kernel32.IsWow64Process2.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT),
    ctypes.POINTER(wintypes.USHORT)]

_advapi32.OpenProcessToken.restype = wintypes.BOOL
_advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_advapi32.GetTokenInformation.restype = wintypes.BOOL
_advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD)]
_advapi32.LookupAccountSidW.restype = wintypes.BOOL
_advapi32.LookupAccountSidW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
# These two return pointers INTO the SID. Truncated, they crash the process.
_advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
_advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
_advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
_advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]


@dataclass(frozen=True, slots=True)
class ProcessDetails:
    """What we could learn about one process, and what we could not.

    Every unknown is `None`. Each field that can be refused carries its own
    `_error`, because being refused the path and being refused the token are
    different facts and collapsing them loses which one happened.
    """

    pid: int
    path: Optional[str] = None
    path_error: Optional[str] = None
    cmdline: Optional[str] = None
    cmdline_error: Optional[str] = None
    user: Optional[str] = None
    user_error: Optional[str] = None
    integrity: Optional[str] = None
    elevated: Optional[bool] = None
    #: Whether the process token is an AppContainer token -- a distinct
    #: fact from integrity or elevation, and from `classify.is_immersive`
    #: (a package identity). Measured on this machine: 13 of 348 processes
    #: are AppContainer-sandboxed, and `msedge.exe` itself is one of them
    #: while carrying NO package family at all -- a desktop program, not a
    #: Store app, that still runs its renderer inside the sandbox boundary.
    #: `None` means the token could not be read, same as every other field
    #: here.
    appcontainer: Optional[bool] = None
    architecture: Optional[str] = None
    description: Optional[str] = None
    company: Optional[str] = None


def resolve(pid: int) -> ProcessDetails:
    """Everything we can learn about `pid` right now.

    Never raises: a process ending between the snapshot and this call is the
    normal case on a busy machine, not an error.
    """
    handle = _kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        reason = _reason(ctypes.get_last_error())
        return ProcessDetails(
            pid=pid, path_error=reason, cmdline_error=reason,
            user_error=reason)

    try:
        path, path_error = _image_path(handle)
        cmdline, cmdline_error = _command_line(pid)
        user, user_error, integrity, elevated, appcontainer = _token_facts(handle)
        description, company = _version_info(path)
        return ProcessDetails(
            pid=pid,
            path=path, path_error=path_error,
            cmdline=cmdline, cmdline_error=cmdline_error,
            user=user, user_error=user_error,
            integrity=integrity, elevated=elevated,
            appcontainer=appcontainer,
            architecture=_architecture(handle),
            description=description, company=company,
        )
    finally:
        _kernel32.CloseHandle(handle)


class DetailCache:
    """`resolve` once per process, then never again.

    Keyed by `(pid, create_time)`: the create time is what distinguishes a
    reused pid from the process that held it before, and without it this
    cache is a machine for showing stale names.
    """

    def __init__(self) -> None:
        self._entries: Dict[Tuple[int, int], ProcessDetails] = {}

    def tracked(self) -> int:
        return len(self._entries)

    def get(self, pid: int, create_time: int,
            budget: Optional[List[int]] = None) -> ProcessDetails:
        """The cold details for one process, resolving them if needed.

        `budget` is an optional one-element list used as a mutable counter,
        decremented on every resolution actually performed. At zero, this
        returns an UNRESOLVED `ProcessDetails` -- every field `None` -- and
        does not cache it, so the next tick tries again.

        The counter exists because the cold sweep's cost depends entirely
        on how much the caller is allowed to see. Unelevated, half the
        processes refuse immediately and a full sweep is ~141 ms; ELEVATED,
        nothing refuses and the same sweep costs **2,252 ms**, which is a
        pane that sits empty for two seconds on the tick it is opened.
        Spreading that over a few ticks shows names and rates at once and
        fills the paths in behind them.

        An unresolved record is not a refused one, and neither is a lie:
        both are `None` with no claim attached.
        """
        key = (pid, create_time)
        found = self._entries.get(key)
        if found is not None:
            return found
        if budget is not None:
            if budget[0] <= 0:
                return ProcessDetails(pid=pid)
            budget[0] -= 1
        found = resolve(pid)
        self._entries[key] = found
        return found

    def retain(self, live_pids: Set[int]) -> None:
        """Drop everything for processes that are gone.

        A machine that churns processes -- a build, a script loop -- would
        otherwise grow this dict for the life of the pane.
        """
        self._entries = {key: value for key, value in self._entries.items()
                         if key[0] in live_pids}


# ---- the individual reads ----------------------------------------------

def _image_path(handle) -> Tuple[Optional[str], Optional[str]]:
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if _kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)):
        return buffer.value, None
    return None, _reason(ctypes.get_last_error())


def _command_line(pid: int) -> Tuple[Optional[str], Optional[str]]:
    """Via psutil, which already handles the WOW64 and PEB-layout cases.

    Reading it directly means walking the target's PEB, and the layout
    differs between 32- and 64-bit targets; psutil carries that code and is
    cheap enough on a path paid once per process.
    """
    try:
        import psutil

        parts = psutil.Process(pid).cmdline()
    except Exception as error:  # noqa: BLE001 - the reason is the product
        return None, _describe(error)

    if not parts:
        # psutil hands back an EMPTY LIST here rather than raising, and
        # joining that produced `""` -- the one value this module promises
        # never to emit. It happens for the kernel processes (pid 4 and
        # its kin): they have no user-mode PEB, so there is no command
        # line to read, which is a different fact from an empty one.
        return None, "the process has no command line (kernel processes " \
                     "have no user-mode PEB to read one from)"
    return " ".join(parts), None


def _token_facts(handle):
    """User, integrity, elevation and AppContainer -- one open token, four reads."""
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
            handle, TOKEN_QUERY, ctypes.byref(token)):
        reason = _reason(ctypes.get_last_error())
        return None, reason, None, None, None
    try:
        return (_token_user(token), None, _token_integrity(token),
                _token_elevated(token), _token_appcontainer(token))
    finally:
        _kernel32.CloseHandle(token)


def _token_information(token, kind) -> Optional[bytes]:
    size = wintypes.DWORD(0)
    _advapi32.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
    if not size.value:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if not _advapi32.GetTokenInformation(
            token, kind, buffer, size, ctypes.byref(size)):
        return None
    return buffer


def _token_user(token) -> Optional[str]:
    buffer = _token_information(token, _TokenUser)
    if buffer is None:
        return None
    # TOKEN_USER is a SID_AND_ATTRIBUTES: the SID pointer comes first.
    sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    return _account_name(sid) if sid else None


def _account_name(sid) -> Optional[str]:
    name = ctypes.create_unicode_buffer(256)
    name_size = wintypes.DWORD(256)
    domain = ctypes.create_unicode_buffer(256)
    domain_size = wintypes.DWORD(256)
    use = wintypes.DWORD()
    if not _advapi32.LookupAccountSidW(
            None, ctypes.c_void_p(sid), name, ctypes.byref(name_size),
            domain, ctypes.byref(domain_size), ctypes.byref(use)):
        return None
    if domain.value:
        return f"{domain.value}\\{name.value}"
    return name.value or None


def _token_integrity(token) -> Optional[str]:
    buffer = _token_information(token, _TokenIntegrityLevel)
    if buffer is None:
        return None
    sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    if not sid:
        return None
    count_ptr = _advapi32.GetSidSubAuthorityCount(sid)
    if not count_ptr:
        return None
    count = count_ptr[0]
    if count == 0:
        return None
    # The integrity level lives in the LAST sub-authority.
    rid_ptr = _advapi32.GetSidSubAuthority(sid, count - 1)
    if not rid_ptr:
        return None
    rid = rid_ptr[0]
    # The highest named level at or below the RID: Windows defines the
    # levels as thresholds, not as an enumeration.
    label = None
    for threshold, named in _INTEGRITY_LEVELS:
        if rid >= threshold:
            label = named
    return label


def _token_elevated(token) -> Optional[bool]:
    buffer = _token_information(token, _TokenElevation)
    if buffer is None:
        return None
    return bool(ctypes.cast(buffer,
                            ctypes.POINTER(wintypes.DWORD))[0])


def _token_appcontainer(token) -> Optional[bool]:
    """Whether this token is an AppContainer token (sandboxed).

    Distinct from `classify.package_family_name` -- that answers "does this
    process have a Store package identity", this answers "does its token
    carry the sandbox boundary at all". They disagree in exactly the
    interesting direction: `msedge.exe` on this machine has NO package
    family (it is an ordinary desktop install) but IS an AppContainer
    token, because Edge sandboxes its own renderer/utility processes the
    same way a packaged app's broker would.
    """
    buffer = _token_information(token, _TokenIsAppContainer)
    if buffer is None:
        return None
    return bool(ctypes.cast(buffer,
                            ctypes.POINTER(wintypes.DWORD))[0])


def _architecture(handle) -> Optional[str]:
    """Via IsWow64Process2, which names the machine rather than answering
    the older "is it WOW64" yes/no that cannot distinguish ARM64."""
    process_machine = wintypes.USHORT()
    native_machine = wintypes.USHORT()
    try:
        ok = _kernel32.IsWow64Process2(
            handle, ctypes.byref(process_machine),
            ctypes.byref(native_machine))
    except AttributeError:
        return None
    if not ok:
        return None
    # IMAGE_FILE_MACHINE_UNKNOWN means "not running under WOW64", i.e. the
    # process is native -- so the answer is the machine's own architecture.
    if process_machine.value == 0:
        return _IMAGE_FILE_MACHINE.get(native_machine.value)
    return _IMAGE_FILE_MACHINE.get(process_machine.value)


def _version_info(path: Optional[str]):
    """Description and company from the binary's version resource.

    Plenty of real executables carry no version resource at all; that is a
    definite "there is none", so it stays `None` without an error beside it.
    """
    if not path or not os.path.exists(path):
        return None, None
    try:
        import win32api

        info = win32api.GetFileVersionInfo(path, "\\VarFileInfo\\Translation")
        if not info:
            return None, None
        lang, codepage = info[0]
        prefix = f"\\StringFileInfo\\{lang:04x}{codepage:04x}"
        return (_string_value(win32api, path, prefix, "FileDescription"),
                _string_value(win32api, path, prefix, "CompanyName"))
    except Exception as error:  # noqa: BLE001
        logger.debug("No version info for %s: %s", path, error)
        return None, None


def _string_value(win32api, path, prefix, key) -> Optional[str]:
    try:
        value = win32api.GetFileVersionInfo(path, f"{prefix}\\{key}")
    except Exception:  # noqa: BLE001 - a missing key is not an error
        return None
    # Never "": the caller cannot tell an empty string from "not known".
    return value or None


def _reason(code: int) -> str:
    """A Windows error as words. An error number is not a reason -- it goes
    in a cell someone has to read."""
    if not code:
        return "unknown error"
    try:
        return ctypes.WinError(code).strerror or f"error {code}"
    except Exception:  # noqa: BLE001
        return f"error {code}"


def _describe(error: Exception) -> str:
    text = str(error).strip()
    return text or error.__class__.__name__
