"""A native, cancellable "tell me the instant a new file appears in this
folder" watcher -- `ReadDirectoryChangesW` with overlapped I/O, not
`QFileSystemWatcher` + directory diffing, which can miss a fast
create/delete pair on a churning temp folder.

`worker.is_cancelled` alone cannot stop this: the blocking wait is a
native WaitForMultipleObjects call, which nothing about `Worker.cancel()`
touches. `stop()` sets a real Win32 event this class also waits on, the
same class of fix this session already applied to DISM and winget calls
that could not be cancelled mid-operation.
"""
import os
from typing import Callable

import pywintypes
import win32con
import win32event
import win32file

FILE_LIST_DIRECTORY = 0x0001
_BUFFER_SIZE = 64 * 1024


class FolderWatcher:
    def __init__(self, path: str, on_created: Callable[[str], None],
                recursive: bool = False):
        self._path = path
        self._on_created = on_created
        self._recursive = recursive
        self._stop_event = win32event.CreateEvent(None, True, False, None)

    def run(self, worker) -> None:
        try:
            handle = win32file.CreateFile(
                self._path, FILE_LIST_DIRECTORY,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                None, win32con.OPEN_EXISTING,
                win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED,
                None,
            )
        except pywintypes.error as exc:
            # pywintypes.error does NOT subclass OSError (confirmed: its MRO
            # is (error, Exception, BaseException, object)) -- callers
            # catching the standard "this path is no good" exception would
            # miss it entirely. winerror/strerror line up with OSError's own
            # errno/strerror fields, so translate rather than let a
            # Win32-specific type leak into a caller that reasonably expects
            # OSError for a bad path.
            raise OSError(exc.winerror, exc.strerror, self._path) from exc
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        buf = win32file.AllocateReadBuffer(_BUFFER_SIZE)
        try:
            while not worker.is_cancelled:
                win32file.ReadDirectoryChangesW(
                    handle, buf, self._recursive,
                    win32con.FILE_NOTIFY_CHANGE_FILE_NAME,
                    overlapped,
                )
                rc = win32event.WaitForMultipleObjects(
                    [overlapped.hEvent, self._stop_event], False, win32event.INFINITE)
                if rc == win32event.WAIT_OBJECT_0 + 1:
                    break  # stop() was called
                nbytes = win32file.GetOverlappedResult(handle, overlapped, False)
                for action, filename in win32file.FILE_NOTIFY_INFORMATION(buf, nbytes):
                    if action == 1:  # FILE_ACTION_ADDED
                        self._on_created(os.path.join(self._path, filename))
        finally:
            win32file.CloseHandle(handle)

    def stop(self) -> None:
        """Thread-safe -- call from the UI thread while run() is blocking
        on a worker thread."""
        win32event.SetEvent(self._stop_event)
