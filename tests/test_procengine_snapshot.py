"""One reading of the machine, joined and shaped into a tree.

Brings the three sources together -- the bulk syscall, the rate maths and
the cold cache -- and answers the question the tree view asks: who is whose
parent.

That question is harder than it looks, because a parent pid is only a
NUMBER. The parent may be long dead and its pid reused by something
unrelated, which is how a process tree ends up claiming Notepad started
sixty services.
"""
import os

import pytest

from core.procengine.ntquery import ProcessRaw
from core.procengine.snapshot import SnapshotSource, build_tree


def _raw(pid, ppid=0, name="test.exe", create_time=100):
    return ProcessRaw(
        pid=pid, ppid=ppid, name=name, threads=1, handles=1, session=1,
        base_priority=8, working_set=0, working_set_private=0,
        peak_working_set=0, private_bytes=0, peak_pagefile=0,
        peak_virtual_size=0, paged_pool=0,
        nonpaged_pool=0, pagefile=0, virtual_size=0, page_faults=0,
        hard_faults=0, kernel_time=0, user_time=0, cycles=0,
        create_time=create_time, read_bytes=0, write_bytes=0, other_bytes=0,
        read_ops=0, write_ops=0, other_ops=0)


# ---- the tree -----------------------------------------------------------

def test_a_child_is_placed_under_its_parent():
    rows = [_raw(1, 0, create_time=100), _raw(2, 1, create_time=200)]
    roots = build_tree(rows)
    assert [node.pid for node in roots] == [1]
    assert [child.pid for child in roots[0].children] == [2]


def test_an_orphan_becomes_a_root():
    """Its parent exited. Task Manager and Process Explorer both show these
    at the top level rather than hiding them."""
    roots = build_tree([_raw(2, ppid=999)])
    assert [node.pid for node in roots] == [2]


def test_a_parent_created_after_its_child_is_not_its_parent():
    """The pid-reuse trap, and the reason this is not a one-liner. A dead
    parent's pid gets reused; the orphan still points at the number, so a
    naive join hangs it under whatever process holds that pid now -- which
    is how a tree claims Notepad started sixty services."""
    rows = [_raw(1, 0, name="notepad.exe", create_time=500),
            _raw(2, ppid=1, name="orphan.exe", create_time=100)]

    roots = build_tree(rows)

    assert {node.pid for node in roots} == {1, 2}, \
        "the orphan was adopted by a process that started after it"
    assert roots[0].children == () or roots[1].children == ()


def test_a_process_is_never_its_own_parent():
    """Pid 0's parent is 0. Left alone that is an infinite loop."""
    roots = build_tree([_raw(0, ppid=0)])
    assert [node.pid for node in roots] == [0]
    assert roots[0].children == ()


def test_a_cycle_does_not_recurse_forever():
    """Two processes each claiming the other. Reuse makes this possible,
    and a recursive walk would blow the stack."""
    rows = [_raw(1, ppid=2, create_time=100),
            _raw(2, ppid=1, create_time=100)]
    roots = build_tree(rows)
    assert roots, "the cycle swallowed every process"
    assert len(_walk(roots)) == 2


def _walk(nodes):
    seen = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        seen.append(node.pid)
        stack.extend(node.children)
    return seen


def test_every_process_appears_exactly_once():
    rows = [_raw(1, 0, create_time=100), _raw(2, 1, create_time=200),
            _raw(3, 1, create_time=300), _raw(4, 2, create_time=400)]
    assert sorted(_walk(build_tree(rows))) == [1, 2, 3, 4]


def test_children_are_ordered_by_pid():
    """Stable order, or rows jump under the cursor between refreshes."""
    rows = [_raw(1, 0, create_time=100), _raw(5, 1, create_time=200),
            _raw(3, 1, create_time=200)]
    roots = build_tree(rows)
    assert [child.pid for child in roots[0].children] == [3, 5]


# ---- against the real machine -------------------------------------------

@pytest.fixture(scope="module")
def snapshot():
    source = SnapshotSource()
    source.read()          # first reading has no rates
    return source.read()


def test_the_real_machine_produces_a_snapshot(snapshot):
    assert len(snapshot.by_pid) > 20


def test_our_own_process_is_joined_from_all_three_sources(snapshot):
    mine = snapshot.by_pid[os.getpid()]
    assert mine.raw.working_set > 0, "no bulk data"
    assert mine.rates.cpu_percent is not None, "no rate on the second reading"
    assert mine.details.path is not None, "no cold data"


def test_the_real_tree_holds_every_process(snapshot):
    assert sorted(_walk(snapshot.roots)) == sorted(snapshot.by_pid)


def test_our_own_process_is_marked_as_ours(snapshot):
    assert snapshot.by_pid[os.getpid()].is_own_user is True


def test_a_system_process_is_not_marked_as_ours(snapshot):
    assert snapshot.by_pid[4].is_own_user is False


def test_services_are_marked_when_the_service_list_is_supplied():
    source = SnapshotSource()
    source.set_service_pids({os.getpid()})
    source.read()
    snapshot = source.read()
    assert snapshot.by_pid[os.getpid()].is_service is True


def test_reading_twice_is_cheap_because_the_cold_data_is_cached():
    """The two-tier design, measured: the second read must not re-resolve
    275 processes."""
    import time

    source = SnapshotSource()
    source.read()
    start = time.perf_counter()
    source.read()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.10, f"a warm read took {elapsed * 1000:.0f} ms"


def test_a_snapshot_reports_when_it_could_not_see_everything(snapshot):
    """Unelevated, about half the machine refuses. The pane has to be able
    to say so rather than showing blanks."""
    from core.procengine.snapshot import details_unreadable
    assert snapshot.readable == sum(i.details.path is not None for i in snapshot.by_pid.values())
    assert snapshot.refused == sum(details_unreadable(i) for i in snapshot.by_pid.values())
    assert snapshot.refused >= 0


# ---- Qt-free ------------------------------------------------------------

def test_the_engine_does_not_import_qt():
    import inspect

    from core.procengine import snapshot as module

    assert "PyQt6" not in inspect.getsource(module)
