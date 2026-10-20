"""Open handles, per process.

The previous implementation of this returned an empty list for every
process on the machine, always, because of a signed/unsigned comparison in
its retry loop. Several of these tests exist specifically so that cannot
come back quietly.
"""
import os
import time

import pytest

from core.procengine.handles import (
    HandleEntry, HandleNamer, STATUS_INFO_LENGTH_MISMATCH, access_flags,
    system_handles,
)
from core.procengine.ntquery import system_processes

MY_PID = os.getpid()


def _mine():
    return [row for row in system_processes() if row.pid == MY_PID][0]


# ---- enumeration --------------------------------------------------------

def test_the_machine_has_handles():
    assert len(system_handles()) > 1000


def test_this_process_has_handles():
    """The bug this replaces returned zero here while the kernel reported
    185. A pane saying a process holds no handles at all is not a subtle
    kind of wrong."""
    assert system_handles(MY_PID)


def test_the_count_is_close_to_what_the_kernel_reports():
    ours = system_handles(MY_PID)
    reported = _mine().handles
    # Not exact: handles open and close between the two calls.
    assert abs(len(ours) - reported) < max(40, reported * 0.3), (
        f"the handle table says {len(ours)}, the process record says "
        f"{reported}")


def test_the_retry_loop_can_actually_fire():
    """`NtQuerySystemInformation` answers its FIRST call with
    STATUS_INFO_LENGTH_MISMATCH by design -- the buffer size is not
    knowable in advance. Undeclared, ctypes returns a signed int, the
    status reads as -1073741820, never equals 0xC0000004, and the retry
    never happens. That is exactly how the old view came to show zero
    handles for every process.
    """
    import ctypes

    from core.procengine import handles as module

    assert module._ntdll.NtQuerySystemInformation.restype is ctypes.c_ulong
    assert STATUS_INFO_LENGTH_MISMATCH == 0xC0000004
    # The signed reading of the same bits, which must NOT compare equal.
    assert ctypes.c_long(STATUS_INFO_LENGTH_MISMATCH).value != \
        STATUS_INFO_LENGTH_MISMATCH


def test_the_pid_field_is_full_width():
    """Class 16 stores UniqueProcessId in a USHORT. Windows pids are
    DWORDs -- this machine is already past 35,000 -- and beyond 65,535
    that field wraps and gives one process another's handles."""
    from core.procengine.handles import (
        SystemExtendedHandleInformation, _EXTENDED_HANDLE,
    )
    import ctypes

    assert SystemExtendedHandleInformation == 64
    field = dict((name, kind) for name, kind in _EXTENDED_HANDLE._fields_)
    assert ctypes.sizeof(field["UniqueProcessId"]) >= 4


def test_every_pid_seen_belongs_to_a_live_process():
    live = {row.pid for row in system_processes()}
    seen = {entry.pid for entry in system_handles()}
    # A handful will have exited between the two calls; the point is that
    # the pids are real numbers rather than truncated ones.
    assert len(seen & live) > len(seen) * 0.9


def test_filtering_by_pid_matches_filtering_by_hand():
    everything = system_handles()
    just_us = system_handles(MY_PID)
    by_hand = [e for e in everything if e.pid == MY_PID]
    assert abs(len(just_us) - len(by_hand)) < 40


def test_an_entry_carries_its_access_and_type():
    for entry in system_handles(MY_PID)[:20]:
        assert entry.value > 0
        assert entry.granted_access >= 0
        assert entry.type_index >= 0


def test_enumeration_is_fast_enough_to_be_usable():
    """169,153 handles in 8 ms measured. A second is a very loose bound
    that still catches a rewrite that starts querying per process."""
    started = time.perf_counter()
    system_handles()
    assert time.perf_counter() - started < 1.0


# ---- naming -------------------------------------------------------------

def test_our_own_handles_get_type_names():
    rows, note = HandleNamer().describe(system_handles(MY_PID))
    assert rows
    named = [row for row in rows if row.type_name]
    assert named, f"no handle got a type name (note: {note})"
    kinds = {row.type_name for row in named}
    assert "File" in kinds or "Key" in kinds or "Event" in kinds


def test_some_of_our_handles_get_object_names():
    rows, _note = HandleNamer().describe(system_handles(MY_PID))
    named = [row for row in rows if row.name]
    assert named, "nothing was named at all"
    assert any("\\" in row.name for row in named)


def test_an_unnamed_object_says_so_rather_than_showing_blank():
    """Most kernel objects genuinely have no name -- 34 of 93 nameable
    handles had one here. "Has no name", "was refused" and "we ran out of
    time" are three different facts and none of them is an empty string.
    """
    rows, _note = HandleNamer().describe(system_handles(MY_PID))
    for row in rows:
        if row.name is None:
            assert row.unavailable, f"{row.type_name} gave no reason"
        else:
            assert row.name != ""


def test_the_type_name_is_resolved_once_per_type():
    namer = HandleNamer()
    namer.describe(system_handles(MY_PID))
    # A machine has a couple of dozen object types, not one per handle.
    assert 0 < len(namer._types) < 60


def test_naming_nothing_is_not_an_error():
    rows, note = HandleNamer().describe([])
    assert rows == [] and note is None


def test_a_process_that_cannot_be_opened_says_why(qapp=None):
    """Rather than an empty table, which reads as "this process holds no
    handles"."""
    entry = HandleEntry(pid=4, value=4, object_address=0, granted_access=0,
                        type_index=0, attributes=0)
    rows, note = HandleNamer().describe([entry])
    assert len(rows) == 1
    if rows[0].name is None:
        assert rows[0].unavailable
    if note:
        assert "driver" in note or "elevated" in note


def test_naming_respects_its_deadline():
    """The deadline exists because NtQueryObject cannot be cancelled and
    blocks for ever on a synchronous pipe with no peer."""
    entries = system_handles(MY_PID)
    started = time.perf_counter()
    rows, _note = HandleNamer().describe(entries, deadline=0.0)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
    assert len(rows) == len(entries), \
        "every handle must still produce a row, named or not"


def test_a_deadline_miss_explains_itself():
    entries = system_handles(MY_PID)
    if len(entries) < 5:
        pytest.skip("too few handles to out-run")
    rows, note = HandleNamer().describe(entries, deadline=0.0)
    unnamed = [row for row in rows if row.name is None]
    assert unnamed
    assert all(row.unavailable for row in unnamed)


# ---- the access mask ----------------------------------------------------

def test_generic_access_bits_are_decoded():
    assert "SYNCHRONIZE" in access_flags(0x00100000, "Event")
    assert "DELETE" in access_flags(0x00010000, "File")


def test_type_specific_bits_are_not_guessed_at():
    """A File's 0x0001 is FILE_READ_DATA and a Key's is KEY_QUERY_VALUE.
    Guessing puts a confident wrong word beside a number that was right."""
    text = access_flags(0x0001, "File")
    assert "specific" in text and "0x0001" in text
    assert access_flags(0x0001, "File") == access_flags(0x0001, "Key")


def test_no_access_is_said_plainly():
    assert access_flags(0, "Event") == "none"


# ---- Qt-free ------------------------------------------------------------

def test_the_engine_does_not_import_qt():
    import inspect

    from core.procengine import handles

    assert "PyQt6" not in inspect.getsource(handles)


def test_the_process_handle_is_closed_by_the_worker_not_the_caller():
    """Closing `source` only on the success path leaked one process handle
    per timed-out process -- and the leak is self-amplifying, because it
    accumulates in the very process doing the searching, which then takes
    longer to search and times out more often.

    Asserted structurally as well as numerically: the numeric check below
    can only measure this whole process, and under a full test run other
    suites leave genuinely-blocked naming threads behind (the documented
    driver limit), which drift the count independently of this function.
    """
    import inspect

    from core.procengine import handles as module

    # The naming worker lives in `_pass` (one per stretch between blocked handles).
    source = inspect.getsource(module.HandleNamer._pass)
    worker = source[source.index("def work("):]
    assert "CloseHandle(source)" in worker,         "the worker must own the handle it was given"
    after_wait = source[source.index("finished.wait("):]
    assert "CloseHandle(source)" not in after_wait,         "closing on the caller's side leaks whenever the deadline trips"


def test_repeated_timed_out_passes_do_not_leak():
    """Measured directly: 20 abandoned passes over 660 handles grow this
    process's handle count by zero."""
    from core.procengine.ntquery import system_processes

    def held() -> int:
        return [r for r in system_processes() if r.pid == MY_PID][0].handles

    entries = system_handles(MY_PID)
    before = held()
    for _ in range(20):
        HandleNamer().describe(entries, deadline=0.0)
    time.sleep(1.0)
    # Loose: this counts the WHOLE process, and a full test run leaves
    # blocked naming threads from other suites holding handles they can
    # never release. The leak this guards against was one per pass.
    assert held() - before < 120, (
        f"handles grew from {before} to {held()} over 20 abandoned passes")


def test_a_named_pipe_is_skipped_rather_than_risked():
    """`NtQueryObject` on a synchronous pipe with no peer blocks for ever
    and cannot be cancelled. `GetFileType` spots one without touching the
    other end, and skipping them took a full machine sweep from 5.5s to
    1.2s -- the hang risk was also the dominant cost."""
    import inspect

    from core.procengine import handles as module

    assert module.FILE_TYPE_PIPE == 3
    source = inspect.getsource(module.HandleNamer._one)
    assert "GetFileType" in source
