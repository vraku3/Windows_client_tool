"""Machine-wide operation coordination via a named Windows mutex. Qt-free."""
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

MUTEX_NAME = "Global\\WinClientTool_OperationLock"
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_WAIT_ABANDONED = 0x80

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
_kernel32.ReleaseMutex.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL

# Windows mutexes are recursive on the owning thread; the gate prevents a
# second instance or operation in this process from entering recursively.
_process_gate = threading.Lock()
_active_lock = None


def _sidecar() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "WindowsTweaker" / "operation_lock.json"


def current_owner() -> Optional[str]:
    active = _active_lock
    if active is not None:
        return active.owner
    try:
        data = json.loads(_sidecar().read_text(encoding="utf-8"))
        owner = data.get("owner") if isinstance(data, dict) else None
        return owner if isinstance(owner, str) else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Could not read operation owner", exc_info=True)
        return None


def _write_owner(owner: str) -> None:
    temporary = None
    try:
        path = _sidecar()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="operation_lock.", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump({"owner": owner, "pid": os.getpid(), "started": time.time()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError):
        logger.warning("Could not write operation owner", exc_info=True)
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary operation owner file", exc_info=True)


class OperationLock:
    """A mutex acquisition must be released by the same thread that made it."""

    def __init__(self):
        self._handle = None
        self._owner: Optional[str] = None
        self._thread = None

    def try_acquire(self, owner: str, timeout_ms: int = 0) -> bool:
        global _active_lock
        if not isinstance(timeout_ms, int) or not 0 <= timeout_ms < 0xFFFFFFFF:
            raise ValueError("timeout_ms must be a finite nonnegative DWORD")
        active = _active_lock
        if active is not None and active.owned_by_current_thread:
            return False
        deadline = time.monotonic() + timeout_ms / 1000
        if not _process_gate.acquire(timeout=timeout_ms / 1000):
            return False
        handle = None
        acquired = False
        try:
            handle = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            result = _kernel32.WaitForSingleObject(handle, remaining)
            if result == _WAIT_TIMEOUT:
                return False
            if result not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
                raise ctypes.WinError(ctypes.get_last_error())
            if result == _WAIT_ABANDONED:
                logger.warning("Previous operation holder exited without releasing the mutex")
            self._handle, self._owner, self._thread = handle, owner, threading.get_ident()
            _active_lock = self
            acquired = True
            _write_owner(owner)
            return True
        finally:
            if not acquired:
                if handle and not _kernel32.CloseHandle(handle):
                    logger.warning("Could not close operation mutex handle: %s", ctypes.get_last_error())
                _process_gate.release()

    def release(self) -> None:
        global _active_lock
        if not self.held:
            return
        if not self.owned_by_current_thread:
            raise RuntimeError("Operation lock must be released on its acquiring thread")
        # Remove metadata while still owning the mutex so a new holder's
        # sidecar cannot be removed by this operation's cleanup.
        try:
            _sidecar().unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove operation owner", exc_info=True)
        if not _kernel32.ReleaseMutex(self._handle):
            raise ctypes.WinError(ctypes.get_last_error())
        handle = self._handle
        self._handle = self._owner = self._thread = None
        _active_lock = None
        try:
            if not _kernel32.CloseHandle(handle):
                logger.warning("Could not close operation mutex handle: %s", ctypes.get_last_error())
        finally:
            _process_gate.release()

    @property
    def held(self) -> bool:
        return self._handle is not None

    @property
    def owner(self) -> Optional[str]:
        return self._owner

    @property
    def owned_by_current_thread(self) -> bool:
        return self.held and self._thread == threading.get_ident()


class OperationBusy(RuntimeError):
    def __init__(self, owner: str = ""):
        self.owner = owner
        super().__init__(f"Operation in progress: {owner or 'another WinClientTool operation'}")


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except (ImportError, ValueError, TypeError):
        return False


def active_owner() -> Optional[str]:
    """Owner of a LIVE operation, in this process or another one.

    The sidecar is best effort, so a dead holder (crash, killed app) must not
    keep the UI claiming an operation is running: its PID is checked before
    the owner is believed.
    """
    active = _active_lock
    if active is not None and active.held:
        return active.owner
    try:
        data = json.loads(_sidecar().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Could not read the operation owner", exc_info=True)
        return None
    owner, pid = data.get("owner"), data.get("pid")
    if not isinstance(owner, str) or not isinstance(pid, int):
        return None
    if pid == os.getpid():
        return None            # this process, but nothing is held: stale
    return owner if _pid_alive(pid) else None


lock = OperationLock()


@contextmanager
def operation_lock(owner: str, timeout_ms: int = 5000):
    if not lock.try_acquire(owner, timeout_ms):
        raise OperationBusy(current_owner() or "")
    try:
        yield lock
    finally:
        lock.release()
