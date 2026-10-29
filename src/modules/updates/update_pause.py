"""Pause and resume Windows Update through the UX settings key.

Windows Settings writes these values under
HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\UX\\Settings.
Pausing is reversible by deleting the values; the real limit (35 days) is
Windows', not an arbitrary app rule. Writes need administrator rights.
"""
from __future__ import annotations

import winreg
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

UX_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\UX\Settings"
VALUE_NAMES = (
    "PauseFeatureUpdatesStartTime",
    "PauseFeatureUpdatesEndTime",
    "PauseQualityUpdatesStartTime",
    "PauseQualityUpdatesEndTime",
    "PauseUpdatesExpiryTime",
)
MAX_DAYS = 35


def _stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pause_values(days: int = 7, now: Optional[datetime] = None) -> Dict[str, str]:
    """The five REG_SZ values Windows Settings writes for a pause."""
    days = max(1, min(int(days), MAX_DAYS))
    current = now or datetime.now(timezone.utc)
    start = _stamp(current)
    end = _stamp(current + timedelta(days=days))
    return {
        "PauseFeatureUpdatesStartTime": start,
        "PauseFeatureUpdatesEndTime": end,
        "PauseQualityUpdatesStartTime": start,
        "PauseQualityUpdatesEndTime": end,
        "PauseUpdatesExpiryTime": end,
    }


def read_pause(reg=winreg) -> Tuple[Optional[str], str]:
    """(expiry string, reason) for the UX pause; ("", None) when not paused."""
    try:
        key = reg.OpenKey(reg.HKEY_LOCAL_MACHINE, UX_KEY, 0, reg.KEY_READ)
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, f"Could not read the pause state: {exc}"
    try:
        value, _kind = reg.QueryValueEx(key, "PauseUpdatesExpiryTime")
        return str(value or ""), ""
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, f"Could not read the pause state: {exc}"
    finally:
        reg.CloseKey(key)


def pause_updates(days: int = 7, now: Optional[datetime] = None, reg=winreg) -> Tuple[bool, str]:
    values = pause_values(days, now)
    try:
        key = reg.CreateKeyEx(reg.HKEY_LOCAL_MACHINE, UX_KEY, 0, reg.KEY_SET_VALUE)
    except OSError as exc:
        return False, f"Could not pause updates (run as administrator): {exc}"
    try:
        for name, value in values.items():
            reg.SetValueEx(key, name, 0, reg.REG_SZ, value)
    except OSError as exc:
        return False, f"Could not write the pause values: {exc}"
    finally:
        reg.CloseKey(key)
    return True, f"Windows Update paused until {values['PauseUpdatesExpiryTime']}."


def resume_updates(reg=winreg) -> Tuple[bool, str]:
    try:
        key = reg.OpenKey(reg.HKEY_LOCAL_MACHINE, UX_KEY, 0, reg.KEY_SET_VALUE)
    except FileNotFoundError:
        return True, "No Windows Update pause was configured."
    except OSError as exc:
        return False, f"Could not resume updates (run as administrator): {exc}"
    removed = 0
    errors = []
    try:
        for name in VALUE_NAMES:
            try:
                reg.DeleteValue(key, name)
                removed += 1
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f"{name}: {exc}")
    finally:
        reg.CloseKey(key)
    if errors:
        return False, "Could not remove every pause value: " + "; ".join(errors)
    return True, (f"Removed {removed} pause value(s)." if removed
                  else "No Windows Update pause was configured.")
