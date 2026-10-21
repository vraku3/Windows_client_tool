"""Guard every action with fresh metadata and content, then measure its outcome."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from ctypes import wintypes
from datetime import datetime

from core.security_vendors import SECURITY_VENDORS
from core.game_folders import GAME_FOLDERS

from modules.cleanup import cleanup_history
from modules.cleanup.cleanup_scanner import ScanItem, known_folders
from modules.cleanup.cleanup_scanner.deleter import DeleteReport, delete_path
from modules.cleanup.cleanup_scanner.scanners_system import _protected_entry, _refusal_reason
from .enumerate import CLOUD_MASK, system_folders, unsafe_ancestors
from .finder import ScanCancelled, dhash, hash_file, safe_snapshot
from .models import canonical_path, under, validate_selection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionOutcome:
    path: str
    status: str
    reason: str
    freed: int = 0


@dataclass
class ActionResult:
    outcomes: list[ActionOutcome] = field(default_factory=list)
    freed: int = 0
    cancelled: bool = False


#: Security software is never moved or deleted, whatever was scanned or confirmed -- a
#: duplicate component can still be the one its service or driver registration points at.
#: Matched as a folder-name segment, case-insensitive.
SECURITY_FOLDERS = SECURITY_VENDORS


def always_refused(path: str):
    """Reasons no confirmation can override (security software, the Windows folder,
    the owner's Cleanup exclusions, AI model caches)."""
    norm = canonical_path(path)
    windir = canonical_path(os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows")
    if under(norm, windir):
        return "Files inside the Windows folder are never acted on (Windows keeps its duplicates on purpose)"
    parts = {p.lower() for p in norm.split(os.sep)}
    if any(name in parts for name in SECURITY_FOLDERS) or os.path.basename(norm).startswith(("sysmon", "sysmondrv")):
        return "Security software is never moved or deleted"
    try:
        from modules.cleanup.cleanup_scanner import exclusions
        if exclusions.is_excluded(path, exclusions.list_exclusions_strict()):
            return "Excluded in Cleanup settings"
    except (OSError, ValueError) as error:          # unreadable exclusions: refuse, never guess
        logger.warning("duplicates: Cleanup exclusions unreadable: %s", error)
        return "Cleanup exclusions could not be read"
    from modules.cleanup.cleanup_scanner.recommend import is_protected
    if is_protected(path, os.path.expanduser("~")):
        return "AI model caches are never acted on"
    return None


class _StreamData(ctypes.Structure):
    _fields_ = [("StreamSize", ctypes.c_longlong), ("cStreamName", wintypes.WCHAR * 296)]


def _stream_reason(path: str):
    """Content hashes cover only the default stream; preserve other file data."""
    if os.name != "nt":
        return "Alternate data streams could not be checked on this platform"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FindFirstStreamW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                       ctypes.POINTER(_StreamData), wintypes.DWORD]
    kernel.FindFirstStreamW.restype = wintypes.HANDLE
    kernel.FindNextStreamW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_StreamData)]
    kernel.FindNextStreamW.restype = wintypes.BOOL
    kernel.FindClose.argtypes = [wintypes.HANDLE]
    kernel.FindClose.restype = wintypes.BOOL
    data = _StreamData()
    handle = kernel.FindFirstStreamW(path, 0, ctypes.byref(data), 0)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        # ERROR_HANDLE_EOF means the enumeration succeeded with no streams.
        return None if error == 38 else f"Alternate data streams could not be read (error {error})"
    try:
        while True:
            if data.cStreamName.casefold() not in ("::$data", ":zone.identifier:$data"):
                return f"Named alternate data stream: {data.cStreamName}"
            if not kernel.FindNextStreamW(handle, ctypes.byref(data)):
                error = ctypes.get_last_error()
                return None if error == 38 else f"Alternate data streams could not be read (error {error})"
    finally:
        kernel.FindClose(handle)


def protected_reason(entry, *, scanned_roots=(), confirm_system=False):
    path = entry.path
    hard = always_refused(path)
    if hard:
        return hard
    if any(folder in canonical_path(path).replace("/", "\\").lower() for folder in GAME_FOLDERS):
        return "Game installation files are never acted on"
    current = safe_snapshot(entry)
    if current.attrs & CLOUD_MASK or known_folders.is_cloud_placeholder(path):
        return "Cloud placeholders cannot be acted on"
    reason = _stream_reason(path)
    if reason:
        return reason
    if _protected_entry(path, False):
        return "Protected mailbox or sign-in file"
    reason = _refusal_reason(ScanItem(path=path, size=entry.size, is_dir=False))
    if reason:
        return "Protected: " + reason
    # Explicit scanning means a root INSIDE that system folder, not an entire C:.
    # AppData/ProgramData need the same conservative confirmation as Windows.
    for system in system_folders():
        if under(path, system):
            if not confirm_system or not any(under(root, system) and under(path, root)
                                             for root in scanned_roots):
                return "System folder requires an explicit root and separate confirmation"
    return None


def verify_pair(keeper, target, *, cancelled=lambda: False, scanned_roots=(), confirm_system=False,
                method="exact", image_threshold=5):
    """Fresh exact comparison, or fresh perceptual distance for manually selected images."""
    try:
        for entry in (keeper, target):
            reason = protected_reason(entry, scanned_roots=scanned_roots, confirm_system=confirm_system)
            if reason:
                return reason
        if keeper.identity == target.identity:
            return "Hard links identify the same physical file"
        if method == "images":
            left, right = int(dhash(keeper, cancelled), 16), int(dhash(target, cancelled), 16)
            if (left ^ right).bit_count() > image_threshold:
                return "Images no longer match the scanned similarity threshold"
            safe_snapshot(keeper)
            safe_snapshot(target)
            return None
        if keeper.size != target.size:
            return "Candidate is not an exact content match"
        if hash_file(keeper, cancelled=cancelled) != hash_file(target, cancelled=cancelled):
            return "Candidate is not an exact content match"
        safe_snapshot(keeper)
        safe_snapshot(target)
        return None
    except OSError as exc:
        return str(exc)


def recycle_file(path: str):
    """Force recycling, including where legacy ALLOWUNDO would permit a hard delete."""
    if os.name != "nt":
        raise OSError("Recycle Bin is available on Windows only")
    import pythoncom
    import pywintypes
    from win32com.shell import shell
    # COM is initialized on this worker thread, and released before returning.
    pythoncom.CoInitialize()
    op = item = None
    try:
        op = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None,
                                        pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
        # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI, plus
        # FOFX_RECYCLEONDELETE (Windows 8+) and FOFX_EARLYFAILURE.
        # FOF_NO_CONNECTED_ELEMENTS: report.html must not take report_files with it.
        op.SetOperationFlags(0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x2000 | 0x00080000 | 0x00100000)
        item = shell.SHCreateItemFromParsingName(os.path.abspath(path), None, shell.IID_IShellItem)
        op.DeleteItem(item, None)
        op.PerformOperations()
        if op.GetAnyOperationsAborted():
            raise OSError("Recycle Bin operation was aborted")
    except pywintypes.com_error as exc:
        raise OSError(f"Recycle Bin refused the operation: {exc}") from exc
    finally:
        op = item = None
        pythoncom.CoUninitialize()


def _journal_move(path, target, state, error=None):
    folder = os.path.join(os.path.dirname(cleanup_history._history_path()), "cleanup")
    os.makedirs(folder, exist_ok=True)
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "src": path, "dest": target, "state": state}
    if error is not None:
        entry["error"] = str(error)
    with open(os.path.join(folder, "duplicate_moves.jsonl"), "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _move_file(path, destination):
    # A unique leaf also prevents two same-named duplicates from colliding.
    target = os.path.join(destination, f"{uuid.uuid4().hex[:12]}_{os.path.basename(path)}")
    if os.path.lexists(target):
        raise FileExistsError(target)
    _journal_move(path, target, "moving")  # durable before the source can disappear
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        kernel.MoveFileExW.restype = wintypes.BOOL
        # COPY_ALLOWED keeps alternate streams when crossing volumes; WRITE_THROUGH
        # waits for that copy before success. No REPLACE_EXISTING: never overwrite.
        if not kernel.MoveFileExW(path, target, 0x2 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    except OSError as error:
        _journal_move(path, target, "failed", error)
        raise
    _journal_move(path, target, "moved")
    return target


def perform_actions(groups, selected, *, mode="recycle", destination="", action=None,
                    cancelled=lambda: False, scanned_roots=(), confirm_system=False,
                    on_outcome=None, record=True, image_threshold=5) -> ActionResult:
    """Inject `action(path)` in tests. It must really remove/move its temp source.

    Full comparison is intentionally fresh even for an exact cached scan. Recycling
    and same-volume moves free zero; permanent removal uses the measured deleter.
    Audit recording happens inside this routine, so cancellation cannot lose it.
    """
    selected = set(selected)
    validate_selection(groups, selected)
    if mode not in ("recycle", "move", "delete"):
        raise ValueError("Unknown duplicate action")
    if mode == "move":
        destination = os.path.abspath(destination)
        if not os.path.isdir(destination) or unsafe_ancestors(destination):
            raise ValueError("Move destination must be a local, ordinary folder")
        if any(under(f.path, destination) for g in groups for f in g.files if f.path in selected):
            raise ValueError("Move destination already contains a selected source")
    result = ActionResult()
    removed, refused = [], []
    report = DeleteReport()
    try:
        for group in groups:
            keepers = [f for f in group.files if f.path not in selected]
            if not keepers:
                continue
            keeper = keepers[0]
            for target in group.files:
                if target.path not in selected:
                    continue
                if cancelled():
                    result.cancelled = True
                    break
                try:
                    reason = verify_pair(keeper, target, cancelled=cancelled,
                                         scanned_roots=scanned_roots, confirm_system=confirm_system,
                                         method=group.method, image_threshold=image_threshold)
                    if reason:
                        outcome = ActionOutcome(target.path, "skipped", reason)
                        refused.append((target.path, reason))
                    else:
                        # The final guard is deliberately adjacent to the operation.
                        safe_snapshot(keeper)
                        fresh = safe_snapshot(target)
                        freed = 0
                        moved_to = ""
                        if action is not None:
                            action(target.path)
                        elif mode == "delete":
                            item_report = delete_path(target.path, False, cancelled=cancelled,
                                                      skip=_protected_entry)
                            freed = item_report.freed if fresh.nlink <= 1 else 0
                            report.locked.extend(item_report.locked)
                            report.denied.extend(item_report.denied)
                        elif mode == "move":
                            same_volume = fresh.dev == os.stat(destination).st_dev
                            # The moved file gets a unique name: record it, or nobody can
                            # find the file again from the result or the history.
                            moved_to = _move_file(target.path, destination)
                            if not same_volume and fresh.nlink <= 1:
                                freed = fresh.on_disk
                        else:
                            recycle_file(target.path)
                        try:
                            os.lstat(target.path)
                        except FileNotFoundError:
                            gone = True
                        else:
                            gone = False
                        if not gone:
                            outcome = ActionOutcome(target.path, "failed", "Source still exists after the action")
                        else:
                            how = f"moved to {moved_to}" if moved_to else mode
                            outcome = ActionOutcome(target.path, "done", how, freed)
                            result.freed += freed
                            report.freed += freed
                            report.files += 1
                            removed.append((f"{target.path} -> {moved_to}" if moved_to else target.path, freed))
                            if moved_to:
                                logger.info("duplicates: moved %s -> %s", target.path, moved_to)
                except ScanCancelled:
                    result.cancelled = True
                    break
                except (OSError, ValueError) as exc:
                    logger.warning("Duplicate action failed on %s: %s", target.path, exc)
                    outcome = ActionOutcome(target.path, "failed", str(exc))
                if outcome.status == "failed":
                    refused.append((target.path, f"{mode}: {outcome.reason}"))
                result.outcomes.append(outcome)
                if on_outcome:
                    on_outcome(outcome)
            if result.cancelled:
                break
    finally:
        report.cancelled = result.cancelled or cancelled()
        result.cancelled = report.cancelled
        if result.cancelled:
            attempted = {o.path for o in result.outcomes}
            for path in sorted(selected - attempted):
                outcome = ActionOutcome(path, "skipped", "Cancelled before action")
                result.outcomes.append(outcome)
                refused.append((path, outcome.reason))
                if on_outcome:
                    on_outcome(outcome)
        if record:
            cleanup_history.record_report(report, refused, removed=removed)
            if result.freed:
                # Record here, even if Qt has destroyed the tab or suppresses a
                # cancelled Worker's result. The tab forwards session totals only.
                cleanup_history.record(result.freed)
    return result
