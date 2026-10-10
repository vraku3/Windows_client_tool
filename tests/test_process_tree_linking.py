"""Process Explorer's tree: relinked on every load, filtered by the search box.

Pins three measured defects:
- children lists were kept from the FIRST tick, so a process started later
  was in the snapshot but under no row (invisible) and an exited one was
  still drawn -- 354 processes, 346 reachable rows, over six live seconds;
- the toolbar's "Search processes" box was connected to nothing;
- every process start or exit reset the model and collapsed whatever
  branch the user had open.
"""
import os
import subprocess
import time

from PyQt6.QtCore import QModelIndex

from modules.process_explorer.process_node import ProcessNode, link_children
from modules.process_explorer.process_tree_model import (ProcessTreeModel,
                                                         matches)


def _node(pid, name, parent_pid=0, create_time=0, exe=None, cmdline=""):
    return ProcessNode(pid=pid, name=name, exe=exe or f"C:\\{name}",
                       cmdline=cmdline, user="VRAKU\\iorda",
                       status="running", parent_pid=parent_pid,
                       create_time=create_time)


def _reachable(model, parent=QModelIndex()):
    found = []
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        found.append(index.internalPointer().pid)
        found += _reachable(model, index)
    return found


# ---- linking ----------------------------------------------------------

def test_a_process_added_after_the_first_load_appears_under_its_parent():
    model = ProcessTreeModel()
    parent = _node(100, "explorer.exe", create_time=10)
    model.load_snapshot({100: parent})
    child = _node(200, "cmd.exe", parent_pid=100, create_time=20)
    model.load_snapshot({100: parent, 200: child})
    assert sorted(_reachable(model)) == [100, 200]
    assert parent.children == [child]


def test_an_exited_process_is_no_longer_drawn():
    parent = _node(100, "explorer.exe", create_time=10)
    child = _node(200, "cmd.exe", parent_pid=100, create_time=20)
    model = ProcessTreeModel()
    model.load_snapshot({100: parent, 200: child})
    model.load_snapshot({100: parent})
    assert _reachable(model) == [100]


def test_a_reused_parent_pid_is_not_the_parent():
    # pid 100 now belongs to a process that started AFTER the child.
    impostor = _node(100, "notepad.exe", create_time=50)
    child = _node(200, "powershell.exe", parent_pid=100, create_time=20)
    roots = link_children({100: impostor, 200: child})
    assert impostor.children == []
    assert {n.pid for n in roots} == {100, 200}


def test_system_keeps_registry_despite_its_later_create_time():
    # Measured: System (4) reports a create time after Registry's.
    system = _node(4, "System", create_time=500)
    registry = _node(464, "Registry", parent_pid=4, create_time=100)
    link_children({4: system, 464: registry})
    assert system.children == [registry]


def test_a_ppid_cycle_is_cut_not_left_unreachable():
    a = _node(10, "a.exe", parent_pid=20)
    b = _node(20, "b.exe", parent_pid=10)
    model = ProcessTreeModel()
    model.load_snapshot({10: a, 20: b})
    assert sorted(_reachable(model)) == [10, 20]


# ---- search -----------------------------------------------------------

def test_matches_on_each_field():
    node = _node(4242, "svchost.exe", exe=r"C:\Windows\System32\svchost.exe",
                 cmdline="svchost.exe -k netsvcs -p")
    assert matches(node, "4242")
    assert matches(node, "netsvcs")
    assert matches(node, "system32")
    assert matches(node, "iorda")
    assert not matches(node, "chrome")
    assert not matches(node, "424")       # a pid matches exactly


def test_filter_lists_buried_matches_flat_and_clears():
    root = _node(1, "wininit.exe", create_time=1)
    mid = _node(2, "services.exe", parent_pid=1, create_time=2)
    leaf = _node(3, "svchost.exe", parent_pid=2, create_time=3)
    model = ProcessTreeModel()
    model.load_snapshot({1: root, 2: mid, 3: leaf})
    model.set_filter("SVCHOST")
    assert model.rowCount() == 1
    assert model.index(0, 0).internalPointer() is leaf
    assert model.visible_count() == 1
    model.set_filter("")
    assert model.rowCount() == 1                 # back to the tree's root
    assert sorted(_reachable(model)) == [1, 2, 3]


def test_a_path_arriving_late_joins_the_filter():
    node = _node(7, "app.exe", exe="")
    model = ProcessTreeModel()
    model.load_snapshot({7: node})
    model.set_filter("program files")
    assert model.rowCount() == 0
    late = _node(7, "app.exe", exe=r"C:\Program Files\App\app.exe")
    model.update_nodes({7: late})
    assert model.rowCount() == 1


# ---- the live pane ----------------------------------------------------

def _pump(qapp, seconds):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


def _live_module(qapp):
    from PyQt6.QtCore import QThreadPool

    from modules.process_explorer.process_explorer_module import (
        ProcessExplorerModule)

    class _App:
        thread_pool = QThreadPool.globalInstance()
        config = None

    module = ProcessExplorerModule()
    module.on_start(_App())
    module.create_widget()
    module.on_activate()
    _pump(qapp, 2.5)
    return module


def test_real_machine_every_process_is_reachable_and_ours_shows(qapp):
    module = _live_module(qapp)
    try:
        child = subprocess.Popen(
            ["ping", "-n", "4", "127.0.0.1"], stdout=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        _pump(qapp, 2.0)
        model = module._model
        reachable = _reachable(model)
        assert len(reachable) == len(set(reachable))
        assert set(reachable) == set(model._snapshot)
        if child.poll() is None:
            mine = model._snapshot[os.getpid()]
            assert child.pid in [c.pid for c in mine.children]
        child.wait(timeout=10)
    finally:
        module.on_stop()


def test_real_machine_expanded_branch_survives_a_process_start(qapp):
    module = _live_module(qapp)
    try:
        model, view = module._model, module._tree_view
        parent_pid = next(pid for pid in model.expandable_pids()
                          if model.index_for_pid(pid).isValid())
        view.setExpanded(model.index_for_pid(parent_pid), True)
        child = subprocess.Popen(
            ["ping", "-n", "3", "127.0.0.1"], stdout=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        _pump(qapp, 2.0)          # at least one add -> model reset
        assert view.isExpanded(model.index_for_pid(parent_pid))
        child.wait(timeout=10)
    finally:
        module.on_stop()
