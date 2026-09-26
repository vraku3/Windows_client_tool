"""Environment-variable reads, verified writes and snapshot diffs. Qt-free.

The registry is reached through a small backend object so tests never touch
the real one.
"""
from __future__ import annotations

import ctypes
import json
import logging
import winreg
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SYS_PATH = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
USR_PATH = r"Environment"


@dataclass
class EnvVar:
    name: str
    value: str
    kind: int  # winreg.REG_SZ or REG_EXPAND_SZ


@dataclass
class OpResult:
    ok: bool
    message: str


class RegistryBackend:
    """Thin winreg wrapper; the only place that touches the registry."""

    def read_all(self, hive, path: str) -> List[EnvVar]:
        out: List[EnvVar] = []
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    name, data, kind = winreg.EnumValue(k, i)
                except OSError:
                    logger.debug("end of values in %s", path)  # winreg's end-of-enumeration signal
                    break
                i += 1
                out.append(EnvVar(name, str(data), kind))
        return out

    def write(self, hive, path: str, name: str, value: str, kind: int) -> None:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, kind, value)

    def delete(self, hive, path: str, name: str) -> None:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)


def read_env(hive, path: str, backend: Optional[RegistryBackend] = None) -> Tuple[Optional[List[EnvVar]], str]:
    """(vars, "") or (None, reason). A refused read is not an empty list."""
    backend = backend or RegistryBackend()
    try:
        return sorted(backend.read_all(hive, path), key=lambda v: v.name.lower()), ""
    except OSError as e:
        logger.warning("Could not read env vars from %s: %s", path, e)
        return None, str(e)


def choose_kind(value: str, existing: Optional[int]) -> int:
    """Keep a variable's type on edit; a new one is EXPAND_SZ only if it needs it."""
    if existing in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        return existing
    return winreg.REG_EXPAND_SZ if "%" in value else winreg.REG_SZ


def broadcast_env_change() -> None:
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)


def _find(vars_: Optional[List[EnvVar]], name: str) -> Optional[EnvVar]:
    for v in vars_ or []:
        if v.name.lower() == name.lower():
            return v
    return None


def set_verified(hive, path: str, name: str, value: str, *, backend=None, broadcast=broadcast_env_change) -> OpResult:
    backend = backend or RegistryBackend()
    before, why = read_env(hive, path, backend)
    if before is None:
        return OpResult(False, f"Could not read the current variables first: {why}")
    old = _find(before, name)
    kind = choose_kind(value, old.kind if old else None)
    try:
        backend.write(hive, path, name, value, kind)
    except OSError as e:
        return OpResult(False, f"Write refused: {e}")
    after, why = read_env(hive, path, backend)
    got = _find(after, name)
    if got is None or got.value != value:
        return OpResult(False, "The write returned success but reading it back did not show the new value.")
    broadcast()
    return OpResult(True, f"{name} set and read back.")


def delete_verified(hive, path: str, name: str, *, backend=None, broadcast=broadcast_env_change) -> OpResult:
    backend = backend or RegistryBackend()
    try:
        backend.delete(hive, path, name)
    except OSError as e:
        return OpResult(False, f"Delete refused: {e}")
    after, why = read_env(hive, path, backend)
    if after is None:
        return OpResult(False, f"Deleted, but could not read back to confirm: {why}")
    if _find(after, name) is not None:
        return OpResult(False, "The delete returned success but the variable is still there.")
    broadcast()
    return OpResult(True, f"{name} deleted and confirmed gone.")


# ── snapshots ────────────────────────────────────────────────────────────

def to_snapshot(system: List[EnvVar], user: List[EnvVar]) -> Dict[str, Dict[str, str]]:
    return {"system": {v.name: v.value for v in system}, "user": {v.name: v.value for v in user}}


def diff_snapshots(old: Dict[str, Dict[str, str]], new: Dict[str, Dict[str, str]]) -> List[str]:
    """One human line per change, scope by scope."""
    lines: List[str] = []
    for scope in ("system", "user"):
        o, n = old.get(scope, {}), new.get(scope, {})
        for k in sorted(set(o) | set(n), key=str.lower):
            if k not in o:
                lines.append(f"[{scope}] added {k} = {n[k]}")
            elif k not in n:
                lines.append(f"[{scope}] removed {k} (was {o[k]})")
            elif o[k] != n[k]:
                lines.append(f"[{scope}] changed {k}: {o[k]}  ->  {n[k]}")
    return lines


def save_snapshot(path: str, snap: Dict[str, Dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)


def load_snapshot(path: str) -> Dict[str, Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "system" not in data or "user" not in data:
        raise ValueError("not an environment snapshot")
    return data
