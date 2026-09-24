"""Threads tab CPU% (was a permanent "—" behind a TODO).

psutil already reports cumulative per-thread CPU seconds, so the percentage
is the delta between two samples over the wall time between them.
"""
import os
import time
from collections import namedtuple

import psutil

from modules.process_explorer.lower_pane import thread_view as tv

T = namedtuple("T", "id user_time system_time")


def test_percent_is_cpu_seconds_over_wall_seconds():
    before = [T(1, 1.0, 0.5), T(2, 0.0, 0.0)]
    after = [T(1, 1.3, 0.7), T(2, 0.0, 0.0)]
    got = tv.thread_cpu_percent(before, after, 1.0)
    assert round(got[1], 1) == 50.0
    assert got[2] == 0.0


def test_a_thread_with_no_baseline_is_left_out_not_reported_idle():
    got = tv.thread_cpu_percent([T(1, 0, 0)], [T(1, 0, 0), T(9, 5, 5)], 1.0)
    assert 9 not in got and 1 in got


def test_one_thread_never_reads_above_100():
    got = tv.thread_cpu_percent([T(1, 0, 0)], [T(1, 2.0, 0)], 1.0)
    assert got[1] == 100.0


def test_a_counter_going_backwards_reads_zero_not_negative():
    got = tv.thread_cpu_percent([T(1, 5, 0)], [T(1, 4, 0)], 1.0)
    assert got[1] == 0.0


def test_no_elapsed_time_gives_no_answer():
    assert tv.thread_cpu_percent([T(1, 0, 0)], [T(1, 1, 0)], 0.0) == {}


def test_real_process_busy_thread_shows_cpu(qapp):
    """Against a real thread: this process, spinning while sampled."""
    import threading
    stop = threading.Event()

    def spin():
        while not stop.is_set():
            pass
    t = threading.Thread(target=spin, daemon=True)
    t.start()
    try:
        proc = psutil.Process(os.getpid())
        first = proc.threads()
        start = time.monotonic()
        time.sleep(0.5)
        got = tv.thread_cpu_percent(first, proc.threads(),
                                    time.monotonic() - start)
    finally:
        stop.set()
    assert max(got.values()) > 20.0, got


def test_populate_shows_percent_and_dash(qapp):
    view = tv.ThreadView()
    view._pid = 7
    view._populate(7, ([T(1, 1.0, 0.0), T(2, 0.0, 0.0)], {1: 42.0}))
    assert view._table.item(0, 1).text() == "42.0"
    assert view._table.item(1, 1).text() == "—"
