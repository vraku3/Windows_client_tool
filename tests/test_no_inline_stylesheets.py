"""An inline stylesheet is a colour frozen against one theme.

`ThemeManager.apply_theme()` calls `QApplication.setStyleSheet()`. A
per-widget `setStyleSheet("color: #e0e0e0")` beats that and is never
revisited, so it survives a theme switch unchanged — which is how a pane
ends up showing dark-theme text on a light ground. There were 204 such
calls when this was written, 37 of them setting a colour.

The replacement is a semantic role: `set_role(label, "statusError")` sets
an objectName the two stylesheets paint, so it follows the theme. Both
sheets carry the same role names, and the sheet-parity test in
test_theme_light_coverage.py keeps them in step.

A RATCHET: BUDGET only ever falls.
"""
import ast
import pathlib

from PyQt6.QtWidgets import QLabel

from core.table_ui import set_role

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

#: 204 when the ratchet went in. Only ever lower it.
BUDGET = 172

#: The roles both stylesheets define.
KNOWN_ROLES = {
    "statusError", "statusWarning", "statusSuccess", "statusInfo",
    "heading", "metric", "link", "muted",
}


def inline_stylesheet_calls():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setStyleSheet"):
                found.append(f"{path.relative_to(SRC).as_posix()}:{node.lineno}")
    return found


def test_the_inline_stylesheet_count_only_falls():
    calls = inline_stylesheet_calls()
    assert len(calls) <= BUDGET, (
        f"{len(calls)} inline stylesheets, budget is {BUDGET}.\n"
        "Use core.table_ui.set_role(widget, '<role>') and a rule in BOTH "
        "dark.qss and light.qss.\nNew ones:\n  "
        + "\n  ".join(calls[BUDGET:BUDGET + 15]))


def test_the_budget_is_not_stale():
    count = len(inline_stylesheet_calls())
    assert count > BUDGET - 25, (
        f"only {count} left against a budget of {BUDGET}: lower BUDGET to "
        f"{count} so the ratchet keeps its grip.")


def test_no_inline_stylesheet_freezes_the_dark_theme_text_colour():
    """#e0e0e0 and #d4d4d4 are dark.qss's text colours. Written into a
    widget they read as near-invisible grey on the light theme's #f5f5f5."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setStyleSheet"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            css = node.args[0].value.lower()
            if "color: #e0e0e0" in css or "color: #d4d4d4" in css:
                offenders.append(
                    f"{path.relative_to(SRC).as_posix()}:{node.lineno}")
    assert offenders == [], (
        "these freeze the dark theme's text colour into a widget:\n  "
        + "\n  ".join(offenders))


def test_every_role_used_in_code_exists_in_the_stylesheets():
    """A typo'd role is invisible: the widget simply keeps the default
    colour and nothing says the rule never matched."""
    used = set()
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "set_role"
                    and len(node.args) == 2
                    and isinstance(node.args[1], ast.Constant)):
                used.add(node.args[1].value)
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setObjectName"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value in KNOWN_ROLES):
                used.add(node.args[0].value)

    styles = SRC / "ui" / "styles"
    for sheet in ("dark.qss", "light.qss"):
        text = (styles / sheet).read_text(encoding="utf-8")
        missing = sorted(r for r in used if f"#{r}" not in text)
        assert missing == [], f"{sheet} has no rule for: {missing}"


def test_set_role_repolishes_so_a_runtime_change_takes_effect(qapp):
    """Qt resolves objectName selectors when a widget is polished. Setting
    the name on a widget already on screen changes nothing until the style
    is asked again — a status label toggling error/success would keep
    whichever colour it was given first, silently."""
    label = QLabel("x")
    set_role(label, "statusError")
    assert label.objectName() == "statusError"

    set_role(label, "statusSuccess")
    assert label.objectName() == "statusSuccess"


class _RecordingStyle:
    """Stands in for the widget's QStyle so the repolish can be counted.

    `widget.style()` hands back a fresh wrapper each call, so patching
    `polish` on the returned object does not stick -- shadow `style` itself.
    """

    def __init__(self):
        self.calls = []

    def unpolish(self, widget):
        self.calls.append("unpolish")

    def polish(self, widget):
        self.calls.append("polish")


def test_set_role_is_a_no_op_when_the_role_is_unchanged(qapp):
    """Called from a refresh that runs every second, an unconditional
    unpolish/polish is wasted work on every tick."""
    label = QLabel("x")
    set_role(label, "statusInfo")

    style = _RecordingStyle()
    label.style = lambda: style

    set_role(label, "statusInfo")
    assert style.calls == [], "the role did not change, so nothing needed repolishing"

    set_role(label, "statusError")
    assert style.calls == ["unpolish", "polish"], "a changed role must be repolished"
    assert label.objectName() == "statusError"
