"""Mount-point links only. Never recursively delete, and never follow on removal."""
import ctypes
import logging
import os
import re
import stat
import subprocess
from pathlib import Path

from .onedrive import normal

logger = logging.getLogger(__name__)
MOUNT_POINT = 0xA0000003


def target(path):
    info = os.lstat(path)
    if not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        return None
    if getattr(info, "st_reparse_tag", 0) != MOUNT_POINT:
        return None
    value = os.readlink(path)
    return value[4:] if value.startswith("\\\\?\\") else value


def valid_name(name):
    if (not name or name in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.endswith((".", " "))
            or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                                                *[f"COM{i}" for i in range(1, 10)],
                                                *[f"LPT{i}" for i in range(1, 10)]}):
        raise ValueError("Choose a name that is valid for a Windows folder.")


def choose(profile, name):
    valid_name(name)
    for index in range(1, 10000):
        suffix = "" if index == 1 else f" ({index})"
        candidate = Path(profile) / (".navpane-" + name + suffix)
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            return str(candidate)
    raise FileExistsError("No unused hidden link name available.")


def create(path, destination):
    logger.info("nav pane: create junction %s -> %s", path, destination)
    try:
        existing = target(path)
    except FileNotFoundError:
        existing = None
    else:
        if existing is None or normal(existing) != normal(destination):
            raise FileExistsError(f"Will not replace existing path: {path}")
        hide(path)
        return
    try:
        from _winapi import CreateJunction
    except ImportError:
        from .registry import system_exe
        if any(character in path + destination for character in '&|<>^%!\r\n'):
            raise ValueError("This path needs native junction support; the command fallback cannot safely quote it.")
        result = subprocess.run([system_exe("cmd.exe"), "/d", "/c", "mklink", "/J", path, destination],
                                capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise OSError(f"Could not create junction: {result.stderr!r}")
    else:
        CreateJunction(destination, path)
    if normal(target(path) or "") != normal(destination):
        raise OSError(f"Junction read-back mismatch: {path}")
    hide(path)


def hide(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    kernel.GetFileAttributesW.restype = ctypes.c_uint32
    kernel.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    attributes = kernel.GetFileAttributesW(path)
    if attributes == 0xFFFFFFFF or not kernel.SetFileAttributesW(path, attributes | 2):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GetFileAttributesW(path) & 2:
        raise OSError(f"Hidden attribute was not applied: {path}")


def remove(path, recorded_target):
    try:
        actual = target(path)
    except FileNotFoundError:
        logger.debug("Junction already absent: %s", path)
        return
    if actual is None or normal(actual) != normal(recorded_target):
        raise OSError(f"Refusing to remove a changed junction or real directory: {path}")
    logger.info("nav pane: remove junction %s", path)
    os.rmdir(path)
    if os.path.lexists(path):
        raise OSError(f"Junction still present: {path}")
