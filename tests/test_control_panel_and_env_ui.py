"""Tests for the Classic Control Panel tab and Environment Variables UI fixes."""
import winreg
from PyQt6.QtWidgets import QPushButton, QSizePolicy

from modules.control_panel import control_panel_catalog as cat
from modules.control_panel.control_panel_module import ControlPanelModule
from modules.env_vars import env_vars_module as ev
from modules.env_vars.env_ops import EnvVar


def test_control_panel_catalog_integrity():
    ids = [a.id for a in cat.ALL_APPLETS]
    assert len(ids) == len(set(ids)), "Duplicate applet IDs"
    assert len(cat.ALL_APPLETS) >= 35
    for applet in cat.ALL_APPLETS:
        assert applet.category in cat.CATEGORIES
        assert applet.launch_args, f"{applet.id} missing launch_args"
        assert applet.command_label


def test_control_panel_filtering_and_counts():
    counts = cat.category_counts()
    assert counts["All"] == len(cat.ALL_APPLETS)
    assert sum(counts[c] for c in cat.CATEGORIES) == len(cat.ALL_APPLETS)

    net = cat.filter_applets(category="Network & Internet")
    assert any(a.command_label == "ncpa.cpl" for a in net)
    assert all(a.category == "Network & Internet" for a in net)

    sound = cat.filter_applets(query="mmsys.cpl")
    assert [a.id for a in sound] == ["mmsys"]


def test_control_panel_widget_search_and_copy(qapp):
    mod = ControlPanelModule()
    mod.on_start(None)
    widget = mod.create_widget()
    assert widget is not None

    pane = mod._pane
    pane.search_edit.setText("ncpa.cpl")
    assert "Showing 1 of" in pane.status.text()

    applet = cat.filter_applets(query="ncpa.cpl")[0]
    pane._copy_cmd(applet)
    assert qapp.clipboard().text() == "ncpa.cpl"


def test_env_vars_copy_button_fits_and_actions_column_does_not_stretch(qapp):
    panel = ev._EnvPanel(winreg.HKEY_CURRENT_USER, "Unused", "User")
    panel._all_rows = [
        EnvVar("API_KEY", "sk-private", winreg.REG_SZ),
        EnvVar("EDITOR", "code", winreg.REG_SZ),
    ]
    panel._apply_filter("")

    assert getattr(panel._table, "_no_stretch_last", False) is True
    assert getattr(panel._table, "_custom_column_fit", False) is True
    assert getattr(panel._table, "_keep_hidden_vertical_header", False) is True
    assert panel._table.rowHeight(0) >= 36
    assert panel._table.rowHeight(1) >= 36

    for row in (0, 1):
        cell = panel._table.cellWidget(row, 4)
        buttons = cell.findChildren(QPushButton)
        assert buttons
        for btn in buttons:
            assert 20 <= btn.height() <= panel._table.rowHeight(row) - 8
            assert btn.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
            assert btn.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed


def test_env_vars_module_uses_subtabs_instead_of_four_squished_panes(qapp):
    mod = ev.EnvVarsModule()
    mod.app = type("A", (), {"thread_pool": None})()
    mod.create_widget()
    assert mod._subtabs.count() == 3
    labels = [mod._subtabs.tabText(i) for i in range(mod._subtabs.count())]
    assert labels == [
        "System & User Variables",
        "Effective (This Process)",
        "PATH Analysis",
    ]
