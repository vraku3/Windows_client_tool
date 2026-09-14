"""Orphaned user profile detection: a folder under %SystemDrive%\\Users
with no matching entry in HKLM\\SOFTWARE\\Microsoft\\Windows NT\\
CurrentVersion\\ProfileList is one Windows itself no longer considers a
real account. Highest-consequence thing this module can point at -- a
home folder, not a cache -- so safety="danger" is load-bearing, not a
formality: _do_clean_all_safe only auto-selects safety=="safe" items, and
the scanner itself must never pre-select such an item (Qt.CheckState.Checked
by default would silently defeat that).
"""
import os

import pytest

from modules.cleanup.cleanup_scanner import scanners_profiles as sp

_ERROR_NO_MORE_ITEMS = 259


def _no_more_items_error() -> OSError:
    """The real shape of what winreg.EnumKey raises when a hive's
    subkeys are exhausted: an OSError whose winerror is 259
    (ERROR_NO_MORE_ITEMS), not a bare OSError with no winerror at all."""
    err = OSError("no more items")
    err.winerror = _ERROR_NO_MORE_ITEMS
    return err


class _FakeKey:
    def __init__(self, subkeys):
        self._subkeys = subkeys  # {sid_name: profile_image_path}
        self._names = list(subkeys.keys())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_registry(monkeypatch, sid_to_path: dict, *, refuse: bool = False):
    """Simulate HKLM\\...\\ProfileList with the given {sid: path} entries,
    or a total refusal (OSError) if refuse=True."""
    root = _FakeKey(sid_to_path)

    def fake_open_key(hive, path, *a, **k):
        if refuse:
            raise OSError("Access is denied")
        if path == sp._PROFILE_LIST_KEY:
            return root
        # Opening a per-SID subkey: return a fake key that answers
        # QueryValueEx for ProfileImagePath.
        sid = path.rsplit("\\", 1)[-1]
        return _FakeKey({"ProfileImagePath": sid_to_path[sid]})

    def fake_enum_key(key, index):
        names = key._names if key is root else []
        if index >= len(names):
            raise _no_more_items_error()
        return names[index]

    def fake_query_value_ex(key, name):
        return (key._subkeys[name], 1)

    monkeypatch.setattr(sp.winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(sp.winreg, "EnumKey", fake_enum_key)
    monkeypatch.setattr(sp.winreg, "QueryValueEx", fake_query_value_ex)


@pytest.fixture
def users_dir(tmp_path, monkeypatch):
    users = tmp_path / "Users"
    users.mkdir()
    monkeypatch.setenv("SystemDrive", str(tmp_path))
    return str(users)


def _mkprofile(users_dir: str, name: str) -> str:
    path = os.path.join(users_dir, name)
    os.makedirs(path)
    with open(os.path.join(path, "ntuser.dat"), "w") as f:
        f.write("x" * 500)
    return path


def test_a_profile_matching_profilelist_is_not_flagged(users_dir, monkeypatch):
    real = _mkprofile(users_dir, "alice")
    _patch_registry(monkeypatch, {"S-1-5-21-1": real})

    result = sp.scan_orphaned_user_profiles()

    assert result.items == []


def test_a_profile_with_no_profilelist_entry_is_flagged_danger(users_dir, monkeypatch):
    orphan = _mkprofile(users_dir, "ghost")
    _patch_registry(monkeypatch, {})  # ProfileList knows nothing

    result = sp.scan_orphaned_user_profiles()

    assert len(result.items) == 1
    assert os.path.normcase(result.items[0].path) == os.path.normcase(orphan)
    assert result.items[0].safety == "danger"


def test_orphaned_profile_item_is_never_pre_selected(users_dir, monkeypatch):
    """C2: an orphaned profile is the single highest-consequence thing
    this module can point at -- a home folder. It must arrive with
    selected=False, never ScanItem's default of True, so a bulk action
    reading .selected cannot sweep it."""
    _mkprofile(users_dir, "ghost")
    _patch_registry(monkeypatch, {})

    result = sp.scan_orphaned_user_profiles()

    assert len(result.items) == 1
    assert result.items[0].selected is False


def test_public_and_default_are_never_flagged_regardless_of_profilelist(users_dir, monkeypatch):
    _mkprofile(users_dir, "Public")
    _mkprofile(users_dir, "Default")
    _patch_registry(monkeypatch, {})  # nothing known -- would flag both if not excluded

    result = sp.scan_orphaned_user_profiles()

    assert result.items == []


def test_defaultuser0_and_wdag_utility_account_are_never_flagged(users_dir, monkeypatch):
    """M5: OOBE/sysprep leftover and WDAG's utility account are Windows-
    created folders, never real profiles."""
    _mkprofile(users_dir, "defaultuser0")
    _mkprofile(users_dir, "WDAGUtilityAccount")
    _patch_registry(monkeypatch, {})

    result = sp.scan_orphaned_user_profiles()

    assert result.items == []


def test_a_refused_registry_read_reports_nothing_not_everything(users_dir, monkeypatch):
    _mkprofile(users_dir, "alice")
    _mkprofile(users_dir, "ghost")
    _patch_registry(monkeypatch, {}, refuse=True)

    result = sp.scan_orphaned_user_profiles()

    assert result.items == [], (
        "a refused registry read must report nothing, never flag every "
        "profile as orphaned")


def test_enum_key_failure_partway_through_reports_nothing(users_dir, monkeypatch):
    """C1+I1: an OSError from EnumKey with a winerror OTHER than
    ERROR_NO_MORE_ITEMS (259) is a real failure partway through
    enumeration, not the normal end-of-list signal. It must not `break`
    and quietly return whatever was collected so far as if it were a
    complete list -- that would flag every real orphan-shaped folder
    Windows never got a chance to tell us about."""
    _mkprofile(users_dir, "alice")
    _mkprofile(users_dir, "ghost")  # orphan-shaped: no ProfileList entry at all

    root = _FakeKey({"S-1-5-21-1": "placeholder"})

    def fake_open_key(hive, path, *a, **k):
        if path == sp._PROFILE_LIST_KEY:
            return root
        raise AssertionError("should never open a per-SID key: EnumKey failed first")

    def fake_enum_key(key, index):
        err = OSError("access denied partway through enumeration")
        err.winerror = 5  # ERROR_ACCESS_DENIED -- NOT 259
        raise err

    monkeypatch.setattr(sp.winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(sp.winreg, "EnumKey", fake_enum_key)

    result = sp.scan_orphaned_user_profiles()

    assert result.items == [], (
        "a real EnumKey failure must report zero items -- not everything "
        "found so far, and not 'everything is orphaned'")


def test_per_sid_read_failure_reports_nothing_not_just_that_one_profile(users_dir, monkeypatch):
    """C1+I1: a per-SID OpenKey/QueryValueEx failure for ONE SID must not
    `continue` and silently drop just that profile from the known set --
    that turns a real, still-logged-in user's profile into a false-
    positive orphan. The whole read is refused instead."""
    real = _mkprofile(users_dir, "alice")
    _mkprofile(users_dir, "ghost")

    root = _FakeKey({"S-1-5-21-1": real, "S-1-5-21-2": "unreadable"})

    def fake_open_key(hive, path, *a, **k):
        if path == sp._PROFILE_LIST_KEY:
            return root
        sid = path.rsplit("\\", 1)[-1]
        if sid == "S-1-5-21-2":
            raise OSError("Access is denied")
        return _FakeKey({"ProfileImagePath": real})

    def fake_enum_key(key, index):
        names = key._names
        if index >= len(names):
            raise _no_more_items_error()
        return names[index]

    def fake_query_value_ex(key, name):
        return (key._subkeys[name], 1)

    monkeypatch.setattr(sp.winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(sp.winreg, "EnumKey", fake_enum_key)
    monkeypatch.setattr(sp.winreg, "QueryValueEx", fake_query_value_ex)

    result = sp.scan_orphaned_user_profiles()

    assert result.items == [], (
        "a per-SID read failure must refuse the whole read (report "
        "nothing), not silently drop just that one profile from the "
        "known set and flag 'alice' or 'ghost' as orphaned")


def test_profile_image_path_with_env_var_is_expanded_before_comparison(users_dir, monkeypatch):
    """C3: ProfileImagePath is REG_EXPAND_SZ and some machines genuinely
    store it with an unexpanded variable. A real logged-in user's profile
    must still be recognized as known once expanded, not flagged as an
    orphan because the raw string with a literal '%SystemDrive%' in it
    never matches the real folder path."""
    real = _mkprofile(users_dir, "bob")
    system_drive = os.environ["SystemDrive"]  # set by the users_dir fixture
    raw_value = real.replace(system_drive, "%SystemDrive%", 1)
    assert "%SystemDrive%" in raw_value  # sanity: the substitution actually happened

    _patch_registry(monkeypatch, {"S-1-5-21-1": raw_value})

    result = sp.scan_orphaned_user_profiles()

    assert result.items == [], (
        "bob's profile uses an unexpanded %SystemDrive% in ProfileImagePath "
        "and must still be recognized as known, not flagged as orphaned")
