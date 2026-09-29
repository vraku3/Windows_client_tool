"""Decode `Registry.pol` — the file local Group Policy actually stores.

Why this exists: `gpresult` refuses the computer half of the report unless
you are elevated, but the local policy *file* is world-readable. On a machine
that is not domain-joined, local policy is the only policy there is, so
reading the file directly fills in everything the unelevated RSOP call drops
-- with no UAC prompt at all.

Format (Microsoft calls it PReg):

    "PReg"  (4 ASCII bytes)
    version (4 bytes, little-endian, currently 1)
    then zero or more records:
    '[' key '\\0' ';' value '\\0' ';' type ';' size ';' data ']'

with `[`, `;` and `]` written as UTF-16LE characters, key and value as
null-terminated UTF-16LE strings, and type/size as 4-byte little-endian
integers.

**The type and size fields are read as exactly four bytes, never scanned for
the `;` delimiter.** A record whose data is 59 bytes long encodes its size as
`3B 00 00 00`, and `0x003B` is the UTF-16 code unit for `;` -- a delimiter
scan terminates the field early and desynchronises the rest of the file. It
would parse this machine's six records perfectly (sizes 0 and 4) and corrupt
the first real domain policy it met.
"""

from __future__ import annotations

import logging
import os
import struct
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

PREG_MAGIC = b"PReg"
PREG_VERSION = 1

#: Where Windows keeps the local GPO's registry policy.
MACHINE_POL = r"System32\GroupPolicy\Machine\Registry.pol"
USER_POL = r"System32\GroupPolicy\User\Registry.pol"

#: Multiple Local GPO -- a SEPARATE mechanism from the single machine-wide
#: local GPO above. gpedit.msc can target one specific local account, or the
#: whole Administrators/Non-Administrators group, and each target gets its
#: own `Registry.pol` under here rather than sharing the one at USER_POL.
#: This is how a shared/kiosk machine (no domain) gives different local
#: policy per local account. Confirmed live on this machine 2026-09-29: the
#: folder exists (Windows creates it regardless) but currently holds zero SID
#: subfolders -- multiple local GPO has never actually been configured here.
GROUP_POLICY_USERS_DIR = r"System32\GroupPolicyUsers"

#: gpedit's own names for the two group targets of multiple local GPO --
#: LookupAccountSid resolves S-1-5-32-544/-545 to "Administrators"/"Users"
#: (the ordinary group names), but gpedit itself calls the second one
#: "Non-Administrators" specifically in this context, so that is what is
#: shown here rather than the generic group name.
_SPECIAL_LOCAL_GPO_SIDS = {
    "S-1-5-32-544": "Administrators",
    "S-1-5-32-545": "Non-Administrators (Users)",
}

REG_NONE = 0
REG_SZ = 1
REG_EXPAND_SZ = 2
REG_BINARY = 3
REG_DWORD = 4
REG_DWORD_BIG_ENDIAN = 5
REG_LINK = 6
REG_MULTI_SZ = 7
REG_QWORD = 11

_TYPE_NAMES = {
    REG_NONE: "REG_NONE",
    REG_SZ: "REG_SZ",
    REG_EXPAND_SZ: "REG_EXPAND_SZ",
    REG_BINARY: "REG_BINARY",
    REG_DWORD: "REG_DWORD",
    REG_DWORD_BIG_ENDIAN: "REG_DWORD_BIG_ENDIAN",
    REG_LINK: "REG_LINK",
    REG_MULTI_SZ: "REG_MULTI_SZ",
    REG_QWORD: "REG_QWORD",
}

#: Value names beginning with `**` are instructions to the Registry
#: client-side extension, not values. They are kept rather than dropped --
#: "this policy deletes that value" is a fact about the machine.
_DIRECTIVES = {
    "**del.": "delete_value",
    "**delvals.": "delete_all_values",
    "**deletevalues": "delete_values",
    "**deletekeys": "delete_keys",
    "**soft.": "set_if_absent",
    "**securekey": "secure_key",
}

_OPEN = "[".encode("utf-16-le")
_SEMI = ";".encode("utf-16-le")
_CLOSE = "]".encode("utf-16-le")


class PolParseError(Exception):
    """The file is not a readable PReg stream."""


@dataclass
class PolicyValue:
    key: str = ""
    value_name: str = ""
    type_id: int = 0
    data: Any = None
    raw: bytes = b""
    directive: str = ""

    @property
    def type_name(self) -> str:
        return _TYPE_NAMES.get(self.type_id, "REG_TYPE_%d" % self.type_id)

    @property
    def full_path(self) -> str:
        """`key\\value`, the way the setting is identified everywhere else."""
        return "%s\\%s" % (self.key, self.value_name) if self.value_name else self.key

    def display(self) -> str:
        """The value as a person would read it."""
        if self.directive:
            return "(%s)" % self.directive.replace("_", " ")
        if self.data is None:
            return ""
        if isinstance(self.data, bytes):
            return self.data.hex()
        if isinstance(self.data, list):
            return ", ".join(self.data)
        return str(self.data)


@dataclass
class PolFile:
    path: str = ""
    scope: str = ""          # "Computer" or "User"
    hive: str = ""           # "HKLM" or "HKCU"
    exists: bool = False
    values: List[PolicyValue] = field(default_factory=list)
    error: str = ""

    @property
    def settings(self) -> List[PolicyValue]:
        """Real values, with the CSE directives filtered out."""
        return [v for v in self.values if not v.directive]


def _read_sz(data: bytes, pos: int) -> Tuple[str, int]:
    """A null-terminated UTF-16LE string starting at `pos`."""
    end = pos
    while True:
        if end + 2 > len(data):
            raise PolParseError("string at offset %d is not terminated" % pos)
        if data[end:end + 2] == b"\x00\x00":
            break
        end += 2
    return data[pos:end].decode("utf-16-le", errors="replace"), end + 2


def _expect(data: bytes, pos: int, token: bytes, what: str) -> int:
    if data[pos:pos + 2] != token:
        raise PolParseError(
            "expected %s at offset %d, found %r"
            % (what, pos, data[pos:pos + 2]))
    return pos + 2


def _decode(type_id: int, raw: bytes) -> Any:
    """Registry data as a Python value; the raw bytes are kept alongside."""
    try:
        if type_id in (REG_SZ, REG_EXPAND_SZ, REG_LINK):
            return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        if type_id == REG_MULTI_SZ:
            text = raw.decode("utf-16-le", errors="replace")
            return [part for part in text.split("\x00") if part]
        if type_id == REG_DWORD and len(raw) >= 4:
            return struct.unpack("<I", raw[:4])[0]
        if type_id == REG_DWORD_BIG_ENDIAN and len(raw) >= 4:
            return struct.unpack(">I", raw[:4])[0]
        if type_id == REG_QWORD and len(raw) >= 8:
            return struct.unpack("<Q", raw[:8])[0]
    except (struct.error, UnicodeDecodeError):
        # Falling through to the raw bytes is right: a value we cannot decode
        # is still a value that is set, and hiding it would understate what
        # the policy does.
        return raw
    return raw


def _directive_for(value_name: str) -> str:
    lowered = value_name.lower()
    for prefix, name in _DIRECTIVES.items():
        if lowered.startswith(prefix) or lowered == prefix.rstrip("."):
            return name
    return ""


def parse_pol_bytes(data: bytes) -> List[PolicyValue]:
    """Every record in a PReg stream. Raises `PolParseError` on a bad file."""
    if len(data) < 8:
        raise PolParseError("file is too short to be a PReg stream")
    if data[:4] != PREG_MAGIC:
        raise PolParseError("not a PReg file (magic is %r)" % data[:4])
    version = struct.unpack("<I", data[4:8])[0]
    if version != PREG_VERSION:
        raise PolParseError("unsupported PReg version %d" % version)

    values: List[PolicyValue] = []
    pos = 8
    while pos < len(data):
        # Some writers pad the tail; stop cleanly rather than complaining.
        if data[pos:pos + 2] != _OPEN:
            if data[pos:].strip(b"\x00") == b"":
                break
            raise PolParseError(
                "expected a record at offset %d, found %r"
                % (pos, data[pos:pos + 2]))
        pos += 2

        key, pos = _read_sz(data, pos)
        pos = _expect(data, pos, _SEMI, "';' after the key")
        value_name, pos = _read_sz(data, pos)
        pos = _expect(data, pos, _SEMI, "';' after the value name")

        # Four raw bytes, not a delimiter scan -- see the module docstring.
        if pos + 4 > len(data):
            raise PolParseError("truncated type field at offset %d" % pos)
        type_id = struct.unpack("<I", data[pos:pos + 4])[0]
        pos = _expect(data, pos + 4, _SEMI, "';' after the type")

        if pos + 4 > len(data):
            raise PolParseError("truncated size field at offset %d" % pos)
        size = struct.unpack("<I", data[pos:pos + 4])[0]
        pos = _expect(data, pos + 4, _SEMI, "';' after the size")

        if pos + size > len(data):
            raise PolParseError(
                "record at offset %d claims %d bytes of data, only %d remain"
                % (pos, size, len(data) - pos))
        raw = data[pos:pos + size]
        pos += size
        pos = _expect(data, pos, _CLOSE, "']' closing the record")

        values.append(PolicyValue(
            key=key,
            value_name=value_name,
            type_id=type_id,
            data=_decode(type_id, raw),
            raw=raw,
            directive=_directive_for(value_name),
        ))
    return values


def read_pol_file(path: str, scope: str = "", hive: str = "") -> PolFile:
    """Read one `Registry.pol`. A missing file is not an error.

    "The file is not there" is the normal state of a machine with no local
    policy of that kind, and it must not read as a failure -- that is the
    same distinction the RSOP side draws between "refused" and "empty".
    """
    result = PolFile(path=path, scope=scope, hive=hive)
    if not os.path.exists(path):
        return result
    result.exists = True
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        result.error = "Could not read %s: %s" % (path, exc)
        return result

    if not data:
        # A zero-byte Registry.pol is what Windows leaves behind when every
        # setting in a scope is set back to Not Configured.
        return result
    try:
        result.values = parse_pol_bytes(data)
    except PolParseError as exc:
        result.error = "Could not parse %s: %s" % (path, exc)
    return result


def local_policy_files(system_root: Optional[str] = None) -> List[PolFile]:
    """The machine's own local GPO, both scopes, read without elevation."""
    root = system_root or os.environ.get("SystemRoot", r"C:\Windows")
    return [
        read_pol_file(os.path.join(root, MACHINE_POL), "Computer", "HKLM"),
        read_pol_file(os.path.join(root, USER_POL), "User", "HKCU"),
    ]


@dataclass
class PerUserLocalPolicy:
    """One `GroupPolicyUsers\\<SID>` folder -- a per-account or per-group
    local GPO, distinct from the single machine-wide one `local_policy_files`
    reads."""
    sid: str = ""
    account_name: str = ""
    resolved: bool = False
    pol: Optional[PolFile] = None


def _resolve_sid_name(sid_str: str) -> Tuple[str, bool]:
    """The account/group name for a SID string, and whether it resolved.

    An unresolvable SID is not an error: Windows leaves the per-user local
    GPO folder in place after the account it targeted is deleted, and that
    folder's policy is still real, still-configured data worth showing under
    its raw SID rather than being dropped.
    """
    special = _SPECIAL_LOCAL_GPO_SIDS.get(sid_str.upper())
    if special:
        return special, True
    try:
        import win32security
        sid = win32security.ConvertStringSidToSid(sid_str)
        name, domain, _sid_type = win32security.LookupAccountSid(None, sid)
        return ("%s\\%s" % (domain, name) if domain else name), True
    except Exception as exc:  # noqa: BLE001 - any pywin32/OS failure here
        # means only "this SID no longer resolves", never a code bug worth
        # crashing the refresh over.
        logger.debug("Could not resolve local-GPO SID %s: %s", sid_str, exc)
        return "", False


def per_user_local_policies(
        system_root_dir: Optional[str] = None) -> List[PerUserLocalPolicy]:
    """Multiple Local GPO: one `Registry.pol` per targeted account/group.

    Enumerates `System32\\GroupPolicyUsers\\<SID>\\User\\Registry.pol` --
    see the module-level `GROUP_POLICY_USERS_DIR` docstring for why this is a
    separate tree from `local_policy_files`. An empty return here is a real,
    verified answer ("multiple local GPO is not configured"), not a refusal;
    a missing top-level folder is treated the same way `read_pol_file`
    treats a missing `Registry.pol` -- the normal state, not an error.
    """
    if system_root_dir is None:
        from core.windows_utils import system_root as _default_system_root
        system_root_dir = _default_system_root()
    base = os.path.join(system_root_dir, GROUP_POLICY_USERS_DIR)
    results: List[PerUserLocalPolicy] = []
    try:
        entries = sorted(os.listdir(base))
    except OSError as exc:
        logger.debug("Could not list %s: %s", base, exc)
        return results
    for name in entries:
        sid_dir = os.path.join(base, name)
        if not os.path.isdir(sid_dir) or not name.upper().startswith("S-1-"):
            continue
        pol = read_pol_file(
            os.path.join(sid_dir, "User", "Registry.pol"),
            scope="User", hive="HKU\\%s" % name)
        account_name, resolved = _resolve_sid_name(name)
        results.append(PerUserLocalPolicy(
            sid=name, account_name=account_name, resolved=resolved, pol=pol))
    return results
