"""Finding which process holds a handle or has a DLL loaded.

The question is "what is holding this file open", and the answer that
matters most is the one about what could NOT be searched: "nothing has it
open" and "nothing I was allowed to look at has it open" are different
answers, and only one of them is safe to act on.
"""
import os
import time

import pytest

from core.procengine.findref import FindReport, Match, find

MY_PID = os.getpid()


# ---- finding things -----------------------------------------------------

def test_a_loaded_dll_is_found():
    report = find("ntdll.dll", handles=False, modules=True)
    assert report.matches
    assert any(match.pid == MY_PID for match in report.matches)
    assert all(match.kind == "DLL" for match in report.matches)


def test_the_search_is_case_insensitive():
    assert find("NTDLL.DLL", handles=False).matches
    assert find("ntdll.dll", handles=False).matches


def test_a_substring_of_a_path_is_enough():
    """Someone searching for a file should not have to know it is held as
    \\Device\\HarddiskVolume3\\..."""
    report = find("system32", handles=False, modules=True)
    assert report.matches


def test_an_open_file_handle_is_found(tmp_path):
    """The question this whole feature exists for: what has my file open?"""
    marker = tmp_path / "findref-probe-file.txt"
    marker.write_text("hello")
    handle = open(marker, "r")
    try:
        report = find("findref-probe-file", handles=True, modules=False)
        assert report.matches, report.summary()
        assert any(match.pid == MY_PID for match in report.matches)
        assert any(match.kind == "Handle" for match in report.matches)
    finally:
        handle.close()


@pytest.fixture
def stuck_directory_handles(tmp_path):
    """Three synchronous directory handles, each with a ReadDirectoryChangesW
    waiting on it -- which holds the file-object lock, so a name query on
    them waits too. A full test run leaves handles like these behind, and
    the search for this process's own open file came back "0 matches"."""
    import ctypes
    import threading
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    watched = tmp_path / "watched"
    watched.mkdir()
    handles = []
    for _ in range(3):
        h = k32.CreateFileW(str(watched), 0x1, 0x7, None, 3, 0x02000000, None)
        assert h and h != wintypes.HANDLE(-1).value, ctypes.get_last_error()
        handles.append(h)

    def wait(h):
        buf = ctypes.create_string_buffer(4096)
        got = wintypes.DWORD()
        k32.ReadDirectoryChangesW(wintypes.HANDLE(h), buf, 4096, False, 0x1,
                                  ctypes.byref(got), None, None)

    threads = [threading.Thread(target=wait, args=(h,), daemon=True) for h in handles]
    for thread in threads:
        thread.start()
    time.sleep(0.3)
    yield
    (watched / "release.txt").write_text("")      # ends the waiting reads
    for thread in threads:
        thread.join(2)
    for h in handles:
        k32.CloseHandle(wintypes.HANDLE(h))


def test_a_stuck_handle_does_not_hide_the_rest_of_the_process(tmp_path,
                                                             stuck_directory_handles):
    marker = tmp_path / "findref-stuck-probe.txt"
    marker.write_text("hello")
    with open(marker):
        report = find("findref-stuck-probe", handles=True, modules=False)
    assert any(match.pid == MY_PID for match in report.matches), report.summary()


def test_a_stuck_handle_costs_one_row_and_says_so(tmp_path, stuck_directory_handles):
    from core.procengine.handles import HandleNamer, system_handles

    marker = tmp_path / "namer-stuck-probe.txt"
    marker.write_text("hello")
    with open(marker):
        rows, note = HandleNamer().describe(system_handles(MY_PID))
        # And again: the objects that blocked are skipped, not waited on twice.
        started = time.perf_counter()
        again, _note = HandleNamer().describe(system_handles(MY_PID))
        second = time.perf_counter() - started
    assert any(r.name and "namer-stuck-probe" in r.name for r in rows), note
    assert note and note.startswith("BLOCKED") and "queried normally" in note
    assert sum("did not return" in (r.unavailable or "") for r in rows) <= 3
    assert any(r.name and "namer-stuck-probe" in r.name for r in again)
    # Skipping is still a gap in the search, and must be reported as one.
    assert _note and _note.startswith("BLOCKED") and "skipped" in _note, _note
    assert second < 0.5, f"the known-blocking objects were waited on again ({second:.2f}s)"


def test_a_closed_file_is_not_found(tmp_path):
    marker = tmp_path / "findref-probe-closed.txt"
    marker.write_text("hello")
    with open(marker):
        pass
    report = find("findref-probe-closed", handles=True, modules=False,
                  budget_seconds=3.0)
    assert not any(match.pid == MY_PID for match in report.matches)


def test_something_nothing_holds_is_not_found():
    report = find("zzz-nothing-holds-this-zzz", budget_seconds=3.0)
    assert report.matches == []


def test_a_match_names_the_process_not_just_the_pid():
    report = find("ntdll.dll", handles=False)
    assert report.matches
    for match in report.matches:
        assert match.process and match.process != ""


# ---- what could not be searched -----------------------------------------

def test_the_report_counts_what_refused():
    """Twenty-one processes refuse their module list on this machine even
    elevated. A search that skipped them silently would answer "nothing
    has that file open" when it means "nothing I could look at"."""
    report = find("ntdll.dll", handles=False, modules=True)
    assert report.searched_processes > 100
    assert report.refused_modules >= 0
    if report.refused_any:
        assert "not necessarily everything" in report.summary()


def test_a_clean_search_does_not_hedge():
    report = FindReport(matches=[], searched_processes=10)
    assert "not necessarily" not in report.summary()


def test_the_summary_says_how_many_and_where():
    report = FindReport(matches=[Match(1, "a.exe", "DLL", "Module", "x")],
                        searched_processes=42)
    assert "1 matches" in report.summary()
    assert "42" in report.summary()


# ---- control ------------------------------------------------------------

def test_an_empty_query_is_refused_rather_than_matching_everything():
    for text in ("", "   ", None):
        report = find(text)
        assert report.matches == []
        assert report.note


def test_the_search_can_be_stopped():
    report = find("dll", should_stop=lambda: True)
    assert report.stopped_early
    assert report.note and "stopped" in report.note.lower()


def test_progress_is_reported():
    seen = []
    find("ntdll.dll", handles=False, modules=True,
         progress=lambda done, total: seen.append((done, total)))
    assert seen
    assert seen[-1][1] > 0


def test_searching_only_modules_skips_handles():
    report = find("ntdll.dll", handles=False, modules=True)
    assert all(match.kind == "DLL" for match in report.matches)


def test_searching_only_handles_skips_modules():
    report = find("REGISTRY", handles=True, modules=False,
                  budget_seconds=3.0)
    assert all(match.kind == "Handle" for match in report.matches)


def test_a_whole_machine_search_finishes_in_reasonable_time():
    """Measured: 143 ms to enumerate 164k handles, ~0.5 s to name them,
    1.3 s for every process's modules. Slow enough that it must be a
    deliberate action on a worker, fast enough to be worth doing."""
    started = time.perf_counter()
    find("zzz-nothing-holds-this-zzz", budget_seconds=3.0)
    assert time.perf_counter() - started < 30.0


# ---- Qt-free ------------------------------------------------------------

def test_the_engine_does_not_import_qt():
    import inspect

    from core.procengine import findref

    assert "PyQt6" not in inspect.getsource(findref)


def test_the_whole_sweep_is_bounded_not_just_each_process():
    """Extrapolating from a sample of readable processes said the sweep
    would take 0.5 s. It took 20 s, because the cost is dominated by the
    processes that BLOCK and a sample of readable ones contains none of
    them. A per-item deadline is not a bound on a loop over items."""
    started = time.perf_counter()
    report = find("zzz-nothing-holds-this-zzz", budget_seconds=2.0)
    elapsed = time.perf_counter() - started
    assert elapsed < 8.0, f"the budget did not bound the sweep ({elapsed:.1f}s)"
    if report.stopped_early:
        assert "partial" in report.note


def test_running_out_of_budget_says_so_rather_than_reporting_nothing_found():
    """The dangerous failure for this feature: a truncated search that
    reads as "nothing has your file open"."""
    report = find("zzz-nothing-holds-this-zzz", budget_seconds=0.0)
    assert report.stopped_early
    assert report.note and "partial" in report.note


def test_truncation_does_not_always_sacrifice_the_newest_processes():
    """Iterating in pid order means a truncated sweep always drops the
    HIGHEST pids -- the most recently started processes, which is exactly
    what someone searching for their own just-locked file is looking for.
    Handle-count order pays the cost with the processes that were going to
    be slowest anyway.
    """
    import inspect

    from core.procengine import findref

    source = inspect.getsource(findref._find_handles)
    assert "len(item[1])" in source, \
        "the handle sweep must not iterate in pid order"


def test_a_truncated_search_says_how_many_it_never_reached():
    """The number that tells someone to re-run with a longer budget."""
    report = find("zzz-nothing-holds-this-zzz", budget_seconds=0.0)
    assert report.stopped_early
    assert report.unsearched > 0
    assert "never reached" in report.summary()
