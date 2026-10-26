"""Regressions for the real-data pane design review."""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget, QVBoxLayout, QTabWidget, QStackedWidget
from ui.module_intro import ModuleIntro, add_module_intro


def owned_intros(root):
    """Count this hosting level, excluding independent tabs and stacks."""
    result = []
    def walk(widget):
        for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if child.objectName() == "moduleIntro":
                result.append(child)
            elif not isinstance(child, (QTabWidget, QStackedWidget)) and not child.property("networkHelpCard"):
                walk(child)
    walk(root)
    return result


def test_intro_guard_through_wrappers_preserves_tab_levels(qapp):
    root = QWidget(); layout = QVBoxLayout(root)
    body = QWidget(); QVBoxLayout(body); layout.addWidget(body)
    intro = add_module_intro(body, "A child pane.")
    assert add_module_intro(root, "Same pane wrapper.", hosting_page=True) is intro
    tabs = QTabWidget(); layout.addWidget(tabs)
    page = QWidget(); QVBoxLayout(page); tabs.addTab(page, "Independent")
    assert add_module_intro(page, "A separate tab.") is not intro
    assert len(owned_intros(root)) == 1


@pytest.mark.parametrize("hub", ["Dashboard", "Diagnose", "Network Diagnostics", "System Management", "Startup & Boot", "Debloat", "Scripts"])
def test_every_hub_child_has_one_intro_per_host(qapp, tmp_path, hub):
    from app import App
    from main import register_all_modules
    app = App(app_data_dir=str(tmp_path))
    register_all_modules(app)
    mod = next(m for m in app.module_registry.modules if m.name == hub)
    mod._is_admin = lambda: True
    try:
        mod.on_start(app)
        root = mod.create_widget()
        for index in mod._live:
            mod._ensure_built(index)
            page = mod._pages[index]
            # The permanent hosting wrapper is included: it used to add its
            # own intro before the child was built.
            count = len(owned_intros(page))
            assert count <= 1, (hub, mod.children[index].name, count)
            if hub == "Dashboard":
                assert count == 1, (hub, mod.children[index].name, count)
            assert page.findChildren(QLabel, "moduleIntro"), (hub, mod.children[index].name)
            for tabs in page.findChildren(QTabWidget):
                for i in range(tabs.count()):
                    assert len(owned_intros(tabs.widget(i))) <= 1, (hub, tabs.tabText(i))
    finally:
        mod.on_stop()
        app.shutdown()
        App.instance = None


def test_named_icons_use_symbol_font_or_style_icon(qapp, monkeypatch):
    from ui import empty_state as e
    monkeypatch.setattr(e.QFontDatabase, "families", lambda: ["Segoe MDL2 Assets"])
    for name, (codepoint, _) in e.ICONS.items():
        state = e.EmptyState(name, "Title", "Hint")
        assert state.glyph_label.font().family() == "Segoe MDL2 Assets"
        assert state.glyph_label.text() == chr(codepoint)
    monkeypatch.setattr(e.QFontDatabase, "families", lambda: [])
    assert not e.EmptyState("drive", "Title", "Hint").glyph_label.pixmap().isNull()


def test_gpu_identity_and_details(qapp, monkeypatch):
    from modules.hardware_inventory import hardware_tabs as h
    from types import SimpleNamespace
    monkeypatch.setattr(h.hr, "get_overview", lambda _: [])
    monkeypatch.setattr(h.hr, "get_cpu_info", lambda _: [])
    monkeypatch.setattr(h.hr, "get_memory_info", lambda _: ([], []))
    monkeypatch.setattr(h.hr, "get_gpu_info", lambda _: [{"Name": "AMD Radeon RX 7900 XTX", "RAM": "24.0 GB", "Driver Version": "32.0"}])
    monkeypatch.setattr(h.hr, "get_bios_info", lambda _: [])
    monkeypatch.setattr(h.ar, "read_monitors", lambda: ([], ""))
    monkeypatch.setattr(h.hr, "get_network_info", lambda _: [])
    root = QWidget(); layout = QVBoxLayout(root)
    h.setup_overview(layout, h.load_overview())
    from ui.glance import GlanceTile
    gpu = root.findChildren(GlanceTile)[2]
    assert gpu.value.text() == "AMD Radeon RX 7900 XTX"
    assert "VRAM: 24.0 GB" in gpu.sub.text()
    assert "Driver: 32.0" in gpu.sub.text()


def test_features_all_states_and_refused_read(qapp):
    from modules.windows_features.features_module import WindowsFeaturesModule
    mod = WindowsFeaturesModule(); root = QWidget(); mod._build_summary(QVBoxLayout(root))
    mod._update_glance(list(enumerate(["Enabled", "Disabled", "DisabledWithPayloadRemoved", "EnablePending", "DisablePending", "OddState"])))
    assert [t.value.text() for t in mod._glance.tiles] == ["1", "2", "2", "1"]
    mod._update_glance(None, "Access denied")
    assert all(t.value.text() == "\u2014" and "Access denied" in t.toolTip() for t in mod._glance.tiles)


@pytest.mark.parametrize("name,value", [("API_KEY", "short"), ("token", "short"), ("PWD", "short"), ("Plain", "sk-example"), ("Plain", "ghp_example"), ("Plain", "xoxb-example"), ("Plain", "AKIAexample"), ("Plain", "a"*40), ("Plain", "A1b2+/"*8)])
def test_secret_detection(name, value):
    from modules.env_vars.secret_values import is_secret, display_value, HIDDEN
    assert is_secret(name, value)
    assert display_value(name, value) == HIDDEN


def test_secret_show_hide_copy_and_refresh(qapp, monkeypatch):
    import winreg
    from modules.env_vars import env_vars_module as m
    from modules.env_vars.env_ops import EnvVar
    panel = m._EnvPanel(winreg.HKEY_CURRENT_USER, "Unused", "User")
    panel._all_rows = [EnvVar("API_KEY", "sk-private", winreg.REG_SZ), EnvVar("EDITOR", "vim", winreg.REG_SZ)]
    panel._apply_filter("")
    assert panel._table.item(0, 2).text() == m.HIDDEN
    assert "sk-private" not in panel._table.item(0, 2).toolTip()
    buttons = panel._table.cellWidget(0, 4).findChildren(QPushButton)
    show, copy = buttons
    show.click(); assert panel._table.item(0, 2).text() == "sk-private"
    show.click(); assert panel._table.item(0, 2).text() == m.HIDDEN
    qapp.clipboard().setText("untouched")
    monkeypatch.setattr(m, "_allow_secret", lambda *a: False)
    copy.click(); assert qapp.clipboard().text() == "untouched"
    monkeypatch.setattr(m, "_allow_secret", lambda *a: True)
    copy.click(); assert qapp.clipboard().text() == "sk-private"
    panel._apply_filter("")
    assert panel._table.item(0, 2).text() == m.HIDDEN
    assert panel._table.item(1, 2).text() == "vim"


def test_snapshot_redacts_by_default_and_explicit_export(qapp, tmp_path, monkeypatch):
    import json
    from modules.env_vars import env_vars_module as m
    from modules.env_vars.env_ops import EnvVar
    from types import SimpleNamespace
    import winreg
    mod = m.EnvVarsModule(); mod._widget = QWidget()
    rows = [EnvVar("API_KEY", "sk-private", winreg.REG_SZ), EnvVar("EDITOR", "vim", winreg.REG_SZ)]
    mod._sys_panel = mod._usr_panel = SimpleNamespace(variables=lambda: rows)
    path = tmp_path / "snapshot.json"
    monkeypatch.setattr(m.QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    monkeypatch.setattr(m, "_allow_secret", lambda *a: False)
    mod._save_snapshot()
    data = json.loads(path.read_text())
    assert data["user"]["API_KEY"] == m.HIDDEN
    assert data["user"]["EDITOR"] == "vim"
    monkeypatch.setattr(m, "_allow_secret", lambda *a: True)
    mod._save_snapshot()
    assert json.loads(path.read_text())["user"]["API_KEY"] == "sk-private"


def test_sensitive_snapshot_changes_remain_visible_without_values():
    from modules.env_vars.secret_values import snapshot_differences, HIDDEN
    lines = snapshot_differences({"user": {"TOKEN": "old-private"}}, {"user": {"TOKEN": "new-private"}})
    assert len(lines) == 1 and "changed TOKEN" in lines[0]
    assert HIDDEN in lines[0]
    assert "old-private" not in lines[0] and "new-private" not in lines[0]


def test_export_prompt_defaults_to_no(qapp, monkeypatch):
    from modules.env_vars import env_vars_module as m
    captured = []
    def question(*args):
        captured.append(args)
        return m.QMessageBox.StandardButton.No
    monkeypatch.setattr(m.QMessageBox, "question", question)
    assert not m._allow_secret(QWidget(), "Export", "Include secrets?")
    assert captured[0][-1] == m.QMessageBox.StandardButton.No


def test_effective_values_also_mask_secrets(qapp, monkeypatch):
    from types import SimpleNamespace
    from modules.env_vars import env_vars_module as m
    monkeypatch.setenv("API_KEY", "sk-inherited")
    panel = SimpleNamespace(variables=lambda: [])
    pane = m._EffectivePane(panel, panel, None)
    pane._refresh()
    row = next(r for r in range(pane._table.rowCount()) if pane._table.item(r, 0).text() == "API_KEY")
    assert pane._table.item(row, 3).text() == m.HIDDEN
    assert "sk-inherited" not in pane._table.item(row, 3).toolTip()
    pane._table.cellWidget(row, 5).findChild(QPushButton).click()
    assert pane._table.item(row, 3).text() == "sk-inherited"


def test_icon_paint_font_survives_application_stylesheet(qapp):
    from ui.empty_state import EmptyState
    state = EmptyState("registry", "Title", "Hint")
    state.setStyleSheet("QLabel { font-family: Arial; }")
    state.ensurePolished()
    if state.glyph_label.symbol_font is not None:
        assert state.glyph_label.symbol_font.family() in ("Segoe Fluent Icons", "Segoe MDL2 Assets")
    assert not state.grab().isNull()


def test_all_empty_state_callers_pass_known_names():
    import ast
    from pathlib import Path
    from ui.empty_state import ICONS
    paths = list(Path("src").rglob("*.py")) + [Path("tools/ui_gallery.py")]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "EmptyState":
                arg = node.args[0]
                if isinstance(arg, ast.Constant):
                    assert arg.value in ICONS, (str(path), node.lineno, arg.value)
