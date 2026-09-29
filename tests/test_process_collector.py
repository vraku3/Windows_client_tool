"""The Process Explorer tab's collector, now on the Dashboard's engine.

It used to call `psutil.process_iter`, which wave 1 measured at 667.9 ms per
refresh against 2.6 ms for the syscall the engine uses. These tests read the
real machine the way the rest of the engine's tests do, and pin the
conversion rules with synthetic rows.
"""
import time


from core.procengine.details import ProcessDetails
from core.procengine.ntquery import ProcessRaw
from core.procengine.rates import Rates
from core.procengine.snapshot import ProcessInfo, SnapshotSource
from modules.process_explorer.process_collector import (
    ProcessCollector, build_snapshot, diff_snapshots, node_from_info,
)


def _raw(pid=100, name="chrome.exe", ppid=4, session=1, threads=5,
         cycles=1234, working_set=2048, virtual_size=8192):
    return ProcessRaw(
        pid=pid, ppid=ppid, name=name, threads=threads, handles=10,
        session=session, base_priority=8, working_set=working_set,
        working_set_private=working_set, private_bytes=working_set,
        paged_pool=0, nonpaged_pool=0, pagefile=0, peak_pagefile=0,
        virtual_size=virtual_size, peak_virtual_size=virtual_size,
        peak_working_set=working_set, page_faults=0, hard_faults=0,
        kernel_time=0, user_time=0, cycles=cycles, read_bytes=0,
        write_bytes=0, other_bytes=0, read_ops=0, write_ops=0, other_ops=0,
        create_time=0)


def _info(raw=None, rates=None, details=None, **kw):
    raw = raw if raw is not None else _raw()
    return ProcessInfo(
        raw=raw,
        rates=rates if rates is not None else Rates(),
        details=details if details is not None else ProcessDetails(pid=raw.pid),
        **kw)


# ---- the real machine ---------------------------------------------------

def test_a_snapshot_is_keyed_by_pid():
    snapshot = build_snapshot(set())
    assert snapshot
    for pid, node in snapshot.items():
        assert node.pid == pid


def test_the_snapshot_covers_this_process():
    import os

    assert os.getpid() in build_snapshot(set())


def test_children_are_linked_to_their_parent():
    snapshot = build_snapshot(set())
    linked = [node for node in snapshot.values() if node.children]
    assert linked, "no process had a child"
    for node in linked:
        for child in node.children:
            assert child.parent_pid == node.pid


def test_no_process_is_its_own_child():
    """pid reuse makes ppid cycles reachable on a real machine, and a cycle
    wearing the shape of a tree is walked forever."""
    for node in build_snapshot(set()).values():
        assert all(child.pid != node.pid for child in node.children)


def test_system_processes_are_recognised():
    """By SESSION, not by user name: unelevated the token classifies zero
    system processes, which is how the Windows group once came out empty."""
    snapshot = build_snapshot(set())
    assert any(node.is_system for node in snapshot.values())


def test_a_snapshot_is_fast_enough_for_the_one_second_tick():
    """The whole point of the change. The old psutil path took 667.9 ms
    here, which is why the tab sat empty for five seconds after a click.
    A 250 ms budget is loose enough for a busy machine and tight enough to
    catch a fall back onto process_iter."""
    source = SnapshotSource()
    build_snapshot(set(), source=source)      # pay the cold cache once
    best = min(_timed(source) for _ in range(3))
    assert best < 0.25, f"a snapshot took {best * 1000:.0f} ms"


def _timed(source):
    started = time.perf_counter()
    build_snapshot(set(), source=source)
    return time.perf_counter() - started


def test_rates_appear_once_there_are_two_readings():
    """A held SnapshotSource is what turns two samples into a rate; a fresh
    one per tick reports 0.0 for everything, forever."""
    source = SnapshotSource()
    build_snapshot(set(), source=source)
    time.sleep(0.5)
    again = build_snapshot(set(), source=source)
    assert any(node.cpu_percent > 0 for node in again.values())


# ---- the conversion -----------------------------------------------------

def test_a_refusal_never_arrives_as_a_number():
    """The engine says `None` for what it could not read. ProcessNode has
    no None, so text becomes "" -- an unfilled cell, which reads as "we
    were not told". What must never happen is a refused value arriving as
    a plausible figure."""
    node = node_from_info(_info(details=ProcessDetails(
        pid=100, path=None, path_error="Access is denied.",
        cmdline=None, user=None)), set())
    assert node.exe == ""
    assert node.cmdline == ""
    assert node.user == ""


def test_readable_details_are_carried_through():
    node = node_from_info(_info(details=ProcessDetails(
        pid=100, path=r"C:\chrome.exe", cmdline="chrome --x",
        user="VRK\\iorda", integrity="High")), set())
    assert node.exe == r"C:\chrome.exe"
    assert node.cmdline == "chrome --x"
    assert node.user == "VRK\\iorda"
    assert node.integrity_level == "High"


def test_appcontainer_is_carried_through_unmodified():
    """Unlike is_immersive/is_dotnet, a refusal (None) must NOT collapse to
    False here -- this is not a row-colour input, so there is nothing to
    protect by lying it into a value."""
    sandboxed = node_from_info(_info(details=ProcessDetails(
        pid=100, appcontainer=True)), set())
    assert sandboxed.appcontainer is True

    ordinary = node_from_info(_info(details=ProcessDetails(
        pid=100, appcontainer=False)), set())
    assert ordinary.appcontainer is False

    refused = node_from_info(_info(details=ProcessDetails(
        pid=100, appcontainer=None)), set())
    assert refused.appcontainer is None


def test_rates_are_carried_through():
    node = node_from_info(
        _info(rates=Rates(cpu_percent=12.5, read_bps=1024.0,
                          write_bps=512.0)), set())
    assert node.cpu_percent == 12.5
    assert node.disk_read_bps == 1024.0
    assert node.disk_write_bps == 512.0


def test_an_unmeasured_rate_is_not_invented():
    node = node_from_info(_info(rates=Rates()), set())
    assert node.cpu_percent == 0.0


def test_session_zero_is_a_system_process():
    assert node_from_info(_info(raw=_raw(session=0)), set()).is_system
    assert not node_from_info(_info(raw=_raw(session=1)), set()).is_system


def test_a_service_is_marked_from_either_source():
    assert node_from_info(_info(), {"chrome.exe"}).is_service
    assert node_from_info(_info(), set(), None).is_service is False
    assert node_from_info(_info(is_service=True), set()).is_service


def test_a_process_with_threads_but_no_cycles_is_suspended():
    node = node_from_info(_info(raw=_raw(threads=5, cycles=0)), set())
    assert node.is_suspended and node.status == "suspended"
    running = node_from_info(_info(raw=_raw(threads=5, cycles=99)), set())
    assert not running.is_suspended and running.status == "running"


# ---- the GPU column -----------------------------------------------------

def test_the_gpu_column_is_filled_from_the_sampler():
    """It read 0.0 for every process since it was added, because nothing
    ever wrote to it."""
    node = node_from_info(_info(raw=_raw(pid=42)), set(), gpu={42: 37.5})
    assert node.gpu_percent == 37.5


def test_a_process_the_gpu_never_saw_reads_zero():
    node = node_from_info(_info(raw=_raw(pid=42)), set(), gpu={99: 37.5})
    assert node.gpu_percent == 0.0


def test_the_real_machine_attributes_gpu_to_some_process():
    from core.procengine.gpuinfo import GpuSampler

    with GpuSampler() as sampler:
        sampler.sample()
        time.sleep(1.0)
        sampler.sample()
        usage = sampler.process_usage()
    assert usage, "no process was given any GPU figure"
    assert all(0.0 <= value <= 100.0 for value in usage.values())


# ---- diffing ------------------------------------------------------------

def test_diff_reports_added_and_removed():
    old = {100: node_from_info(_info(raw=_raw(pid=100)), set())}
    new = {200: node_from_info(_info(raw=_raw(pid=200)), set())}
    added, removed, changed = diff_snapshots(old, new)
    assert added == [200] and removed == [100] and changed == []


def test_diff_notices_a_cpu_change():
    old = {100: node_from_info(_info(rates=Rates(cpu_percent=1.0)), set())}
    new = {100: node_from_info(_info(rates=Rates(cpu_percent=50.0)), set())}
    assert diff_snapshots(old, new)[2] == [100]


def test_diff_notices_a_gpu_only_change():
    """A row whose only movement is on the GPU has to repaint too, or the
    column sits at whatever it read the last time some other number moved."""
    old = {100: node_from_info(_info(), set(), gpu={100: 0.0})}
    new = {100: node_from_info(_info(), set(), gpu={100: 60.0})}
    assert diff_snapshots(old, new)[2] == [100]


def test_diff_notices_a_disk_only_change():
    old = {100: node_from_info(_info(rates=Rates(read_bps=0.0)), set())}
    new = {100: node_from_info(_info(rates=Rates(read_bps=4096.0)), set())}
    assert diff_snapshots(old, new)[2] == [100]


def test_an_unchanged_row_is_not_reported_as_changed():
    node = _info(rates=Rates(cpu_percent=1.0))
    old = {100: node_from_info(node, set())}
    new = {100: node_from_info(node, set())}
    assert diff_snapshots(old, new)[2] == []


# ---- lifecycle ----------------------------------------------------------

def test_stopping_releases_the_gpu_query(qapp):
    """The PDH query lives in the performance-counter service, so an
    abandoned one outlives the collector that opened it."""
    from core.procengine.gpuinfo import GpuSampler

    collector = ProcessCollector()
    collector._gpu = GpuSampler()
    collector.stop()
    assert collector._gpu is None


def test_stopping_a_collector_that_never_ran_is_harmless(qapp):
    ProcessCollector().stop()


# ---- the new / exited highlight -----------------------------------------

def _collector_with(snapshot, qapp):
    collector = ProcessCollector()
    collector._first = False
    collector._snapshot = snapshot
    collector._first_seen = {pid: 0.0 for pid in snapshot}
    return collector


def _n(pid):
    return node_from_info(_info(raw=_raw(pid=pid)), set())


def test_nothing_is_new_on_the_first_snapshot(qapp):
    """270 processes were all running before the pane opened. Flashing
    every one of them green says the machine just booted."""
    collector = ProcessCollector()
    seen = {}
    collector.snapshot_ready.connect(lambda s: seen.update(s))
    collector._on_snapshot({1: _n(1), 2: _n(2)})
    assert seen and not any(node.is_new for node in seen.values())


def test_a_process_that_appeared_is_flagged_new(qapp):
    collector = _collector_with({1: _n(1)}, qapp)
    collector._on_snapshot({1: _n(1), 2: _n(2)})
    assert collector._snapshot[2].is_new
    assert not collector._snapshot[1].is_new


def test_a_process_that_exited_is_held_and_flagged(qapp):
    """An exited process is in no snapshot any more, so the only way to
    colour its row red is to keep the row."""
    collector = _collector_with({1: _n(1), 2: _n(2)}, qapp)
    collector._on_snapshot({1: _n(1)})
    assert 2 in collector._snapshot
    assert collector._snapshot[2].is_deleted


def test_an_exited_process_is_dropped_once_the_highlight_expires(qapp):
    import modules.process_explorer.process_collector as module

    collector = _collector_with({1: _n(1), 2: _n(2)}, qapp)
    collector._on_snapshot({1: _n(1)})
    assert 2 in collector._snapshot

    collector._departed[2] = (collector._departed[2][0],
                              time.monotonic() - module.HIGHLIGHT_SECONDS - 1)
    collector._on_snapshot({1: _n(1)})
    assert 2 not in collector._snapshot


def test_a_reused_pid_is_a_new_process_not_the_old_one_returning(qapp):
    """Windows reuses pids briskly. A held-dead row whose pid comes back
    must give way rather than keep painting the successor red."""
    collector = _collector_with({1: _n(1), 2: _n(2)}, qapp)
    collector._on_snapshot({1: _n(1)})
    assert collector._snapshot[2].is_deleted

    collector._on_snapshot({1: _n(1), 2: _n(2)})
    assert not collector._snapshot[2].is_deleted
    assert collector._snapshot[2].is_new


def test_the_new_flag_lapses(qapp):
    import modules.process_explorer.process_collector as module

    collector = _collector_with({1: _n(1)}, qapp)
    collector._on_snapshot({1: _n(1), 2: _n(2)})
    assert collector._snapshot[2].is_new

    collector._first_seen[2] = time.monotonic() - module.HIGHLIGHT_SECONDS - 1
    collector._on_snapshot({1: _n(1), 2: _n(2)})
    assert not collector._snapshot[2].is_new


def test_the_first_seen_map_does_not_grow_for_ever(qapp):
    """A machine that churns processes -- a build, a script loop -- would
    otherwise accumulate a pid per process for the life of the pane."""
    collector = _collector_with({1: _n(1)}, qapp)
    for pid in range(2, 60):
        collector._on_snapshot({1: _n(1), pid: _n(pid)})
    assert len(collector._first_seen) <= 3
