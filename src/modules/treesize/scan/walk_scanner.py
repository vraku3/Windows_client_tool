"""Directory walk fallback using FindFirstFileExW.

FindExInfoBasic skips 8.3 name resolution and FIND_FIRST_EX_LARGE_FETCH
batches directory entries, together giving far fewer kernel transitions
than os.scandir on deep trees.
"""
import ctypes
import os
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

from ..store.node_store import (
    NodeStore, COMPRESSED, DIR, HIDDEN, REPARSE, SPARSE,
)
from .mft_reader import BATCH_INTERVAL

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_NO_MORE_FILES = 18
# Unix epoch expressed in the FILETIME epoch (1601-01-01), and its tick rate.
# Every timestamp in the store is a FILETIME; os.stat is the one place a Unix
# timestamp gets in, so it converts here rather than anywhere downstream.
FILETIME_EPOCH_OFFSET = 11_644_473_600
FILETIME_TICKS_PER_SECOND = 10_000_000
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_HIDDEN = 0x2
# These two are WIN32 bits, and they are NOT the node flags of the same name
# (node COMPRESSED is 0x10, node SPARSE is 0x20). The two words overlap
# numerically and mean different things -- ruling F4 -- so every one of these
# is translated below, never copied.
FILE_ATTRIBUTE_SPARSE_FILE = 0x200
FILE_ATTRIBUTE_COMPRESSED = 0x800
FindExInfoBasic = 1
FindExSearchNameMatch = 0
FIND_FIRST_EX_LARGE_FETCH = 2

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    @property
    def value(self) -> int:
        return (self.high << 32) | self.low


class WIN32_FIND_DATAW(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", FILETIME),
        ("ftLastAccessTime", FILETIME),
        ("ftLastWriteTime", FILETIME),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("dwReserved0", wintypes.DWORD),
        ("dwReserved1", wintypes.DWORD),
        ("cFileName", wintypes.WCHAR * 260),
        ("cAlternateFileName", wintypes.WCHAR * 14),
    ]


_kernel32.FindFirstFileExW.restype = wintypes.HANDLE
_kernel32.FindFirstFileExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.LPVOID,
                                       ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
_kernel32.FindNextFileW.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
_kernel32.FindClose.argtypes = [wintypes.HANDLE]


def to_filetime(seconds: float) -> int:
    if not seconds or seconds < 0:
        return 0
    return int((seconds + FILETIME_EPOCH_OFFSET) * FILETIME_TICKS_PER_SECOND)


def root_timestamps(path: str) -> tuple[int, int, int]:
    """(mtime, ctime, atime) for the scan root, as FILETIMEs.

    The root is added before the walk begins, so no DirEntry ever describes
    it -- which is why selecting the scanned folder used to show a dash for
    every date. A root that cannot be stat-ed still scans: a timestamp is
    decoration, and losing the whole scan over one would not be.
    """
    try:
        info = os.stat(path)
    except OSError:
        return 0, 0, 0
    return (to_filetime(info.st_mtime), to_filetime(info.st_ctime),
            to_filetime(info.st_atime))


@dataclass(frozen=True)
class DirEntry:
    name: str
    is_dir: bool
    is_reparse: bool
    is_hidden: bool
    size: int
    ctime: int
    mtime: int
    atime: int
    #: Free: they arrive in the same WIN32_FIND_DATAW record as the size. Spec
    #: 3.3 declines the per-file GetCompressedFileSize call that would give a
    #: correct `alloc` here, because that one costs more than the walk itself
    #: -- but that is an argument about `alloc`, and it does not reach these.
    #: Defaulted so that the many callers building a DirEntry by hand, tests
    #: included, do not all have to say "not compressed, not sparse".
    is_compressed: bool = False
    is_sparse: bool = False


def _long_path(path: str) -> str:
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def list_directory(path: str, on_error=None) -> list[DirEntry]:
    """Entries in `path`, or [] if it could not be opened.

    An empty list is ambiguous by itself -- an empty folder and a folder we were
    denied look identical -- so a caller that cares passes `on_error(path, why)`
    and learns which it was. Without that, a locked subtree silently contributes
    zero bytes to a total the user is expected to trust.
    """
    data = WIN32_FIND_DATAW()
    handle = _kernel32.FindFirstFileExW(
        _long_path(os.path.join(path, "*")), FindExInfoBasic, ctypes.byref(data),
        FindExSearchNameMatch, None, FIND_FIRST_EX_LARGE_FETCH)
    if not handle or handle == INVALID_HANDLE_VALUE:
        if on_error is not None:
            on_error(path, ctypes.FormatError(ctypes.get_last_error()).strip())
        return []
    out: list[DirEntry] = []
    try:
        while True:
            name = data.cFileName
            if name not in (".", ".."):
                attrs = data.dwFileAttributes
                out.append(DirEntry(
                    name=name,
                    is_dir=bool(attrs & FILE_ATTRIBUTE_DIRECTORY),
                    is_reparse=bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT),
                    is_hidden=bool(attrs & FILE_ATTRIBUTE_HIDDEN),
                    is_compressed=bool(attrs & FILE_ATTRIBUTE_COMPRESSED),
                    is_sparse=bool(attrs & FILE_ATTRIBUTE_SPARSE_FILE),
                    size=(data.nFileSizeHigh << 32) | data.nFileSizeLow,
                    ctime=data.ftCreationTime.value,
                    mtime=data.ftLastWriteTime.value,
                    atime=data.ftLastAccessTime.value,
                ))
            if not _kernel32.FindNextFileW(handle, ctypes.byref(data)):
                # 18 (ERROR_NO_MORE_FILES) is the end; anything else -- a share
                # dropping, an I/O error -- cut the listing short, and a total
                # built on it must not look complete.
                err = ctypes.get_last_error()
                if err != ERROR_NO_MORE_FILES and on_error is not None:
                    on_error(path, "listing stopped early: "
                             + ctypes.FormatError(err).strip())
                break
    finally:
        _kernel32.FindClose(handle)
    return out


class WalkScanner:
    MAX_RECORDED_ERRORS = 100

    def __init__(self, root_path: str, bytes_per_cluster: int = 4096,
                 max_workers: int | None = None,
                 exclude: Callable[..., bool] | None = None,
                 owner_resolver=None,
                 clock=time.monotonic) -> None:
        self.root_path = os.path.abspath(root_path)
        self.bytes_per_cluster = bytes_per_cluster
        # Reserved: the walk is single-threaded today. Kept so the threading
        # work deferred out of phase 1 does not change this constructor.
        self.max_workers = max_workers or min(32, (os.cpu_count() or 4) * 4)
        self.exclude = exclude
        # None means "do not read owners", which is the default: reading one
        # costs a security call per file, and the Users view is not what most
        # scans are for (spec 5.8).
        self.owner_resolver = owner_resolver
        self._clock = clock
        self.root = -1
        # Directories that could not be listed. The list is capped so a scan of
        # a heavily locked-down tree cannot balloon; error_count is not, so the
        # true scale of the gap is never lost.
        self.errors: list[tuple[str, str]] = []
        self.error_count = 0

    def _owner_of(self, path: str, store: NodeStore) -> int:
        """Interned owner index, or -1 when owners are not being collected.

        An unreadable owner also gives -1: interning "" would hand the Users
        view a nameless bucket that looks like a real account.
        """
        if self.owner_resolver is None:
            return -1
        name = self.owner_resolver.for_path(path)
        return store.intern_owner(name) if name else -1

    def _record_error(self, path: str, why: str) -> None:
        self.error_count += 1
        if len(self.errors) < self.MAX_RECORDED_ERRORS:
            self.errors.append((path, why))

    def _alloc_for(self, size: int) -> int:
        if size == 0:
            return 0
        c = self.bytes_per_cluster
        return ((size + c - 1) // c) * c

    def scan(self, store: NodeStore, on_batch=None, should_cancel=None,
             wait_if_paused=None, batch_size: int = 500) -> int:
        mtime, ctime, atime = root_timestamps(self.root_path)
        self.root = store.add(-1, self.root_path, attrs=DIR,
                              mtime=mtime, ctime=ctime, atime=atime,
                              owner_id=self._owner_of(self.root_path, store))
        queue: deque[tuple[int, str]] = deque([(self.root, self.root_path)])
        batch_start = len(store)
        batch_opened = self._clock()
        while queue:
            if wait_if_paused:
                wait_if_paused()
            if should_cancel and should_cancel():
                break
            node, path = queue.popleft()
            for entry in list_directory(path, on_error=self._record_error):
                flags = 0
                if entry.is_dir:
                    flags |= DIR
                if entry.is_reparse:
                    flags |= REPARSE
                if entry.is_hidden:
                    flags |= HIDDEN
                if entry.is_compressed:
                    flags |= COMPRESSED
                if entry.is_sparse:
                    flags |= SPARSE
                size = 0 if entry.is_dir else entry.size
                if self.exclude is not None and self.exclude(
                        entry.name, size, flags, entry.mtime,
                        os.path.join(path, entry.name)):
                    continue
                child_path = os.path.join(path, entry.name)
                idx = store.add(node, entry.name, size=size,
                                alloc=self._alloc_for(size),
                                mtime=entry.mtime, ctime=entry.ctime,
                                atime=entry.atime, attrs=flags,
                                owner_id=self._owner_of(child_path, store))
                if entry.is_dir and not entry.is_reparse:
                    queue.append((idx, child_path))
            if on_batch and len(store) > batch_start and (
                    len(store) - batch_start >= batch_size
                    or self._clock() - batch_opened >= BATCH_INTERVAL):
                on_batch((batch_start, len(store)))
                batch_start = len(store)
                batch_opened = self._clock()
        store.build_child_lists()
        if on_batch and len(store) > batch_start:
            on_batch((batch_start, len(store)))
        return len(store)
