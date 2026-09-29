from modules.process_explorer.process_node import ProcessNode
from modules.process_explorer.process_tree_model import ProcessTreeModel, COL_PID, COL_CPU


def _node(pid, name, parent_pid=0, cpu=0.0, children=None):
    return ProcessNode(pid=pid, name=name, exe=f"C:\\{name}", cmdline="",
                       user="testuser", status="running", parent_pid=parent_pid,
                       children=children or [],
                       cpu_percent=cpu, memory_rss=1024*1024, memory_vms=2*1024*1024)


def test_model_loads_flat_snapshot():
    model = ProcessTreeModel()
    snapshot = {4: _node(4, "System"), 100: _node(100, "chrome.exe", parent_pid=4)}
    # add chrome as child of System
    snapshot[4].children.append(snapshot[100])
    model.load_snapshot(snapshot)
    # root should contain System (PID 4 has no parent in snapshot)
    assert model.rowCount() == 1
    parent_idx = model.index(0, 0)
    assert model.data(parent_idx) == "System"


def test_model_child_count():
    model = ProcessTreeModel()
    child = _node(100, "chrome.exe", parent_pid=4)
    root = _node(4, "System", children=[child])
    model.load_snapshot({4: root, 100: child})
    parent_idx = model.index(0, 0)
    assert model.rowCount(parent_idx) == 1


def test_model_column_pid():
    model = ProcessTreeModel()
    model.load_snapshot({4: _node(4, "System")})
    idx = model.index(0, COL_PID)
    assert model.data(idx) == "4"


def test_model_flat_mode():
    model = ProcessTreeModel()
    child = _node(100, "chrome.exe", parent_pid=4)
    root = _node(4, "System", children=[child])
    model.load_snapshot({4: root, 100: child})
    model.set_flat_mode(True)
    assert model.rowCount() == 2


def test_model_update_metrics():
    model = ProcessTreeModel()
    model.load_snapshot({4: _node(4, "System", cpu=1.0)})
    updated = _node(4, "System", cpu=50.0)
    model.update_nodes({4: updated})
    idx = model.index(0, COL_CPU)
    assert "50" in model.data(idx)


def test_appcontainer_arrives_late_like_the_other_cold_details():
    """appcontainer is resolved cold, the same as exe/user/cmdline, so a
    later tick's real reading must overwrite the first tick's None -- and
    a later None (the budget ran out again) must NOT blank out a value
    already learned."""
    model = ProcessTreeModel()
    first = _node(4, "System")
    first.appcontainer = None
    model.load_snapshot({4: first})

    resolved = _node(4, "System")
    resolved.appcontainer = True
    model.update_nodes({4: resolved})
    assert model._snapshot[4].appcontainer is True

    unresolved_again = _node(4, "System")
    unresolved_again.appcontainer = None
    model.update_nodes({4: unresolved_again})
    assert model._snapshot[4].appcontainer is True
