r"""Where the user's data actually is, and which of it lives in the cloud.

Six scanners used to build their target list from `os.path.expanduser("~")`
plus a hardcoded folder name. That is wrong on any machine where a known
folder is redirected, which is the default once OneDrive Backup is on.
Measured on this machine: `~\Desktop` and `~\Pictures` do not exist at all,
and `~\Documents` holds 10 entries against the real folder's 225. A
missing directory is not an error to those scanners, so Large Items,
duplicates, old files and empty folders all quietly reported on a shadow
of the profile.

`SHGetKnownFolderPath` is the API Explorer itself uses, and it answers with
the redirected path. There is no registry read here on purpose: the shell
consults more than `User Shell Folders` (per-machine policy, and the
folder's own `desktop.ini` redirection), and reimplementing that is how you
get an answer that is right on the dev machine only.

The second thing this module knows is the trap that arrives WITH the fix.
Once the scanners reach a real OneDrive folder, anything that opens a file
hydrates it — Files On-Demand fetches the whole thing over the network.
`_hash_file_fast` opens every file in a size-collision group, so duplicate
detection would silently download gigabytes. `is_cloud_placeholder` answers
that from the file attributes, without opening anything.
"""
from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

#: FOLDERID GUIDs, as the shell defines them. Downloads is the one with no
#: `%USERPROFILE%\<name>` fallback worth having: it is a GUID even in the
#: registry, because it postdates the named-value scheme.
_FOLDER_IDS: Dict[str, str] = {
    "Desktop":   "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Music":     "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Pictures":  "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Videos":    "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}

#: The folders the user-data scanners sweep, in a stable order.
USER_DATA_FOLDERS = ("Downloads", "Documents", "Desktop", "Pictures",
                     "Videos", "Music")

#: The subset safe for a scanner that FLAGS INDIVIDUAL FILES for deletion by
#: age/size/duplicate-hash (scan_old_files, scan_large_files,
#: scan_duplicate_files) rather than one that only measures a folder as a
#: whole. Pictures and Videos are deliberately excluded: a real photo or
#: home video is exactly as "old" or "large" as a real duplicate or a real
#: stale download, and none of those three heuristics can tell irreplaceable
#: personal media apart from actual junk. User-reported concern (2026-09-21):
#: make sure Cleanup never deletes photos.
FILE_SWEEP_SAFE_FOLDERS = tuple(
    f for f in USER_DATA_FOLDERS if f not in ("Pictures", "Videos")
)

# Attributes that mean "the bytes are not necessarily here". OFFLINE alone is
# not enough — plenty of backup software sets it — but together with a
# reparse point it is what a Files On-Demand placeholder looks like.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_OFFLINE = 0x00001000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000

_PLACEHOLDER_MASK = (_FILE_ATTRIBUTE_RECALL_ON_OPEN
                     | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)

_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

_cache: Dict[str, Optional[str]] = {}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid_from_string(text: str) -> _GUID:
    guid = _GUID()
    hresult = ctypes.windll.ole32.CLSIDFromString(
        ctypes.c_wchar_p(text), ctypes.byref(guid))
    if hresult != 0:
        raise OSError(f"CLSIDFromString({text}) failed: 0x{hresult & 0xFFFFFFFF:08X}")
    return guid


def known_folder(name: str) -> Optional[str]:
    """The real path of a known folder, redirection followed.

    None for a name this module does not know, and for the (unexpected)
    case where the shell refuses to answer — never a guessed
    `%USERPROFILE%\\<name>`, because a plausible wrong path is exactly what
    made the old behaviour invisible.
    """
    if name in _cache:
        return _cache[name]

    folder_id = _FOLDER_IDS.get(name)
    if folder_id is None:
        _cache[name] = None
        return None

    path_ptr = ctypes.c_wchar_p()
    try:
        hresult = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(_guid_from_string(folder_id)),
            0,        # no KF_FLAG_*: we want the current, redirected path
            None,     # current user
            ctypes.byref(path_ptr))
        if hresult != 0:
            logger.warning("SHGetKnownFolderPath(%s) failed: 0x%08X",
                           name, hresult & 0xFFFFFFFF)
            _cache[name] = None
            return None
        resolved = path_ptr.value
    except OSError:
        logger.warning("Could not resolve known folder %s", name, exc_info=True)
        _cache[name] = None
        return None
    finally:
        if path_ptr.value is not None:
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)

    _cache[name] = resolved
    return resolved


def user_data_dirs(folders=USER_DATA_FOLDERS) -> List[str]:
    """The user's real data folders — the ones that resolved and exist."""
    found: List[str] = []
    for name in folders:
        path = known_folder(name)
        if path and os.path.isdir(path) and path not in found:
            found.append(path)
    return found


def is_cloud_placeholder(path: str) -> bool:
    """True if opening this file would pull it down from the cloud.

    Answered from the file attributes, which costs one metadata call and
    does NOT hydrate the file. Anything that cannot be read is reported as
    "not a placeholder": callers use this to skip work, and a failed
    attribute read must not silently drop a real local file from a scan.
    """
    try:
        get_attributes = ctypes.windll.kernel32.GetFileAttributesW
        # Without this, ctypes hands back a SIGNED int and the sentinel
        # 0xFFFFFFFF arrives as -1 — so "the file is not there" tested
        # positive for every attribute bit, and a missing path was reported
        # as a cloud placeholder.
        get_attributes.restype = wintypes.DWORD
        get_attributes.argtypes = [wintypes.LPCWSTR]
        attributes = get_attributes(path)
    except OSError:
        return False
    if attributes == _INVALID_FILE_ATTRIBUTES:
        return False
    if attributes & _PLACEHOLDER_MASK:
        return True
    # A dehydrated OneDrive file on older builds shows up as a reparse point
    # marked offline rather than with the RECALL_ON_* attributes.
    return bool(attributes & _FILE_ATTRIBUTE_OFFLINE
                and attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def reset_cache() -> None:
    """Forget resolved paths. For tests; nothing in the app needs it."""
    _cache.clear()
