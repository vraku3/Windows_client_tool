"""Dashboard explanations cover construction and the on-demand inspectors."""
from unittest.mock import patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QMenu

from ui.help.dashboard_text import CONTROLS, FILTERS, HEADERS, INTRO, TERMS
from ui.help.dashboard_ui import apply_help, explain_menu
from ui.tooltip_audit import audit_modules, inspect_widget


def test_dashboard_audit_has_no_gaps(qapp, tmp_path):
    rows = audit_modules(str(tmp_path), "Dashboard")
    assert len(rows) == 20
    assert all(row["error"] is None for row in rows), rows
    assert {row["module"]: row["gaps"] for row in rows if row["total"]} == {}


@pytest.mark.parametrize("mapping", [INTRO, HEADERS, CONTROLS, FILTERS, TERMS])
def test_explanations_are_not_empty(mapping):
    assert mapping
    assert all(key.strip() and value.strip() for key, value in mapping.items())


def test_thread_cpu_explains_its_distinct_scale(qapp):
    from modules.process_explorer.lower_pane.thread_view import ThreadView
    widget = ThreadView()
    tooltip = widget._table.horizontalHeaderItem(1).toolTip()
    assert "one logical processor" in tooltip
    assert "half-second" in tooltip
    assert inspect_widget(widget, "Threads")["total"] == 0
    widget.deleteLater()


def test_process_model_explains_memory_and_io(qapp):
    from modules.process_explorer.process_tree_model import ProcessTreeModel
    model = ProcessTreeModel()
    tip = lambda column: model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
    assert "including shared" in tip(3)
    assert "not physical-disk" in tip(4)
    assert "all logical processors" in tip(2)


def test_on_demand_search_and_recording_library_have_help(qapp, tmp_path):
    from modules.dashboard.flight_tab import SavedTracesDialog
    from modules.process_explorer.find_dialog import FindHandleDialog
    for widget in (FindHandleDialog(), SavedTracesDialog(str(tmp_path))):
        row = inspect_widget(widget, type(widget).__name__)
        assert row["total"] == 0, row["gaps"]
        widget.deleteLater()


def test_properties_explain_every_tab_and_do_not_duplicate_intros(qapp):
    from modules.process_explorer.process_node import ProcessNode
    from modules.process_explorer.properties_dialog import ProcessPropertiesDialog
    node = ProcessNode(123, "test", "", "", "", "running", 0)
    with patch.object(ProcessPropertiesDialog, "_start_watching"):
        widget = ProcessPropertiesDialog(node)
    row = inspect_widget(widget, "Properties")
    assert row["total"] == 0, row["gaps"]
    count = len(widget.findChildren(QLabel, "moduleIntro"))
    apply_help(widget, "Process properties")
    assert len(widget.findChildren(QLabel, "moduleIntro")) == count
    widget.deleteLater()


def test_menu_actions_have_status_and_tool_tips(qapp):
    menu = QMenu()
    for label in ("Kill", "Kill Tree", "Start service", "Copy service name", "End task (3)"):
        menu.addAction(label)
    priorities = menu.addMenu("Set priority")
    priorities.addAction("Realtime")
    explain_menu(menu)
    for action in menu.actions() + priorities.actions():
        assert action.statusTip()
        assert action.toolTip() != action.text()
    assert "no undo" in menu.actions()[0].toolTip()
    menu.deleteLater()
