"""MTP device scanner via the Windows Shell namespace.

MTP (Media Transfer Protocol) devices — phones, cameras, media players — do
not get drive letters and are invisible to FindFirstFileExW. Windows exposes
them through the Shell namespace (IShellFolder / Shell.Application COM), which
is what Explorer itself uses to browse them.

Measured on a OnePlus 15 over USB: ~208 items/s, 22,861 items in 110 s.
MTP is inherently slow; progress reporting and cancellation are essential.
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import logging

import pythoncom
import win32com.client

from ..store.node_store import DIR
from ..targets.base import unix_to_filetime

logger = logging.getLogger(__name__)
_THIS_PC = "::{20d04fe0-"


@dataclass
class MtpDevice:
    """A connected MTP device discovered through Shell namespace."""

    name: str
    shell_path: str
    capacity: int
    free_space: int

    @property
    def used(self) -> int:
        return max(0, self.capacity - self.free_space) if self.capacity else 0


def _property(item, name):
    try:
        return item.ExtendedProperty("System." + name)
    except Exception as exc:
        logger.debug("MTP property %s unavailable: %s", name, exc)
        return None


def _byte_count(value) -> int:
    try:
        return max(0, min(int(value), (1 << 63) - 1))
    except (TypeError, ValueError, OverflowError):
        return 0


def _folder_for_path(shell, shell_path):
    folder = shell.NameSpace(shell_path)
    if folder is not None:
        return folder
    # Some Shell providers cannot bind a device parsing name directly.
    computer = shell.NameSpace(17)
    if computer is not None:
        for item in computer.Items():
            if str(item.Path).casefold() == shell_path.casefold():
                return item.GetFolder
    return None


def _folder_storage(folder) -> tuple[int, int]:
    if folder is None:
        return 0, 0
    # A storage path may be supplied directly instead of its device path.
    own = folder.Self
    capacity = _byte_count(_property(own, "Capacity")) if own is not None else 0
    if capacity:
        return capacity, _byte_count(_property(own, "FreeSpace"))
    total, free = 0, 0
    for storage in folder.Items():
        total += _byte_count(_property(storage, "Capacity"))
        free += _byte_count(_property(storage, "FreeSpace"))
    return total, free


def mtp_device_storage(shell_path: str) -> tuple[int, int]:
    """Return combined (capacity, free_space) for a device's storages.

    MUST be called from a COM-initialized thread.
    """
    shell = win32com.client.Dispatch("Shell.Application")
    return _folder_storage(_folder_for_path(shell, shell_path))


def list_mtp_devices() -> list[MtpDevice]:
    """Discover portable devices in This PC, initializing COM on this thread.

    Type names are localized, so USB parsing paths also identify devices.
    No COM objects escape this function. Discovery failures are nonfatal.
    """
    initialized = False
    devices = []
    shell = computer = item = folder = None
    try:
        pythoncom.CoInitialize()
        initialized = True
        shell = win32com.client.Dispatch("Shell.Application")
        computer = shell.NameSpace(17)
        if computer is None:
            return []
        for item in computer.Items():
            try:
                path = str(item.Path)
                # Read Type separately: unavailable localized text must not
                # hide a device whose parsing name already identifies it.
                try:
                    kind = str(item.Type).casefold()
                except Exception as exc:
                    logger.debug("MTP device type unavailable: %s", exc)
                    kind = ""
                usb = path.casefold().startswith(_THIS_PC) and "usb#" in path.casefold()
                if not usb and not any(word in kind for word in ("phone", "portable", "camera")):
                    continue
                name = str(item.Name)
                capacity = free = 0
                try:
                    folder = item.GetFolder
                    capacity, free = _folder_storage(folder)
                except Exception as exc:
                    logger.warning("MTP storage metadata unavailable for %s: %s", name, exc)
                devices.append(MtpDevice(name, path, capacity, free))
            except Exception as exc:
                logger.warning("Could not inspect portable device: %s", exc)
    except Exception as exc:
        logger.warning("MTP discovery failed: %s", exc)
    finally:
        # Release apartment-owned proxies before tearing down COM.
        shell = computer = item = folder = None
        if initialized:
            pythoncom.CoUninitialize()
    return devices


def _modified_time(value) -> int:
    if not value:
        return 0
    if isinstance(value, datetime):
        try:
            return unix_to_filetime(value.timestamp())
        except (OSError, OverflowError, ValueError):
            return 0
    # Explorer may embed direction marks and nonbreaking spaces in dates.
    text = str(value).translate(str.maketrans({"\u200e": "", "\u200f": "",
                                             "\u00a0": " ", "\u202f": " "})).strip()
    formats = ("%x %X", "%c", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
               "%d.%m.%Y %H:%M:%S", "%d/%m/%Y %I:%M:%S %p",
               "%m/%d/%Y %I:%M:%S %p", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M")
    try:
        return unix_to_filetime(datetime.fromisoformat(text).timestamp())
    except (OSError, OverflowError, ValueError):
        logger.debug("MTP date is not ISO formatted: %r", text)
    for fmt in formats:
        try:
            return unix_to_filetime(datetime.strptime(text, fmt).timestamp())
        except (OSError, OverflowError, ValueError):
            continue
    logger.debug("MTP modification date unrecognized: %r", value)
    return 0


def mtp_enumerate(shell_path: str, store, root: int,
                  on_batch=None, should_cancel=None,
                  batch_size: int = 200) -> tuple[int, int, list]:
    """BFS walk into a NodeStore; call only on a COM-initialized thread.

    Device storage folders remain children of the device root. Cancellation
    is checked between folders. Batches contain newly added index ranges.
    Files have alloc=size; directories have zero size until rollup.
    Returns (node_count, error_count, errors), retaining at most 100 details.
    """
    errors = []
    error_count = 0

    def record_error(path, reason):
        nonlocal error_count
        error_count += 1
        if len(errors) < 100:
            errors.append((path, str(reason)))

    batch_size = max(1, batch_size)
    batch_start = len(store)
    shell = win32com.client.Dispatch("Shell.Application")
    queue = deque([(root, None, store.path(root))])
    while queue:
        if should_cancel and should_cancel():
            break
        parent, folder_item, path = queue.popleft()
        try:
            folder = (folder_item.GetFolder if folder_item is not None
                      else _folder_for_path(shell, shell_path))
            if folder is None:
                record_error(path, "Shell folder unavailable (device may be locked or disconnected)")
                continue
            for item in folder.Items():
                item_path = path
                try:
                    name = str(item.Name)
                    item_path = path + "\\" + name
                    is_dir = bool(item.IsFolder)
                    size = 0
                    if not is_dir:
                        raw_size = _property(item, "Size")
                        size = _byte_count(raw_size)
                        if raw_size is None:
                            record_error(item_path, "File size unavailable")
                    mtime = _modified_time(_property(item, "DateModified"))
                    child = store.add(parent, name, size=size, alloc=size,
                                      mtime=mtime, attrs=DIR if is_dir else 0)
                    if is_dir:
                        queue.append((child, item, item_path))
                except Exception as exc:
                    record_error(item_path, exc)
                    continue
                if on_batch and len(store) - batch_start >= batch_size:
                    on_batch((batch_start, len(store)))
                    batch_start = len(store)
        except Exception as exc:
            record_error(path, exc)
    store.build_child_lists()
    if on_batch and len(store) > batch_start:
        on_batch((batch_start, len(store)))
    return len(store), error_count, errors
