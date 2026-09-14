"""Orphaned user profile detection: a folder under %SystemDrive%\\Users
with no matching entry in HKLM\\SOFTWARE\\Microsoft\\Windows NT\\
CurrentVersion\\ProfileList is one Windows itself no longer considers a
real account. Highest-consequence thing this module can point at -- a
home folder, not a cache -- so safety="danger" is load-bearing, not a
formality: _do_clean_all_safe only auto-selects safety=="safe" items.
"""
import os
import tempfile

import pytest

from modules.cleanup.cleanup_scanner import scanners_profiles as sp


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
            raise OSError("no more items")
        return names[index]

    def fake_query_value_ex(key, name):
        return (key._subkeys[name], 1)

    monkeypatch.setattr(sp.winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(sp.winreg, "EnumKey", fake_enum_key)
    monkeypatch.setattr(sp.winreg, "QueryValueEx", fake_query_value_ex)


@pytest.fixture
def users_dir(monkeypatch):
    tmp = tempfile.mkdtemp()
    users = os.path.join(tmp, "Users")
    os.makedirs(users)
    monkeypatch.setenv("SystemDrive", tmp)
    return users


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


def test_public_and_default_are_never_flagged_regardless_of_profilelist(users_dir, monkeypatch):
    _mkprofile(users_dir, "Public")
    _mkprofile(users_dir, "Default")
    _patch_registry(monkeypatch, {})  # nothing known -- would flag both if not excluded

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
