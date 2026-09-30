"""registry_acl: who can write to a registry key, via GetNamedSecurityInfo.

Real-machine facts pinned here (all measured against this machine,
unelevated, see registry_acl.py's own docstring for the probe):
- HKCC's security-path prefix is "CONFIG", not "CURRENT_CONFIG" like the
  other four hives' pattern would suggest.
- HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run really does carry
  inherit-only ACEs that must be excluded from "can write to this key".
- HKLM\\SAM\\SAM is really refused, unelevated, through this exact call.
"""
import pytest

from core.admin_utils import is_admin
from modules.registry_explorer import registry_acl as ra


def test_to_security_path_maps_the_five_hives():
    assert ra.to_security_path(r"HKEY_LOCAL_MACHINE\SOFTWARE") == r"MACHINE\SOFTWARE"
    assert ra.to_security_path(r"HKEY_CURRENT_USER\Software") == r"CURRENT_USER\Software"
    assert ra.to_security_path(r"HKEY_CLASSES_ROOT\.txt") == r"CLASSES_ROOT\.txt"
    assert ra.to_security_path(r"HKEY_USERS\.DEFAULT") == r"USERS\.DEFAULT"
    # The one hive whose prefix does NOT just drop "HKEY_" -- measured live,
    # "CURRENT_CONFIG\\..." raises ERROR_INVALID_PARAMETER (87).
    assert ra.to_security_path(r"HKEY_CURRENT_CONFIG\Software") == r"CONFIG\Software"


def test_to_security_path_rejects_an_unknown_hive():
    assert ra.to_security_path(r"NOT_A_HIVE\Foo") is None


def test_bare_hive_with_no_subpath():
    assert ra.to_security_path("HKEY_LOCAL_MACHINE") == "MACHINE"


class _FakeSid:
    def __init__(self, tag):
        self.tag = tag


class _FakeDacl:
    def __init__(self, aces):
        self._aces = aces  # list of ((type, flags), mask, sid)

    def GetAceCount(self):
        return len(self._aces)

    def GetAce(self, i):
        return self._aces[i]


class _FakeSD:
    def __init__(self, owner_sid, dacl):
        self._owner_sid = owner_sid
        self._dacl = dacl

    def GetSecurityDescriptorOwner(self):
        return self._owner_sid

    def GetSecurityDescriptorDacl(self):
        return self._dacl


def _fake_lookup(names):
    def lookup(_system, sid):
        if sid.tag not in names:
            raise Exception("no mapping")  # noqa: TRY002
        domain, name = names[sid.tag]
        return name, domain, 1
    return lookup


def test_excludes_inherit_only_aces_from_effective_write_access():
    """The exact shape measured on Run: an allow ACE that applies here
    (flags 0x10, INHERITED but not inherit-only) plus a second entry for the
    same trustee that is inherit-only (flags 0x1a) and must not count."""
    admin_sid = _FakeSid("admin")
    dacl = _FakeDacl([
        ((0, 0x10), 0xF003F, admin_sid),   # applies here: Full Control
        ((0, 0x1A), 0x10000000, admin_sid),  # inherit-only: future subkeys only
    ])
    sd = _FakeSD(_FakeSid("owner"), dacl)
    lookup = _fake_lookup({"admin": ("BUILTIN", "Administrators"), "owner": ("NT AUTHORITY", "SYSTEM")})

    result = ra.describe_write_access(
        r"HKEY_LOCAL_MACHINE\SOFTWARE\Test",
        get_security_info=lambda *_a, **_k: sd,
        lookup_account_sid=lookup,
    )
    assert result.refused is None
    assert result.owner == "NT AUTHORITY\\SYSTEM"
    assert len(result.writers) == 1
    assert result.writers[0].trustee == "BUILTIN\\Administrators"
    assert result.writers[0].allowed is True


def test_read_only_ace_is_not_reported_as_a_writer():
    users_sid = _FakeSid("users")
    dacl = _FakeDacl([
        ((0, 0x10), 0x20019, users_sid),  # KEY_READ -- no write bits
    ])
    sd = _FakeSD(_FakeSid("owner"), dacl)
    lookup = _fake_lookup({"users": ("BUILTIN", "Users"), "owner": ("NT AUTHORITY", "SYSTEM")})

    result = ra.describe_write_access(
        r"HKEY_LOCAL_MACHINE\SOFTWARE\Test",
        get_security_info=lambda *_a, **_k: sd,
        lookup_account_sid=lookup,
    )
    assert result.writers == []


def test_deny_write_ace_is_reported_and_marked_not_allowed():
    everyone_sid = _FakeSid("everyone")
    dacl = _FakeDacl([
        ((ra._ACCESS_DENIED_ACE_TYPE, 0x0), 0x2, everyone_sid),  # DENY KEY_SET_VALUE
    ])
    sd = _FakeSD(_FakeSid("owner"), dacl)
    lookup = _fake_lookup({"everyone": ("", "Everyone"), "owner": ("NT AUTHORITY", "SYSTEM")})

    result = ra.describe_write_access(
        r"HKEY_LOCAL_MACHINE\SOFTWARE\Test",
        get_security_info=lambda *_a, **_k: sd,
        lookup_account_sid=lookup,
    )
    assert len(result.writers) == 1
    assert result.writers[0].allowed is False


def test_a_refused_read_is_its_own_state_not_an_empty_writer_list():
    def refuse(*_a, **_k):
        raise OSError("Access is denied.")

    result = ra.describe_write_access(r"HKEY_LOCAL_MACHINE\SAM\SAM", get_security_info=refuse)
    assert result.refused
    assert result.writers == []


def test_unknown_hive_is_refused_not_silently_ignored():
    result = ra.describe_write_access(r"HKEY_WEIRD\Foo")
    assert result.refused


def test_lookup_failure_falls_back_to_well_known_name_or_raw_sid():
    import win32security

    creator_owner_sid = win32security.ConvertStringSidToSid("S-1-3-0")

    def raising_lookup(_system, _sid):
        raise Exception("No mapping between account names and security IDs was done.")

    name = ra._lookup_name(creator_owner_sid, raising_lookup)
    assert name == "CREATOR OWNER"


# ---------------------------------------------------------------------------
# Real-machine assertions (require pywin32 + real winreg; run on Windows).
# ---------------------------------------------------------------------------

def test_real_machine_run_key_dacl_excludes_inherit_only_entries():
    """HKLM\\...\\CurrentVersion\\Run, read unelevated: BUILTIN\\Administrators
    and NT AUTHORITY\\SYSTEM hold write access; BUILTIN\\Users (KEY_READ only)
    does not. Pinned against the real DACL, not a guessed shape."""
    result = ra.describe_write_access(
        r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    )
    assert result.refused is None, result.refused
    assert result.owner
    trustees = {w.trustee.upper() for w in result.writers if w.allowed}
    assert "NT AUTHORITY\\SYSTEM" in trustees
    assert "BUILTIN\\ADMINISTRATORS" in trustees
    assert "BUILTIN\\USERS" not in trustees


@pytest.mark.skipif(is_admin(), reason="HKLM\\SAM\\SAM is only refused unelevated")
def test_real_machine_sam_key_is_refused_unelevated():
    """HKLM\\SAM\\SAM is refused to an unelevated process through this exact
    call -- a real, reproducible refusal, never reported as "no writers"."""
    result = ra.describe_write_access(r"HKEY_LOCAL_MACHINE\SAM\SAM")
    assert result.refused
    assert result.writers == []
