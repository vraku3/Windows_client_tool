r"""Open handles, per process -- the lower pane's Handles tab.

`NtQuerySystemInformation(SystemExtendedHandleInformation)` returns every
handle on the machine in one call: **169,153 handles in 8 ms** here. There
is no per-process variant, so a pane showing one process filters the whole
list, which is still far cheaper than any alternative.

Three things this module exists to get right, each of which the previous
implementation got wrong:

**The status code is unsigned.** `NtQuerySystemInformation` answers its
first call with `STATUS_INFO_LENGTH_MISMATCH` by design -- you cannot know
the buffer size in advance -- and the caller grows the buffer and retries.
With no `restype` declared, ctypes hands back a SIGNED int, so the status
reads as `-1073741820`, never equals `0xC0000004`, and the retry never
fires. The previous handle view had exactly this, which is why its tab
showed **zero handles for every process, always**: the kernel said this
process holds 185 and the pane said 0.

**The pid field must be full width.** The classic
`SystemHandleInformation` (class 16) stores `UniqueProcessId` as a
`USHORT`. Windows pids are DWORDs; this machine is currently at 35,612 and
climbing, and past 65,535 that field silently wraps and attributes one
process's handles to another. Class 64 has a full-width pid for the same
8 ms, so there is no reason to use the narrow one.

**A name is not always available, and the reasons differ.** Of this
process's 138 handles: 93 could be duplicated (45 refused), and of those
only 34 have a name -- most kernel objects genuinely have none. "Refused",
"has no name" and "we ran out of time" are three different answers and
none of them is an empty string.

## What needs a driver

Process Explorer ships a signed kernel driver and this does not, so:

- **Handles in other users' processes** cannot be duplicated without
  `PROCESS_DUP_HANDLE`, which an unelevated process does not have over
  them. Those rows arrive with a type but no name.
- **`NtQueryObject(ObjectNameInformation)` can block for ever** on a
  synchronous named pipe whose peer never answers, and on ANY file opened
  for synchronous I/O while another thread waits in a synchronous call on
  it (the file-object lock). There is no timeout parameter and no way to
  cancel it. Pipes are skipped up front; for the rest the naming runs on a
  thread that is abandoned when one query stalls, and a fresh thread
  carries on from the next handle -- see `HandleNamer`.

Qt-free, like the rest of the engine.
"""
import ctypes
import logging
import threading
import time as _time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ntdll = ctypes.WinDLL("ntdll")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SystemExtendedHandleInformation = 64
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
ObjectNameInformation = 1
ObjectTypeInformation = 2
DUPLICATE_SAME_ACCESS = 0x00000002
PROCESS_DUP_HANDLE = 0x0040

#: Stop growing the buffer here. 169k handles need ~7 MB; a machine needing
#: more than this has something badly wrong and an unbounded loop would
#: turn that into an out-of-memory rather than a message.
MAX_BUFFER = 128 * 1024 * 1024

#: How long the whole naming pass for one process may take. Generous: the
#: measured cost is 0.4 ms for 93 handles, so reaching this means something
#: is blocked rather than slow.
NAME_DEADLINE_SECONDS = 2.0

#: How long ONE handle's name query may go without returning before it is
#: called blocked. A name takes microseconds; one still pending after this
#: is waiting on a lock that a synchronous I/O in progress holds, and that
#: can last for ever. Generous enough that a busy machine is not mistaken
#: for a stuck one, and a mistake corrects itself (see `_BLOCKING_OBJECTS`).
STALL_SECONDS = 0.2

#: Blocked handles one process may cost before its pass gives up. Each one
#: leaves a thread waiting in the kernel until the I/O ends, so this bounds
#: the leak as well as the time.
MAX_BLOCKED_PER_PASS = 8

#: Kernel objects whose name query blocked, by address, so the next search
#: skips them instead of leaving another thread stuck on the same object.
#: An object that answers late is removed again, and the set is cleared
#: rather than grown past `_BLOCKING_LIMIT` (addresses are reused).
_BLOCKING_OBJECTS: set = set()
_BLOCKING_LIMIT = 4096
SUSPECTED_BLOCK = ("the name query did not return in time -- most likely "
                   "a synchronous I/O in progress holds this object's lock")
NOT_REOPENED = "the process could not be reopened after a blocked handle"
SKIPPED_BLOCKING = ("not queried: a name query on this object blocked "
                    "earlier and is still waiting")

#: Declared, and the reason is the whole first paragraph of this module.
_ntdll.NtQuerySystemInformation.restype = ctypes.c_ulong
_ntdll.NtQuerySystemInformation.argtypes = [
    ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
    ctypes.POINTER(wintypes.ULONG)]
_ntdll.NtQueryObject.restype = ctypes.c_ulong
_ntdll.NtQueryObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG)]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                  wintypes.DWORD]
_kernel32.DuplicateHandle.restype = wintypes.BOOL
_kernel32.DuplicateHandle.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL,
    wintypes.DWORD]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetFileType.restype = wintypes.DWORD
_kernel32.GetFileType.argtypes = [wintypes.HANDLE]

#: `GetFileType` results. A pipe is the handle kind whose name query can
#: block for ever, and this call is the cheap way to spot one -- it is a
#: local lookup and does not touch the other end.
FILE_TYPE_DISK = 0x0001
FILE_TYPE_CHAR = 0x0002
FILE_TYPE_PIPE = 0x0003


class _EXTENDED_HANDLE(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_size_t),
        ("HandleValue", ctypes.c_size_t),
        ("GrantedAccess", wintypes.ULONG),
        ("CreatorBackTraceIndex", wintypes.USHORT),
        ("ObjectTypeIndex", wintypes.USHORT),
        ("HandleAttributes", wintypes.ULONG),
        ("Reserved", wintypes.ULONG),
    ]


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p)]


@dataclass(frozen=True, slots=True)
class HandleEntry:
    """One open handle, as the kernel reports it."""

    pid: int
    value: int
    object_address: int
    granted_access: int
    type_index: int
    attributes: int


@dataclass(frozen=True, slots=True)
class HandleInfo:
    """One handle, with whatever we could learn about it."""

    entry: HandleEntry
    type_name: Optional[str] = None
    name: Optional[str] = None
    #: Why `name` is None. Never collapsed into an empty string: "we were
    #: refused", "this object has no name" and "the query did not return"
    #: are three different facts about a row.
    unavailable: Optional[str] = None

    @property
    def pid(self) -> int:
        return self.entry.pid

    @property
    def value(self) -> int:
        return self.entry.value


def system_handles(pid: Optional[int] = None) -> List[HandleEntry]:
    """Every open handle on the machine, or just one process's.

    One call for the whole machine either way -- there is no per-process
    class -- so filtering here costs nothing over asking.
    """
    size = 0x10000
    while size <= MAX_BUFFER:
        buffer = (ctypes.c_byte * size)()
        needed = wintypes.ULONG(0)
        status = _ntdll.NtQuerySystemInformation(
            SystemExtendedHandleInformation, buffer, size,
            ctypes.byref(needed))
        if status == STATUS_INFO_LENGTH_MISMATCH:
            # Grow past what it asked for: the count moves between calls on
            # a live machine, and matching it exactly loops for ever.
            size = max(size * 2, needed.value + 0x10000)
            continue
        if status != 0:
            logger.warning("The handle table was refused: status %#x", status)
            return []
        return _parse(buffer, pid)
    logger.warning("The handle table did not fit in %d bytes", MAX_BUFFER)
    return []


def _parse(buffer, pid: Optional[int]) -> List[HandleEntry]:
    pointer = ctypes.sizeof(ctypes.c_size_t)
    count = ctypes.cast(buffer,
                        ctypes.POINTER(ctypes.c_size_t)).contents.value
    base = ctypes.addressof(buffer) + 2 * pointer
    stride = ctypes.sizeof(_EXTENDED_HANDLE)
    out = []
    for index in range(count):
        raw = _EXTENDED_HANDLE.from_address(base + index * stride)
        if pid is not None and raw.UniqueProcessId != pid:
            continue
        out.append(HandleEntry(
            pid=raw.UniqueProcessId,
            value=raw.HandleValue,
            object_address=raw.Object or 0,
            granted_access=raw.GrantedAccess,
            type_index=raw.ObjectTypeIndex,
            attributes=raw.HandleAttributes))
    return out


class HandleNamer:
    """Turns handle entries into named rows, under a deadline.

    Names come from duplicating each handle into this process and asking
    `NtQueryObject`. Two costs are bounded here rather than trusted:

    - **The type-name cache.** A machine has a couple of dozen object
      types and a hundred thousand handles, so the index is resolved once
      per type and reused. Measured: 16 distinct types across this
      process's 138 handles.
    - **The naming deadline.** `ObjectNameInformation` has no timeout and
      cannot be cancelled; on a synchronous pipe with no peer it never
      returns. The pass therefore runs on a daemon thread, and if that
      thread does not finish in time the rows named so far are returned
      and the rest say why. Abandoning the thread leaks it -- one per
      genuinely blocked handle -- which is the cost of not shipping a
      driver, and is stated rather than hidden.
    """

    def __init__(self) -> None:
        self._types: Dict[int, Optional[str]] = {}

    def describe(self, entries: List[HandleEntry],
                 deadline: float = NAME_DEADLINE_SECONDS
                 ) -> Tuple[List[HandleInfo], Optional[str]]:
        """`(rows, note)` -- the note explains any gap, or is `None`."""
        if not entries:
            return [], None
        pid = entries[0].pid
        source = _kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
        if not source:
            # Without PROCESS_DUP_HANDLE nothing can be duplicated, so the
            # rows carry their type index and access mask and nothing else.
            return ([HandleInfo(entry=entry,
                                unavailable="this process cannot be opened "
                                            "for handle duplication")
                     for entry in entries],
                    "REFUSED: handle names need PROCESS_DUP_HANDLE on the "
                    "target, which this process does not have. Running "
                    "elevated resolves most of them; the rest need the "
                    "kernel driver Process Explorer ships.")

        # One stuck handle costs ONE handle, not every handle after it.
        #
        # A file opened for synchronous I/O serialises everything on its
        # file-object lock, the name query included, so while another
        # thread waits in a synchronous call on it (ReadDirectoryChangesW on
        # a directory, a read on a device) `NtQueryObject` waits too, for as
        # long as that call does. Pipes are skipped up front, but this case
        # cannot be spotted beforehand. When the pass was one thread under one
        # deadline, the first such handle cost every name after it: a search
        # for a file THIS process had open came back "0 matches" (reproduced
        # with three watched directories, 2026-10-20). So a query that stops
        # progressing for STALL_SECONDS is marked blocked, its thread is
        # abandoned, and a fresh one carries on from the next handle.
        n = len(entries)
        rows: List[Optional[HandleInfo]] = [None] * n
        blocked = 0
        timed_out = False
        start = 0
        end = _time.monotonic() + deadline
        while start < n:
            if source is None:
                source = _kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
                if not source:
                    for index in range(start, n):
                        rows[index] = HandleInfo(
                            entry=entries[index],
                            type_name=self._types.get(entries[index].type_index),
                            unavailable=NOT_REOPENED)
                    break
            done, stalled, timed_out = self._pass(source, entries, start,
                                                  end, pid)
            source = None     # the pass's worker owns it now
            for index, info in done.items():
                rows[index] = info
            if stalled is None or timed_out:
                break
            if stalled not in done:
                entry = entries[stalled]
                rows[stalled] = HandleInfo(
                    entry=entry, type_name=self._types.get(entry.type_index),
                    unavailable=SUSPECTED_BLOCK)
                blocked += 1
                # Time spent waiting out a stall is not charged to the
                # deadline, which bounds slow naming; MAX_BLOCKED_PER_PASS
                # bounds stalls. A handle that answered late gets nothing.
                end += STALL_SECONDS
            start = stalled + 1
            if blocked >= MAX_BLOCKED_PER_PASS:
                timed_out = True
                break
        return self._summarise(rows, entries, blocked, timed_out, deadline)

    def _pass(self, source, entries: List[HandleEntry], first: int,
              end: float, pid: int
              ) -> Tuple[Dict[int, HandleInfo], Optional[int], bool]:
        """Name `entries[first:]` on one worker thread until it finishes,
        stalls or runs out of time: `(named, stalled_index, timed_out)`.

        A stalled index missing from `named` was declared blocked, and its
        object is now in `_BLOCKING_OBJECTS`. The worker owns `source`."""
        n = len(entries)
        out: Dict[int, HandleInfo] = {}
        # (index, since) of the query RUNNING now, or (None, since) between
        # queries. Only a running query can stall: a worker whose thread has
        # not started yet must not get the first object called blocked.
        at = [(None, _time.monotonic())]
        finished = threading.Event()
        # `lock` makes abandoning one step: the caller sets `abandoned`,
        # records the stuck object and copies `out` under it, and the
        # worker stores each result under it. Without it a worker could
        # insert while the caller iterated `out`, or drop a late-answering
        # object from _BLOCKING_OBJECTS before the caller had added it --
        # leaving a healthy object skipped for good.
        lock = threading.Lock()
        abandoned = [False]
        declared = [None]   # the index the caller called blocked

        def work():
            # The worker owns `source` and closes it, including when this
            # thread is abandoned and finishes later.
            #
            # Closing it on the caller's side instead leaked one process
            # handle per timed-out process -- and that leak is
            # self-amplifying, because the handles pile up in the very
            # process doing the searching, which then takes longer to
            # search and times out more often. It showed up as a
            # machine-wide search failing to find a file THIS process had
            # open, but only after a few hundred earlier searches.
            try:
                for index in range(first, n):
                    entry = entries[index]
                    address = entry.object_address
                    with lock:
                        if abandoned[0]:
                            return
                        if address and address in _BLOCKING_OBJECTS:
                            out[index] = HandleInfo(
                                entry=entry,
                                type_name=self._types.get(entry.type_index),
                                unavailable=SKIPPED_BLOCKING)
                            continue
                        at[0] = (index, _time.monotonic())
                    info = self._one(source, entry)
                    with lock:
                        if abandoned[0]:
                            if declared[0] == index:
                                # It answered after all: slow, not stuck,
                                # so the next search must not skip it.
                                _BLOCKING_OBJECTS.discard(address)
                            return
                        out[index] = info
                        at[0] = (None, _time.monotonic())
            except Exception as error:  # noqa: BLE001
                logger.warning("Naming handles for %d failed: %s", pid, error)
            finally:
                finished.set()
                _kernel32.CloseHandle(source)

        threading.Thread(target=work, daemon=True,
                         name=f"handle-names-{pid}").start()
        # Every decision is taken under `lock`, from the state the worker
        # left under it: the index read is the query running at that moment,
        # never one that finished since a look taken outside the lock.
        while True:
            if finished.wait(max(0.0, min(0.02, end - _time.monotonic()))):
                with lock:
                    abandoned[0] = True
                    return dict(out), None, False
            now = _time.monotonic()
            with lock:
                index, since = at[0]
                if now >= end:
                    abandoned[0] = True
                    return dict(out), index, True
                if index is not None and now - since >= STALL_SECONDS:
                    abandoned[0] = True
                    declared[0] = index
                    address = entries[index].object_address
                    if address:
                        if len(_BLOCKING_OBJECTS) >= _BLOCKING_LIMIT:
                            _BLOCKING_OBJECTS.clear()
                        _BLOCKING_OBJECTS.add(address)
                    return dict(out), index, False

    def _summarise(self, rows: List[Optional[HandleInfo]],
                   entries: List[HandleEntry], blocked: int,
                   timed_out: bool, deadline: float
                   ) -> Tuple[List[HandleInfo], Optional[str]]:
        unnamed = 0
        for index in range(len(entries)):
            if rows[index] is None:
                unnamed += 1
                rows[index] = HandleInfo(
                    entry=entries[index],
                    type_name=self._types.get(entries[index].type_index),
                    unavailable="naming stopped: a handle query did not return")
        # Every row this pass never really queried is a gap in the search,
        # and the note counts each kind: findref reports it as "not
        # necessarily everything". Pipes and refused duplications carry
        # their own reason per row, as before.
        skipped = sum(row.unavailable == SKIPPED_BLOCKING for row in rows)
        lost = sum(row.unavailable == NOT_REOPENED for row in rows)
        gaps = []
        if blocked:
            gaps.append(f"{blocked} did not answer within {STALL_SECONDS:.1f}s "
                        f"(suspected block)")
        if skipped:
            gaps.append(f"{skipped} were skipped because they blocked an "
                        f"earlier search")
        if lost:
            gaps.append(f"{lost} were not reached: the process could not be "
                        f"reopened (it may have exited)")
        if timed_out and unnamed:
            return rows, (
                f"BLOCKED: naming stopped after {deadline:.2f}s with "
                f"{unnamed} handles left"
                + (f" ({'; '.join(gaps)})" if gaps else "") +
                ". NtQueryObject cannot be cancelled -- Process Explorer "
                "reads these through its kernel driver instead.")
        if unnamed:
            gaps.append(f"{unnamed} were not reached: naming stopped "
                        f"unexpectedly (see the log)")
        if gaps:
            return rows, (
                f"BLOCKED: {'; '.join(gaps)}. The other handles were queried "
                f"normally; each row says why it has no name.")
        return rows, None

    def _one(self, source, entry: HandleEntry) -> HandleInfo:
        duplicate = wintypes.HANDLE()
        ok = _kernel32.DuplicateHandle(
            source, wintypes.HANDLE(entry.value),
            _kernel32.GetCurrentProcess(), ctypes.byref(duplicate),
            0, False, DUPLICATE_SAME_ACCESS)
        if not ok:
            return HandleInfo(entry=entry,
                              type_name=self._types.get(entry.type_index),
                              unavailable="the handle could not be duplicated")
        try:
            kind = self._type_of(duplicate, entry.type_index)
            if kind == "File" and _kernel32.GetFileType(
                    duplicate) == FILE_TYPE_PIPE:
                # THE hang. `NtQueryObject(ObjectNameInformation)` on a
                # synchronous pipe whose peer never answers blocks for
                # ever and cannot be cancelled -- and because the deadline
                # abandons the whole per-process pass, one such handle
                # used to cost that process every name after it. That is
                # how this process's own open file went unfound while 37
                # processes reported "refused handle inspection".
                #
                # `GetFileType` is a local lookup that does not touch the
                # other end, so it identifies the dangerous ones for
                # nothing.
                return HandleInfo(entry=entry, type_name=kind,
                                  unavailable="named pipes are not queried: "
                                              "the name query cannot be "
                                              "cancelled and can block for "
                                              "ever")
            name = _query_string(duplicate, ObjectNameInformation)
            if name is None:
                return HandleInfo(entry=entry, type_name=kind,
                                  unavailable="the object could not be named")
            if name == "":
                return HandleInfo(entry=entry, type_name=kind,
                                  unavailable="this object has no name")
            return HandleInfo(entry=entry, type_name=kind, name=name)
        finally:
            _kernel32.CloseHandle(duplicate)

    def _type_of(self, handle, index: int) -> Optional[str]:
        if index in self._types:
            return self._types[index]
        found = _query_string(handle, ObjectTypeInformation) or None
        self._types[index] = found
        return found


def _query_string(handle, klass: int, size: int = 4096) -> Optional[str]:
    """One `NtQueryObject` string, `""` for a present-but-empty name."""
    buffer = (ctypes.c_byte * size)()
    needed = wintypes.ULONG(0)
    status = _ntdll.NtQueryObject(handle, klass, buffer, size,
                                  ctypes.byref(needed))
    if status != 0:
        return None
    text = _UNICODE_STRING.from_address(ctypes.addressof(buffer))
    if not text.Buffer or not text.Length:
        return ""
    return ctypes.wstring_at(text.Buffer, text.Length // 2)


def access_flags(granted: int, type_name: Optional[str]) -> str:
    """The access mask as the rights it grants, where they are generic.

    Only the type-independent bits are decoded. A `File`'s 0x0001 is
    FILE_READ_DATA and a `Key`'s is KEY_QUERY_VALUE, and guessing wrong
    would put a confident wrong word next to a number that was right.
    """
    generic = [
        (0x00010000, "DELETE"),
        (0x00020000, "READ_CONTROL"),
        (0x00040000, "WRITE_DAC"),
        (0x00080000, "WRITE_OWNER"),
        (0x00100000, "SYNCHRONIZE"),
    ]
    names = [label for bit, label in generic if granted & bit]
    specific = granted & 0xFFFF
    if specific:
        names.append(f"specific {specific:#06x}")
    return " | ".join(names) if names else "none"
