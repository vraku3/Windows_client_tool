"""Immersive / .NET / packed -- the facts Process Explorer colours rows by.

The theme of these tests is that the three are not equally trustworthy.
Immersive is a fact Windows itself keeps. .NET is a fact, but only from the
right source. Packed is a guess, and the tests say so.
"""
import os
import sys

import pytest

from core.procengine.classify import (
    PACKED_ENTROPY, ClassifyCache, classify, is_dotnet,
    loaded_modules, package_family_name, packed_guess, shannon_entropy,
)
from core.procengine.ntquery import system_processes

MY_PID = os.getpid()


# ---- immersive ----------------------------------------------------------

def test_this_process_is_not_packaged():
    """A desktop Python is not an AppX app, and the answer is a definite
    empty string rather than a refusal."""
    family, error = package_family_name(MY_PID)
    assert error is None
    assert family == ""


def test_the_machine_has_some_packaged_processes():
    """Windows 11 always runs several -- the shell experience host, the
    start menu, the input host."""
    found = [pid for pid in (row.pid for row in system_processes())
             if (package_family_name(pid)[0] or "")]
    assert found, "no packaged process was found"


def test_a_dead_pid_is_refused_rather_than_answered():
    family, error = package_family_name(999_999)
    assert family is None and error


# ---- .NET ---------------------------------------------------------------

def test_our_own_modules_can_be_listed():
    modules = loaded_modules(MY_PID)
    assert modules
    assert any(name.lower() == "kernel32.dll" for name in modules)


def test_python_is_not_a_dotnet_process():
    dotnet, error = is_dotnet(MY_PID)
    assert error is None
    assert dotnet is False


def test_a_refused_process_is_none_not_false():
    """"Not .NET" and "we were not allowed to look" are different answers,
    and a row coloured plain-native because we were refused is a lie told
    in colour."""
    dotnet, error = is_dotnet(999_999)
    assert dotnet is None and error


def test_the_module_scan_finds_more_than_the_clr_counters():
    """The `.NET CLR Memory` counter set enumerates the whole machine in
    140 ms rather than 1.69 ms per process, which is tempting -- but it
    only lists processes publishing the legacy counters, which .NET Core
    and .NET 5+ do not. Measured here: 4 instances against 15 real ones.
    A faster way to get a different, wrong answer.
    """
    win32pdh = pytest.importorskip("win32pdh")

    by_scan = 0
    refused = 0
    for row in system_processes():
        found, error = is_dotnet(row.pid)
        if found:
            by_scan += 1
        elif error:
            refused += 1

    try:
        _counters, instances = win32pdh.EnumObjectItems(
            None, None, ".NET CLR Memory", win32pdh.PERF_DETAIL_WIZARD)
    except Exception:
        pytest.skip("the .NET CLR counter set is not installed")
    by_counters = len([name for name in instances if name != "_Global_"])

    # Unelevated the scan is refused for every elevated process (152 of
    # 331 here, 2026-10-06) while the counters still list them, so it can
    # come up short by refusals -- never by a process it could read.
    assert by_scan + refused >= by_counters, (
        f"the module scan found {by_scan} (refused {refused}) and the counters "
        f"{by_counters}; if the counters ever win, revisit which source this uses")


# ---- packed, which is a guess -------------------------------------------

def test_entropy_of_nothing_is_zero():
    assert shannon_entropy(b"") == 0.0


def test_entropy_of_one_repeated_byte_is_zero():
    assert shannon_entropy(b"\x00" * 4096) == 0.0


def test_entropy_of_every_byte_equally_is_eight():
    assert shannon_entropy(bytes(range(256)) * 16) == pytest.approx(8.0)


def test_entropy_of_english_text_is_middling():
    text = (b"the quick brown fox jumps over the lazy dog " * 200)
    assert 3.0 < shannon_entropy(text) < 5.0


def test_a_real_image_is_measured():
    guess = packed_guess(sys.executable)
    assert guess.entropy is not None, guess.reason
    assert 0.0 <= guess.entropy <= 8.0


def test_the_guess_carries_its_number_not_just_a_verdict():
    """It is entropy, not evidence. A caller has to be able to show the
    figure and hedge the word -- on this machine the same threshold flags
    OneNote and the Command Palette, which are not packed."""
    guess = packed_guess(sys.executable)
    assert guess.looks_packed == (guess.entropy >= PACKED_ENTROPY)


def test_a_missing_path_is_a_reason_not_a_verdict():
    guess = packed_guess(None)
    assert guess.looks_packed is False and guess.reason


def test_a_file_that_is_not_a_pe_says_so(tmp_path):
    plain = tmp_path / "notape.txt"
    plain.write_bytes(b"hello" * 1000)
    guess = packed_guess(str(plain))
    assert guess.looks_packed is False
    assert guess.reason and "PE" in guess.reason


def test_a_file_that_does_not_exist_says_so(tmp_path):
    guess = packed_guess(str(tmp_path / "nope.exe"))
    assert guess.looks_packed is False and guess.reason


# ---- the whole classification -------------------------------------------

def test_classifying_this_process():
    found = classify(MY_PID, sys.executable)
    assert found.pid == MY_PID
    assert found.immersive is False
    assert found.dotnet is False
    assert found.packed is None, "packed must be off unless asked for"


def test_packed_is_only_computed_when_asked():
    """At 4.11 ms it costs more than the other two together, for the least
    trustworthy answer."""
    assert classify(MY_PID, sys.executable).packed is None
    asked = classify(MY_PID, sys.executable, want_packed=True)
    assert asked.packed is not None


def test_a_refusal_is_recorded_with_its_reason():
    found = classify(999_999)
    assert found.immersive is None and found.dotnet is None
    assert "immersive" in found.unavailable
    assert "dotnet" in found.unavailable


# ---- the cache ----------------------------------------------------------

def test_the_cache_resolves_once():
    cache = ClassifyCache()
    first = cache.get(MY_PID, 12345)
    assert cache.tracked() == 1
    assert cache.get(MY_PID, 12345) is first


def test_a_reused_pid_does_not_serve_the_dead_processs_answer():
    cache = ClassifyCache()
    cache.get(MY_PID, 111)
    cache.get(MY_PID, 222)
    assert cache.tracked() == 2


def test_the_cache_honours_a_budget():
    rows = system_processes()
    cache = ClassifyCache()
    budget = [3]
    for row in rows:
        cache.get(row.pid, row.create_time, None, budget)
    assert cache.tracked() == 3


def test_a_budgeted_out_process_is_unresolved_not_wrong():
    cache = ClassifyCache()
    skipped = cache.get(MY_PID, 999, None, [0])
    assert skipped.immersive is None and skipped.dotnet is None
    assert cache.tracked() == 0, "it must be retried, not cached as unknown"


def test_dead_processes_are_dropped():
    cache = ClassifyCache()
    cache.get(MY_PID, 1)
    cache.retain(set())
    assert cache.tracked() == 0


# ---- Qt-free ------------------------------------------------------------

def test_the_engine_does_not_import_qt():
    import inspect

    from core.procengine import classify as module

    assert "PyQt6" not in inspect.getsource(module)


def test_the_dotnet_shim_is_not_the_dotnet_runtime():
    """`mscoree.dll` is the shim. Anything that touches a .NET API loads
    it without ever running managed code -- asking PDH for the
    `.NET CLR Memory` counters loads it into the ASKING process, after
    which a shim-based detector calls itself .NET forever. Found exactly
    that way: two tests in one run disagreed about this very process.
    """
    import win32pdh

    from core.procengine.classify import _CLR_MODULES

    assert "mscoree.dll" not in _CLR_MODULES
    assert "mscoreei.dll" not in _CLR_MODULES

    before, _ = is_dotnet(MY_PID)
    try:
        win32pdh.EnumObjectItems(None, None, ".NET CLR Memory",
                                 win32pdh.PERF_DETAIL_WIZARD)
    except Exception:
        pytest.skip("the .NET CLR counter set is not installed")
    after, _ = is_dotnet(MY_PID)
    assert before is False and after is False, (
        "querying the CLR counters made this process look like a .NET one")
    assert "mscoree.dll" in [name.lower() for name in loaded_modules(MY_PID)], \
        "the premise of this test no longer holds -- the shim did not load"
