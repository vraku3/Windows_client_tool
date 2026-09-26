"""Registry search and 'modified recently' scans. Qt-free, read-only.

The walk reaches winreg through a small backend so tests use a fake tree.
Keys we are refused are counted and named in the result, never skipped
silently: "no hits" with 40 unreadable keys is not the same as "no hits".
"""
from __future__ import annotations

import datetime
import logging
import time
import winreg
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

HIVES = {
    "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
    "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
    "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
    "HKEY_USERS": winreg.HKEY_USERS,
    "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
}
_FILETIME_EPOCH = datetime.datetime(1601, 1, 1)

BOOKMARKS: Dict[str, str] = {
    "Run (HKLM)": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
    "Run (HKCU)": r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run",
    "RunOnce (HKLM)": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
    "Winlogon": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
    "Image File Execution Options": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options",
    "AppInit_DLLs (Windows)": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows",
    "Uninstall": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    "Services": r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services",
    "Environment (system)": r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    "Policies (machine)": r"HKEY_LOCAL_MACHINE\SOFTWARE\Policies",
    "Policies (user)": r"HKEY_CURRENT_USER\Software\Policies",
    "LSA": r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Lsa",
    "Terminal Server (RDP)": r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Terminal Server",
    "Defender": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows Defender",
}


def filetime_to_datetime(ft: int) -> datetime.datetime:
    """QueryInfoKey's last-write value: 100 ns ticks since 1601 (UTC)."""
    return _FILETIME_EPOCH + datetime.timedelta(microseconds=ft // 10)


def split_full_path(full: str) -> Tuple[Optional[int], str]:
    parts = full.strip().strip("\\").split("\\", 1)
    hive = HIVES.get(parts[0].upper())
    return hive, parts[1] if len(parts) > 1 else ""


class Backend:
    def open(self, hive, path):
        return winreg.OpenKey(hive, path, 0, winreg.KEY_READ)

    def close(self, key) -> None:
        key.Close()

    def info(self, key) -> Tuple[int, int, int]:
        return winreg.QueryInfoKey(key)

    def subkey(self, key, i: int) -> str:
        return winreg.EnumKey(key, i)

    def value(self, key, i: int):
        return winreg.EnumValue(key, i)


@dataclass
class Hit:
    path: str
    last_write: datetime.datetime
    what: str        # "key name" | "value name" | "value data" | "modified"
    detail: str = ""


@dataclass
class ScanResult:
    hits: List[Hit] = field(default_factory=list)
    keys_visited: int = 0
    refused: List[str] = field(default_factory=list)
    truncated: bool = False


def scan(root_full: str, *, text: str = "", in_names: bool = True, in_values: bool = False,
         modified_since: Optional[datetime.datetime] = None,
         max_hits: int = 200, max_keys: int = 200_000, time_limit_s: float = 60.0,
         is_cancelled: Callable[[], bool] = lambda: False,
         backend: Optional[Backend] = None, now: Callable[[], float] = time.monotonic) -> ScanResult:
    """Walk `root_full` depth-first. Text matches key/value names (and optionally
    string data); `modified_since` matches on the key's last-write time."""
    be = backend or Backend()
    hive, sub = split_full_path(root_full)
    res = ScanResult()
    if hive is None:
        res.refused.append(f"unknown hive in {root_full!r}")
        return res
    q = text.lower()
    deadline = now() + time_limit_s
    stack = [(root_full.strip("\\"), sub)]
    while stack:
        if is_cancelled():
            break
        if len(res.hits) >= max_hits or res.keys_visited >= max_keys or now() > deadline:
            res.truncated = True
            break
        full, path = stack.pop()
        try:
            key = be.open(hive, path)
        except OSError as e:
            res.refused.append(f"{full}: {e}")
            continue
        try:
            nsub, nval, ft = be.info(key)
            res.keys_visited += 1
            _check_key(be, key, full, nval, filetime_to_datetime(ft), q, in_names, in_values,
                       modified_since, res)
            for i in range(nsub):
                try:
                    name = be.subkey(key, i)
                except OSError as e:
                    res.refused.append(f"{full} (subkey {i}): {e}")
                    continue
                stack.append((full + "\\" + name, (path + "\\" + name) if path else name))
        finally:
            be.close(key)
    return res


def _check_key(be, key, full, nval, when, q, in_names, in_values, modified_since, res) -> None:
    if modified_since is not None and when >= modified_since:
        res.hits.append(Hit(full, when, "modified"))
        return
    if not q:
        return
    if in_names and q in full.rsplit("\\", 1)[-1].lower():
        res.hits.append(Hit(full, when, "key name"))
        return
    if not (in_names or in_values):
        return
    for i in range(nval):
        try:
            name, data, _kind = be.value(key, i)
        except OSError as e:
            res.refused.append(f"{full} (value {i}): {e}")
            continue
        if in_names and q in str(name).lower():
            res.hits.append(Hit(full, when, "value name", str(name)))
            return
        if in_values and isinstance(data, str) and q in data.lower():
            res.hits.append(Hit(full, when, "value data", f"{name} = {data[:120]}"))
            return
