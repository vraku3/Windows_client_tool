"""Which processes hold these files -- Restart Manager, the API Windows
Installer and Explorer's "file in use" dialog use. Qt-free; never raises."""
import ctypes
import logging
import os
from collections import Counter
from ctypes import wintypes
from typing import Dict, Sequence

import psutil

logger = logging.getLogger(__name__)

CCH_RM_SESSION_KEY = 33
CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63
ERROR_MORE_DATA = 234
WEBVIEW = "msedgewebview2.exe"
GET_LIST_ATTEMPTS = 3


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [("Process", _RM_UNIQUE_PROCESS),
                ("strAppName", wintypes.WCHAR * (CCH_RM_MAX_APP_NAME + 1)),
                ("strServiceShortName", wintypes.WCHAR * (CCH_RM_MAX_SVC_NAME + 1)),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", wintypes.ULONG),
                ("TSSessionId", wintypes.DWORD),
                ("bRestartable", wintypes.BOOL)]


def _image_name(pid: int, fallback: str) -> str:
    try:
        return psutil.Process(pid).name()
    except Exception as exc:  # gone, or another user's: the RM display name will do
        logger.debug("no image name for pid %s: %s", pid, exc)
        return fallback or f"PID {pid}"


def _display_name(pid: int, fallback: str) -> str:
    """WebView2 is an embedded browser: name the app that hosts it."""
    name = _image_name(pid, fallback)
    if name.lower() != WEBVIEW:
        return name
    try:
        proc = psutil.Process(pid).parent()
        while proc is not None and proc.name().lower() == WEBVIEW:
            proc = proc.parent()
        if proc is not None:
            return f"{proc.name()} (WebView2)"
        logger.debug("no host process found for WebView2 pid %s", pid)
    except Exception as exc:
        logger.debug("could not walk parents of WebView2 pid %s: %s", pid, exc)
    return name


def _check(op: str, rc: int, path: str) -> bool:
    if rc != 0:
        logger.warning("Restart Manager %s failed (code %s) for %s", op, rc, path)
    return rc == 0


def _get_list(rm, handle, path: str) -> list:
    needed, count, reasons = wintypes.UINT(), wintypes.UINT(0), wintypes.DWORD()
    rc = rm.RmGetList(handle, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
    if rc == 0 or (rc == ERROR_MORE_DATA and needed.value == 0):
        return []
    if rc != ERROR_MORE_DATA:
        _check("RmGetList", rc, path)
        return []
    for _ in range(GET_LIST_ATTEMPTS):
        infos = (_RM_PROCESS_INFO * needed.value)()
        count.value = needed.value
        rc = rm.RmGetList(handle, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons))
        if rc == 0:
            return [_display_name(i.Process.dwProcessId, i.strAppName) for i in infos[:count.value]]
        if rc != ERROR_MORE_DATA:
            _check("RmGetList", rc, path)
            return []
    logger.warning("Restart Manager RmGetList kept growing (code %s) for %s; gave up", rc, path)
    return []


def _owners_of_one(rm, path: str) -> list:
    """Restart Manager answers per SESSION, not per file -- so one session per path
    is what makes "how many of these files does Teams hold" countable."""
    handle = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY)
    if not _check("RmStartSession", rm.RmStartSession(ctypes.byref(handle), 0, key), path):
        return []
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        if not _check("RmRegisterResources",
                      rm.RmRegisterResources(handle, 1, files, 0, None, 0, None), path):
            return []
        return _get_list(rm, handle, path)
    finally:
        _check("RmEndSession", rm.RmEndSession(handle), path)


def owners_of(paths: Sequence[str], limit: int = 64) -> Dict[str, int]:
    if not paths:
        return {}
    try:
        rm = ctypes.WinDLL("rstrtmgr")
    except OSError as exc:
        logger.warning("Restart Manager unavailable: %s", exc)
        return {}
    counts: Counter = Counter()
    for path in list(paths)[:limit]:
        try:
            for name in set(_owners_of_one(rm, os.path.abspath(path))):
                counts[name] += 1
        except Exception as exc:  # never into the clean
            logger.warning("Restart Manager could not check %s: %s", path, exc)
    return dict(counts)
