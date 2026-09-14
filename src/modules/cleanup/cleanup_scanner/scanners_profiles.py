"""Orphaned Windows user profile detection.

A profile folder under %SystemDrive%\\Users with no matching entry in
HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList is one
Windows itself no longer considers a real account -- left behind by an
incompletely-removed domain/AAD account, a profile-creation failure that
Windows abandons under a .bak-suffixed SID key, or manual account
deletion that didn't clean up the folder.

This is the single highest-consequence thing this module can point at: a
home folder, not a cache. safety='danger' is not a formality here --
_do_clean_all_safe only auto-selects 'safe' items, so this can never be
swept by a bulk action, only deleted one folder at a time with the
explicit checkbox ticked.
"""
import logging
import os
import winreg

from modules.cleanup.cleanup_scanner._common import ScanResult, _make_item

logger = logging.getLogger(__name__)

_PROFILE_LIST_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"

#: Folders Windows itself creates under %SystemDrive%\Users that are
#: never real user profiles -- never flag these as orphaned regardless
#: of ProfileList's contents.
_NEVER_ORPHAN = {"Public", "Default", "Default User", "All Users",
                 "desktop.ini"}

__all__ = ['scan_orphaned_user_profiles']


def _known_profile_paths():
    """Every ProfileImagePath ProfileList currently knows about, lower-
    cased for a case-insensitive match against real folder names.
    Returns None if the read was refused -- distinct from an empty set,
    which means "checked, found none"."""
    known = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _PROFILE_LIST_KEY) as root:
            index = 0
            while True:
                try:
                    sid_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, sid_name) as sid_key:
                        path, _ = winreg.QueryValueEx(sid_key, "ProfileImagePath")
                        known.add(os.path.normcase(os.path.normpath(path)))
                except OSError:
                    continue
    except OSError as e:
        logger.warning("Could not read %s: %s", _PROFILE_LIST_KEY, e)
        return None
    return known


def scan_orphaned_user_profiles(min_age_days: int = 0) -> ScanResult:
    result = ScanResult()
    system_drive = os.environ.get("SystemDrive", "C:")
    users_dir = os.path.join(system_drive + "\\", "Users")
    if not os.path.isdir(users_dir):
        return result

    known = _known_profile_paths()
    if known is None:
        return result  # refused read -- report nothing, not everything

    for name in os.listdir(users_dir):
        if name in _NEVER_ORPHAN:
            continue
        full = os.path.join(users_dir, name)
        if not os.path.isdir(full):
            continue
        if os.path.normcase(os.path.normpath(full)) in known:
            continue
        item = _make_item(full, safety="danger", min_age_days=min_age_days)
        if item:
            result.items.append(item)
            result.total_size += item.size
    return result
