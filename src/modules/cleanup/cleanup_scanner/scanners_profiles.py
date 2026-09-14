"""Orphaned Windows user profile detection.

A profile folder under %SystemDrive%\\Users with no matching entry in
HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList is one
Windows itself no longer considers a real account -- left behind by an
incompletely-removed domain/AAD account or manual account deletion that
didn't clean up the folder.

This is the single highest-consequence thing this module can point at: a
home folder, not a cache. safety='danger' is not a formality here --
_do_clean_all_safe only auto-selects 'safe' items, and the item itself is
built with selected=False, so this can never be swept by a bulk action or
arrive pre-checked, only deleted one folder at a time with the explicit
checkbox ticked.
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
                 "desktop.ini", "defaultuser0", "WDAGUtilityAccount"}

__all__ = ['scan_orphaned_user_profiles']


#: winreg.EnumKey's normal "ran out of subkeys" signal -- ERROR_NO_MORE_ITEMS.
#: Any OTHER OSError there is a real failure partway through enumeration,
#: not end-of-list.
_ERROR_NO_MORE_ITEMS = 259


def _known_profile_paths():
    """Every ProfileImagePath ProfileList currently knows about, lower-
    cased for a case-insensitive match against real folder names.
    Returns None if the read was refused -- distinct from an empty set,
    which means "checked, found none".

    A refusal here must never come back as an empty set: an empty set is
    read by the caller as "ProfileList knows nothing", which would flag
    every real profile folder on the machine as orphaned. So both the
    EnumKey loop and the per-SID read below return None (not `break` /
    `continue`) on any real failure, discarding whatever was collected so
    far rather than reporting it as if it were complete.
    """
    known = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _PROFILE_LIST_KEY) as root:
            index = 0
            while True:
                try:
                    sid_name = winreg.EnumKey(root, index)
                except OSError as e:
                    if getattr(e, "winerror", None) == _ERROR_NO_MORE_ITEMS:
                        break  # normal end-of-list, not a failure
                    logger.warning(
                        "EnumKey failed partway through %s at index %d: %s",
                        _PROFILE_LIST_KEY, index, e)
                    return None
                index += 1
                try:
                    with winreg.OpenKey(root, sid_name) as sid_key:
                        path, _ = winreg.QueryValueEx(sid_key, "ProfileImagePath")
                        path = os.path.expandvars(path)
                        known.add(os.path.normcase(os.path.normpath(path)))
                except OSError as e:
                    logger.warning(
                        "Could not read ProfileImagePath for %s\\%s: %s",
                        _PROFILE_LIST_KEY, sid_name, e)
                    return None
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
            # Never pre-selected -- a home folder is the single highest-
            # consequence thing this module can point at. _make_item()
            # takes no `selected` parameter, so it is set here explicitly
            # rather than relying on ScanItem's default (True).
            item.selected = False
            result.items.append(item)
            result.total_size += item.size
    return result
