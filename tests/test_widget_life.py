r"""The widget-lifetime guard has to actually guard.

Fifteen places in this codebase ask "is this widget still alive?" before
touching it from a worker callback, because a Qt call on a deleted object
is a dead process, not a traceback. Eight of them wrote `import sip`.

**There is no top-level `sip` module under PyQt6** — it is `PyQt6.sip`. So
those eight took their `except ImportError` branch on every single call and
returned "valid" unconditionally. The guard was decoration: it read as
protection, cost a try/except per call, and protected nothing.

Found while fixing a real crash of exactly this shape in the Cleanup tab
(a scan result landing after the tab was destroyed, exit -1073740791).

One helper now, and a structural test that fails if anyone writes the bare
import again — because the failure mode here is silent by construction.
"""
import ast
import pathlib

import pytest
from PyQt6.QtWidgets import QLabel, QWidget

from core.widget_life import widget_is_valid

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def test_a_live_widget_is_valid(qapp):
    assert widget_is_valid(QLabel("still here")) is True


def test_a_deleted_widget_is_not_valid(qapp):
    from PyQt6 import sip

    widget = QLabel("about to go")
    sip.delete(widget)
    assert widget_is_valid(widget) is False


def test_none_is_not_valid():
    assert widget_is_valid(None) is False


def test_a_child_destroyed_with_its_parent_is_not_valid(qapp):
    from PyQt6 import sip

    parent = QWidget()
    child = QLabel("inside", parent)
    assert widget_is_valid(child) is True
    sip.delete(parent)
    assert widget_is_valid(child) is False, (
        "a child deleted along with its parent still read as alive")


def test_the_guard_actually_resolves_sip():
    """If this ever falls back, every caller is silently unprotected."""
    import core.widget_life as module

    assert module._sip is not None, (
        "widget_is_valid fell back to its no-sip branch — the guard is inert")


def _bare_sip_imports():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sip":
                        offenders.append(
                            f"{path.relative_to(SRC).as_posix()}:{node.lineno}")
    return offenders


def test_nobody_imports_the_bare_sip_module():
    """`import sip` always raises under PyQt6, so a guard built on it is
    dead code that looks like protection."""
    offenders = _bare_sip_imports()
    assert offenders == [], (
        "`import sip` does not exist under PyQt6 — use "
        "core.widget_life.widget_is_valid:\n  " + "\n  ".join(offenders))


@pytest.mark.parametrize("module_path", [
    "modules/dashboard/process_menu.py",
    "modules/network_diagnostics/network_module.py",
    "modules/process_explorer/lower_pane/activity_view.py",
    "modules/process_explorer/lower_pane/network_view.py",
    "modules/process_explorer/lower_pane/strings_view.py",
    "modules/process_explorer/process_explorer_module.py",
    "modules/security_dashboard/security_module.py",
    "modules/store_apps/store_apps_module.py",
    "modules/updates/store_updates_tab.py",
    "modules/updates/updates_module.py",
    "modules/wifi_analyzer/wifi_module.py",
    "ui/log_table_widget.py",
    "modules/cleanup/tabs/_scan_tab.py",
    "modules/cleanup/components/quick_cleanup_tab.py",
])
def test_every_guarding_module_uses_the_shared_helper(module_path):
    source = (SRC / module_path).read_text(encoding="utf-8")
    assert "core.widget_life" in source, (
        f"{module_path} still rolls its own widget-lifetime guard")
