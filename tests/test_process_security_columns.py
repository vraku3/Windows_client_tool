"""Process Explorer's optional security columns: Protection, DEP, ASLR, CFG.

Measured 2026-10-10: 332 processes read in 0.02 s unelevated, 192 readable.
CFG "Enabled" by policy for 192, but 31 of those images were never built
with CFG -- the column must say "System DLLs only", not "On".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from core.procengine import mitigations as mt  # noqa: E402
from core.procengine.mitigations import MitigationCache, MitigationReport, tree_cells  # noqa: E402


def report(**kw) -> MitigationReport:
    r = MitigationReport(pid=1)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_a_refused_process_says_refused_in_every_column_never_off():
    cells = tree_cells(report(error="Access is denied."))
    assert {k: v[0] for k, v in cells.items()} == {k: "—" for k, _h in mt.TREE_COLUMNS}
    assert "Access is denied" in cells["dep"][1]


def test_cfg_on_by_policy_but_not_in_the_image_is_system_dlls_only():
    r = report(protection="None", dep="Enabled (permanent)", image_cfg=False, image_aslr=True)
    r.flags = {mt.CFG: 1, mt.ASLR: 0x5}
    cells = tree_cells(r)
    assert cells["cfg"][0] == "System DLLs only" and "not compiled with CFG" in cells["cfg"][1]
    assert cells["dep"][0] == "On" and cells["protection"][0] == ""
    assert cells["aslr"][0] == "High entropy"


def test_permanent_dep_off_and_a_protected_process():
    r = report(protection="PsProtectedSignerAntimalware-Light", dep="Disabled (permanent)")
    cells = tree_cells(r)
    assert cells["dep"][0] == "Off"
    assert cells["protection"][0] == "PPL Antimalware-Light"


def test_no_report_yet_is_blank_not_refused():
    assert all(v == ("", "") for v in tree_cells(None).values())


def test_each_process_is_read_once_and_a_reused_pid_is_read_again():
    calls = []
    cache = MitigationCache(reader=lambda pid, path: calls.append(pid) or report())
    cache.get(10, 100.0, "a.exe")
    cache.get(10, 100.0, "a.exe")
    assert calls == [10]
    cache.prune({10: 200.0})                 # pid 10 is now a different process
    cache.get(10, 200.0, "b.exe")
    assert calls == [10, 10]


def test_the_model_shows_and_sorts_the_columns(qapp):
    from PyQt6.QtCore import Qt
    from modules.process_explorer.process_node import ProcessNode
    from modules.process_explorer.process_tree_model import (COL_CFG, COL_DEP, COLUMNS,
                                                             ProcessTreeModel)
    on = report(protection="None", dep="Enabled")
    on.flags, on.image_cfg = {mt.CFG: 1}, True
    nodes = {
        1: ProcessNode(1, "a.exe", "", "", "", "running", 0, mitigations=on),
        2: ProcessNode(2, "b.exe", "", "", "", "running", 0, mitigations=report(error="denied")),
    }
    model = ProcessTreeModel()
    model.load_snapshot(nodes)
    assert COLUMNS[COL_DEP] == "DEP" and model.columnCount() == len(COLUMNS)
    rows = {model.index(r, 0).data(): model.index(r, COL_CFG).data() for r in range(model.rowCount())}
    assert rows == {"a.exe": "On", "b.exe": "—"}
    model.sort(COL_CFG, Qt.SortOrder.AscendingOrder)
    assert model.index(0, 0).data() == "a.exe"            # refused sorts last


def test_the_security_columns_start_hidden(qapp):
    from modules.process_explorer.process_explorer_module import ProcessExplorerModule
    from modules.process_explorer.process_tree_model import COL_CFG, COL_PATH
    mod = ProcessExplorerModule()
    mod.create_widget()
    assert mod._tree_view.isColumnHidden(COL_CFG) and not mod._tree_view.isColumnHidden(COL_PATH)
    mod._set_column(COL_CFG, True)
    assert not mod._tree_view.isColumnHidden(COL_CFG)
    mod.on_stop()


def test_live_every_process_gets_a_report():
    from modules.process_explorer.process_collector import build_snapshot
    snap = build_snapshot(set(), cold_budget=None, mitigations=MitigationCache())
    assert snap and all(n.mitigations is not None for n in snap.values())
    readable = [n for n in snap.values() if not n.mitigations.error]
    assert readable                                    # our own processes always answer
