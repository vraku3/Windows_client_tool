"""Real Windows System Restore checkpoint creation (Checkpoint-Computer).

This is distinct from BackupService.create_restore_point (this app's own
SQLite-backed tweak-revert log) — it creates an actual OS-level restore point.
Synchronous; callers must run this inside a Worker/COMWorker, never on the
Qt main thread.
"""
import logging
import subprocess
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def create_restore_point(description: str, timeout: int = 60) -> Tuple[bool, str]:
    """Create a Windows System Restore point.

    Enables System Restore on C:\\, bypasses the "one restore point per 24h"
    throttle for this call (SystemRestorePointCreationFrequency=0), then runs
    Checkpoint-Computer. Returns (success, output_or_error_message).
    """
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Enable-ComputerRestore -Drive 'C:\\'"],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        logger.warning("Enable-ComputerRestore failed", exc_info=True)

    throttle = _bypass_frequency_throttle()
    try:
        return _checkpoint(description, timeout)
    finally:
        _restore_frequency_throttle(throttle)


_FREQ_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore"
_FREQ_VALUE = "SystemRestorePointCreationFrequency"


def _bypass_frequency_throttle():
    """Set the 24 h creation throttle to 0 for this one call. Returns what to
    put back: ("value", data, kind), ("absent",), or None when nothing was
    changed (the before-state could not be read, so it is left alone)."""
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, _FREQ_KEY, 0,
                                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            try:
                data, kind = winreg.QueryValueEx(key, _FREQ_VALUE)
                before = ("value", data, kind)
            except FileNotFoundError:
                before = ("absent",)
            winreg.SetValueEx(key, _FREQ_VALUE, 0, winreg.REG_DWORD, 0)
            return before
    except OSError:
        logger.warning("Could not bypass restore-point frequency throttle", exc_info=True)
        return None


def _restore_frequency_throttle(before) -> None:
    if before is None:
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _FREQ_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if before[0] == "absent":
                winreg.DeleteValue(key, _FREQ_VALUE)
            else:
                winreg.SetValueEx(key, _FREQ_VALUE, 0, before[2], before[1])
    except OSError:
        logger.error("Could not put %s back after the restore point; it is left at 0 "
                     "(no 24 h throttle)", _FREQ_VALUE, exc_info=True)


def _checkpoint(description: str, timeout: int):
    try:
        safe_desc = description.replace("'", "''")
        # Read back, never trust the exit code: Checkpoint-Computer can print a
        # WARNING (for example "already created within the past 1440 minutes")
        # and exit 0 with no restore point made. It counts only when a point
        # NEWER than every earlier one exists with exactly this description.
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "$before = @(Get-ComputerRestorePoint -ErrorAction SilentlyContinue | "
            "ForEach-Object { [int]$_.SequenceNumber }) | Measure-Object -Maximum; "
            "$floor = if ($before.Count) { [int]$before.Maximum } else { 0 }; "
            f"Checkpoint-Computer -Description '{safe_desc}' -RestorePointType 'MODIFY_SETTINGS'; "
            "$new = Get-ComputerRestorePoint | Where-Object { [int]$_.SequenceNumber -gt $floor "
            f"-and $_.Description -eq '{safe_desc}' }} | Select-Object -Last 1; "
            "if ($new) { 'RESTORE-POINT-CREATED ' + $new.SequenceNumber } "
            "else { 'RESTORE-POINT-NOT-CREATED'; exit 2 }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = (result.stdout or "") + (result.stderr or "")
        created = result.returncode == 0 and "RESTORE-POINT-CREATED" in output
        if not created:
            logger.warning("System restore point not confirmed (rc=%s): %s",
                           result.returncode, output.strip()[-400:])
        return created, output
    except Exception as e:
        logger.warning("Checkpoint-Computer failed: %s", e)
        return False, str(e)


# ── deletion ────────────────────────────────────────────────────────────────
#
# Restore points are removed through srclient.dll's SRRemoveRestorePoint, which
# takes the WMI SystemRestore SequenceNumber and returns a Win32 error code
# directly (0 == ERROR_SUCCESS). Deliberately NOT vssadmin: `vssadmin delete
# shadows` speaks in shadow-copy GUIDs, which do not map one-to-one onto the
# restore points shown in the table, and `/all` would take the newest with it.

ERROR_SUCCESS = 0
ERROR_ACCESS_DENIED = 5


def parse_restore_point_time(value) -> Optional[datetime]:
    """Parse a WMI DMTF datetime ('20260824153000.000000-000') to a datetime.

    Returns None for anything unparseable, so callers can fall back to the
    sequence number for ordering instead of crashing on odd input.
    """
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:14], "%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        return None


def _ordering_key(point: dict) -> Tuple[datetime, int]:
    """Sort key placing the most recent restore point last."""
    try:
        seq = int(point.get("SequenceNumber") or 0)
    except (TypeError, ValueError):
        seq = 0
    return (parse_restore_point_time(point.get("CreationTime")) or datetime.min, seq)


def sequence_numbers_to_prune(points: List[dict]) -> List[int]:
    """Sequence numbers of every restore point EXCEPT the most recent one.

    Ordering is by creation time with the sequence number as tie-break; points
    with no usable sequence number are skipped rather than guessed at, since a
    wrong number would delete somebody else's restore point.
    """
    usable = []
    for pt in points:
        try:
            seq = int(pt.get("SequenceNumber"))
        except (TypeError, ValueError):
            logger.debug("sequence_numbers_to_prune: skipping an item that could not be read", exc_info=True)
            continue
        usable.append((pt, seq))
    if len(usable) < 2:
        return []
    usable.sort(key=lambda item: _ordering_key(item[0]))
    return [seq for _pt, seq in usable[:-1]]


def delete_restore_point(sequence_number: int) -> Tuple[bool, str]:
    """Delete one Windows System Restore point by its WMI SequenceNumber.

    Returns (success, message). Requires elevation — without it the OS answers
    ERROR_ACCESS_DENIED, which is reported rather than swallowed.
    """
    import ctypes

    try:
        srclient = ctypes.WinDLL("srclient.dll")
        srclient.SRRemoveRestorePoint.argtypes = [ctypes.c_uint32]
        srclient.SRRemoveRestorePoint.restype = ctypes.c_uint32
        code = int(srclient.SRRemoveRestorePoint(ctypes.c_uint32(int(sequence_number))))
    except Exception as e:
        logger.warning("SRRemoveRestorePoint(%s) raised", sequence_number, exc_info=True)
        return False, str(e)

    if code == ERROR_SUCCESS:
        return True, ""
    if code == ERROR_ACCESS_DENIED:
        return False, "Access denied — run the tool as Administrator."
    try:
        return False, f"{ctypes.FormatError(code).strip()} (error {code})"
    except Exception:
        return False, f"Windows error {code}"


def delete_restore_points(sequence_numbers: List[int]) -> Tuple[int, List[Tuple[int, str]]]:
    """Delete several restore points. Returns (deleted_count, [(seq, error), …]).

    Keeps going after a failure so one locked point does not abandon the rest.
    """
    deleted = 0
    failures: List[Tuple[int, str]] = []
    for seq in sequence_numbers:
        ok, message = delete_restore_point(seq)
        if ok:
            deleted += 1
        else:
            failures.append((seq, message))
    return deleted, failures
