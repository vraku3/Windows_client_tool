"""The `Registry.pol` decoder.

This file is what lets the Group Policy pane show Computer Configuration
without elevation: `gpresult` refuses the computer half unless elevated, but
the local policy file itself is world-readable, and on a machine that is not
domain-joined local policy is the only policy there is.

The test that matters most here is `test_a_size_field_that_encodes_a_semicolon
_does_not_desynchronise_the_stream`. The PReg format separates fields with a
UTF-16 `;`, and it is tempting to parse type and size by scanning for that
delimiter. `0x003B` is the UTF-16 code unit for `;`, so a record carrying 59
bytes of data encodes its size as `3B 00 00 00` and a delimiter scan stops
mid-field. This machine's own file has sizes of only 0 and 4, so the bug would
have passed every test written against real local data here and corrupted the
first domain policy it ever met.
"""
import os
import struct

import pytest

from modules.gpresult.pol_parser import (
    PolParseError, local_policy_files,
    parse_pol_bytes, per_user_local_policies, read_pol_file,
)

REG_SZ = 1
REG_BINARY = 3
REG_DWORD = 4
REG_MULTI_SZ = 7
REG_QWORD = 11


def _u16(text):
    return text.encode("utf-16-le")


def _record(key, value_name, type_id, data):
    return (_u16("[") + _u16(key) + b"\x00\x00"
            + _u16(";") + _u16(value_name) + b"\x00\x00"
            + _u16(";") + struct.pack("<I", type_id)
            + _u16(";") + struct.pack("<I", len(data))
            + _u16(";") + data + _u16("]"))


def _pol(*records):
    return b"PReg" + struct.pack("<I", 1) + b"".join(records)


# ------------------------------------------------------------------
# Framing
# ------------------------------------------------------------------

def test_a_size_field_that_encodes_a_semicolon_does_not_desynchronise_the_stream():
    """59 bytes of data means the size field is `3B 00 00 00`, and 0x003B is
    the UTF-16 code unit for ';'. Read as four raw bytes this is fine; scanned
    for a delimiter it truncates the field and every later record is garbage."""
    blob = _pol(
        _record("Key", "Blob", REG_BINARY, b"\xAA" * 0x3B),
        _record("Key2", "After", REG_DWORD, struct.pack("<I", 7)),
    )
    values = parse_pol_bytes(blob)
    assert len(values) == 2
    assert len(values[0].raw) == 0x3B
    assert values[1].value_name == "After"
    assert values[1].data == 7


def test_a_type_field_that_encodes_a_semicolon_is_also_safe():
    """Same trap on the other four-byte field."""
    blob = _pol(_record("Key", "Odd", 0x3B, b"\x01\x02"))
    values = parse_pol_bytes(blob)
    assert values[0].type_id == 0x3B
    assert values[0].type_name == "REG_TYPE_59"


def test_a_file_without_the_preg_magic_is_rejected():
    with pytest.raises(PolParseError):
        parse_pol_bytes(b"NOPE" + struct.pack("<I", 1))


def test_an_unsupported_version_is_rejected():
    with pytest.raises(PolParseError):
        parse_pol_bytes(b"PReg" + struct.pack("<I", 99))


def test_a_truncated_record_is_rejected_rather_than_half_read():
    """A record claiming more data than the file holds must not silently
    return the short read as if it were the value."""
    good = _record("Key", "Value", REG_BINARY, b"\xFF" * 16)
    with pytest.raises(PolParseError):
        parse_pol_bytes(_pol(good[:-6]))


def test_trailing_null_padding_is_not_an_error():
    """Some writers pad the tail; that is not a corrupt file."""
    blob = _pol(_record("K", "V", REG_DWORD, struct.pack("<I", 1))) + b"\x00" * 8
    assert len(parse_pol_bytes(blob)) == 1


def test_an_empty_stream_holds_no_values():
    assert parse_pol_bytes(_pol()) == []


# ------------------------------------------------------------------
# Value decoding
# ------------------------------------------------------------------

@pytest.mark.parametrize("type_id,raw,expected", [
    (REG_DWORD, struct.pack("<I", 4294967295), 4294967295),
    (REG_QWORD, struct.pack("<Q", 2 ** 40), 2 ** 40),
    (REG_SZ, _u16("hello") + b"\x00\x00", "hello"),
    (REG_MULTI_SZ, _u16("a\x00b\x00") + b"\x00\x00", ["a", "b"]),
])
def test_registry_types_decode_to_python_values(type_id, raw, expected):
    values = parse_pol_bytes(_pol(_record("K", "V", type_id, raw)))
    assert values[0].data == expected


def test_binary_data_is_kept_as_bytes_and_shown_as_hex():
    values = parse_pol_bytes(_pol(_record("K", "V", REG_BINARY, b"\xde\xad")))
    assert values[0].data == b"\xde\xad"
    assert values[0].display() == "dead"


def test_the_raw_bytes_are_always_kept_alongside_the_decoded_value():
    """A value we decoded wrongly is still recoverable if the bytes survive."""
    raw = struct.pack("<I", 1234)
    values = parse_pol_bytes(_pol(_record("K", "V", REG_DWORD, raw)))
    assert values[0].raw == raw


def test_an_undecodable_value_falls_back_to_its_bytes_rather_than_vanishing():
    """A DWORD record carrying two bytes is malformed, but it is still a value
    that is set -- dropping it would understate what the policy does."""
    values = parse_pol_bytes(_pol(_record("K", "V", REG_DWORD, b"\x01\x02")))
    assert values[0].data == b"\x01\x02"


# ------------------------------------------------------------------
# CSE directives
# ------------------------------------------------------------------

def test_a_delete_directive_is_recorded_not_dropped():
    """`**del.Foo` is an instruction to the Registry extension, not a value.
    It is still a fact about the machine: this policy removes that value."""
    values = parse_pol_bytes(_pol(
        _record("K", "**del.Foo", REG_SZ, b"\x00\x00")))
    assert values[0].directive == "delete_value"
    assert values[0].display() == "(delete value)"


def test_directives_are_excluded_from_settings_but_kept_in_values(tmp_path):
    path = tmp_path / "Registry.pol"
    path.write_bytes(_pol(
        _record("K", "Real", REG_DWORD, struct.pack("<I", 1)),
        _record("K", "**delvals.", REG_SZ, b"\x00\x00"),
    ))
    pol = read_pol_file(str(path))
    assert len(pol.values) == 2
    assert [v.value_name for v in pol.settings] == ["Real"]


# ------------------------------------------------------------------
# Files
# ------------------------------------------------------------------

def test_a_missing_file_is_not_an_error():
    """No local policy of that kind is the normal state of a clean machine,
    and it must not read as a failure -- the same distinction the RSOP side
    draws between "refused" and "empty"."""
    pol = read_pol_file(r"C:\definitely\not\here\Registry.pol")
    assert pol.exists is False
    assert pol.error == ""
    assert pol.values == []


def test_a_zero_byte_file_is_not_an_error(tmp_path):
    """Windows leaves an empty Registry.pol when every setting in a scope is
    put back to Not Configured."""
    path = tmp_path / "Registry.pol"
    path.write_bytes(b"")
    pol = read_pol_file(str(path))
    assert pol.exists is True
    assert pol.error == ""
    assert pol.values == []


def test_a_corrupt_file_reports_the_reason_instead_of_raising(tmp_path):
    path = tmp_path / "Registry.pol"
    path.write_bytes(b"GARBAGE" * 4)
    pol = read_pol_file(str(path))
    assert pol.exists is True
    assert "Could not parse" in pol.error
    assert pol.values == []


def test_local_policy_files_covers_both_scopes(tmp_path):
    machine = tmp_path / "System32" / "GroupPolicy" / "Machine"
    machine.mkdir(parents=True)
    (machine / "Registry.pol").write_bytes(
        _pol(_record("Software\\Policies\\Test", "On", REG_DWORD,
                     struct.pack("<I", 1))))

    pols = local_policy_files(str(tmp_path))
    assert [p.scope for p in pols] == ["Computer", "User"]
    assert [p.hive for p in pols] == ["HKLM", "HKCU"]
    assert pols[0].exists is True and pols[1].exists is False
    assert pols[0].settings[0].full_path == "Software\\Policies\\Test\\On"


# ------------------------------------------------------------------
# Against this machine's real file
# ------------------------------------------------------------------

def test_the_real_local_machine_policy_parses():
    """`C:\\Windows\\System32\\GroupPolicy\\Machine\\Registry.pol` is readable
    by an ordinary user -- which is the whole reason this module exists.

    Skipped rather than failed where there is no local policy, since that is
    a legitimate state for a machine to be in."""
    pols = local_policy_files()
    machine = pols[0]
    if not machine.exists:
        pytest.skip("this machine has no local computer policy")
    assert machine.error == ""
    for value in machine.values:
        assert value.key
        assert value.type_name.startswith("REG_")


def test_a_key_only_record_has_no_value_name():
    """Real local policy contains records that name a key and no value at all
    (this machine has one for `...\\Windows\\Safer`). Code downstream indexes
    on the value name, so it has to cope with it being empty."""
    values = parse_pol_bytes(_pol(
        _record("Software\\Policies\\Microsoft\\Windows\\Safer", "", 0, b"")))
    assert values[0].value_name == ""
    assert values[0].full_path == "Software\\Policies\\Microsoft\\Windows\\Safer"


# ------------------------------------------------------------------
# Multiple Local GPO (System32\GroupPolicyUsers) -- a separate mechanism
# from local_policy_files' single machine-wide Registry.pol above.
# ------------------------------------------------------------------

def test_a_missing_group_policy_users_folder_is_not_an_error(tmp_path):
    """A machine that never used per-user local GPO has no such folder at
    all -- the same "normal state, not a failure" rule read_pol_file already
    applies to a missing Registry.pol."""
    assert per_user_local_policies(str(tmp_path)) == []


def test_an_empty_group_policy_users_folder_reports_zero_entries(tmp_path):
    (tmp_path / "System32" / "GroupPolicyUsers").mkdir(parents=True)
    assert per_user_local_policies(str(tmp_path)) == []


def test_a_specific_account_sid_folder_is_read_and_labelled(tmp_path):
    """A real per-account local GPO: gpedit names the folder after the
    account's own SID, and the Registry.pol inside is the exact same PReg
    format local_policy_files already parses -- reused here, not
    reimplemented."""
    sid = "S-1-5-21-111111111-222222222-333333333-1001"
    sid_dir = tmp_path / "System32" / "GroupPolicyUsers" / sid / "User"
    sid_dir.mkdir(parents=True)
    (sid_dir / "Registry.pol").write_bytes(_pol(
        _record("Software\\Policies\\Test", "On", REG_DWORD,
                struct.pack("<I", 1))))

    entries = per_user_local_policies(str(tmp_path))
    assert len(entries) == 1
    assert entries[0].sid == sid
    assert entries[0].pol.exists is True
    assert entries[0].pol.hive == "HKU\\%s" % sid
    assert entries[0].pol.settings[0].full_path == "Software\\Policies\\Test\\On"


def test_the_administrators_and_non_administrators_group_sids_get_friendly_names(
        tmp_path):
    """The two group-wide targets of Multiple Local GPO use well-known SIDs
    that resolve fine through LookupAccountSid, but gpedit calls the second
    one specifically "Non-Administrators" in this context -- not the plain
    group name "Users" LookupAccountSid would give it."""
    base = tmp_path / "System32" / "GroupPolicyUsers"
    for sid in ("S-1-5-32-544", "S-1-5-32-545"):
        (base / sid / "User").mkdir(parents=True)

    entries = {e.sid: e for e in per_user_local_policies(str(tmp_path))}
    assert entries["S-1-5-32-544"].account_name == "Administrators"
    assert entries["S-1-5-32-545"].account_name == "Non-Administrators (Users)"
    assert entries["S-1-5-32-544"].resolved is True


def test_a_non_sid_entry_in_the_folder_is_ignored(tmp_path):
    """Windows itself only ever writes SID-named subfolders here, but a
    stray non-SID entry must not be walked into as if it were one."""
    base = tmp_path / "System32" / "GroupPolicyUsers"
    (base / "desktop.ini").parent.mkdir(parents=True)
    (base / "desktop.ini").write_text("[.ShellClassInfo]")
    assert per_user_local_policies(str(tmp_path)) == []


def test_a_deleted_accounts_sid_still_reports_its_policy_unresolved(tmp_path):
    """A SID with no corresponding account (the user was deleted after the
    per-user GPO was set) must still surface the policy data -- it is real
    and still in effect for anyone who logs on with that SID again."""
    sid = "S-1-5-21-999999999-888888888-777777777-9999"
    sid_dir = tmp_path / "System32" / "GroupPolicyUsers" / sid / "User"
    sid_dir.mkdir(parents=True)
    (sid_dir / "Registry.pol").write_bytes(_pol(
        _record("K", "V", REG_DWORD, struct.pack("<I", 1))))

    entries = per_user_local_policies(str(tmp_path))
    assert len(entries) == 1
    assert entries[0].resolved is False
    assert entries[0].account_name == ""
    assert entries[0].pol.exists is True


def test_the_real_group_policy_users_folder_on_this_machine():
    """Confirmed live 2026-09-29: `C:\\Windows\\System32\\GroupPolicyUsers`
    exists (Windows creates it regardless) but currently holds zero SID
    subfolders on this machine -- Multiple Local GPO has never been set up
    here. That is a real, verifiable answer, not a skip: the function must
    return an empty list rather than treating the folder's mere existence
    as something to report on."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    folder = os.path.join(system_root, "System32", "GroupPolicyUsers")
    if not os.path.isdir(folder):
        pytest.skip("this machine has no GroupPolicyUsers folder at all")
    sid_subfolders = [
        name for name in os.listdir(folder) if name.upper().startswith("S-1-")
    ]
    entries = per_user_local_policies()
    assert len(entries) == len(sid_subfolders)
