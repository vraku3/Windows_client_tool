"""Who is logged on to this machine, from the Terminal Services API.

No Qt. `quser` is the obvious tool and is absent on Home editions, so this asks
wtsapi32 directly, which every edition has. A failed call is `None` -- "could
not ask" -- and never an empty list, which would read as "nobody is logged on".
"""
import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

WTS_CURRENT_SERVER_HANDLE = 0
_USER, _WINSTATION, _DOMAIN, _STATE, _CLIENT = 5, 6, 7, 8, 10

STATE_NAMES = {0: "Active", 1: "Connected", 2: "Connect query", 3: "Shadow",
               4: "Disconnected", 5: "Idle", 6: "Listening", 7: "Reset",
               8: "Down", 9: "Init"}


@dataclass(frozen=True)
class Session:
    id: int
    winstation: str
    user: str
    domain: str
    state: str
    client: str

    @property
    def account(self) -> str:
        return f"{self.domain}\\{self.user}" if self.domain else self.user

    @property
    def is_remote(self) -> bool:
        return self.winstation.upper().startswith("RDP-")


class _WTS_SESSION_INFO(ctypes.Structure):
    _fields_ = [("SessionId", wintypes.DWORD), ("pWinStationName", wintypes.LPWSTR),
                ("State", wintypes.DWORD)]


def _query_string(wtsapi, session_id: int, info_class: int) -> str:
    buffer = wintypes.LPWSTR()
    size = wintypes.DWORD()
    if not wtsapi.WTSQuerySessionInformationW(
            WTS_CURRENT_SERVER_HANDLE, session_id, info_class,
            ctypes.byref(buffer), ctypes.byref(size)):
        return ""
    try:
        return buffer.value or ""
    finally:
        wtsapi.WTSFreeMemory(buffer)


def list_sessions() -> Optional[List[Session]]:
    """Every session that has a user, or None if the API could not be asked.

    The services session (0) and the listener have no user and are left out:
    they are plumbing, not people.
    """
    try:
        wtsapi = ctypes.WinDLL("wtsapi32", use_last_error=True)
        wtsapi.WTSEnumerateSessionsW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_WTS_SESSION_INFO)),
            ctypes.POINTER(wintypes.DWORD)]
        pinfo = ctypes.POINTER(_WTS_SESSION_INFO)()
        count = wintypes.DWORD()
        if not wtsapi.WTSEnumerateSessionsW(
                WTS_CURRENT_SERVER_HANDLE, 0, 1, ctypes.byref(pinfo), ctypes.byref(count)):
            logger.warning("WTSEnumerateSessions failed: %s", ctypes.get_last_error())
            return None
        try:
            found = []
            for i in range(count.value):
                info = pinfo[i]
                user = _query_string(wtsapi, info.SessionId, _USER)
                if not user:
                    continue
                found.append(Session(
                    id=info.SessionId,
                    winstation=info.pWinStationName or "",
                    user=user,
                    domain=_query_string(wtsapi, info.SessionId, _DOMAIN),
                    state=STATE_NAMES.get(info.State, str(info.State)),
                    client=_query_string(wtsapi, info.SessionId, _CLIENT)))
            return found
        finally:
            wtsapi.WTSFreeMemory(pinfo)
    except (OSError, AttributeError) as e:
        logger.warning("wtsapi32 unavailable: %s", e)
        return None
