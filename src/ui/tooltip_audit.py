"""Construction-only explanation audit. Importing this helper does not import Qt.

Counts include a missing moduleIntro (one gap). Composite rows measure only the
host chrome; children have their own rows. Internal Qt implementation controls
are counted under the requested widget types, except combo/spin line editors.
No event loop is pumped and Qt worker submissions are suppressed during builds.
"""
from collections import Counter
from contextlib import ExitStack
import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _text(value):
    return str(value or "").strip()


def _action_text(value):
    # Qt's default QAction tooltip strips mnemonics and dialog ellipses.
    text = _text(value).replace("&&", "\0").replace("&", "").replace("\0", "&")
    return text.replace("...", "").replace("\u2026", "").strip()


def inspect_widget(root, name):
    """Inspect existing descendants, including every existing tab page, once."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox,
        QLabel, QLineEdit, QMenu, QSlider, QTableWidget, QTabWidget,
        QToolBar, QTreeWidget, QWidget,
    )

    row = dict(module=name, buttons=0, headers=0, tabs=0, intro=False,
               total=0, gaps=[], error=None)
    seen = set()
    widgets = []

    def walk(widget):
        if widget is None or id(widget) in seen:
            return
        seen.add(id(widget))
        widgets.append(widget)
        for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            walk(child)
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                walk(widget.widget(index))

    walk(root)

    def gap(kind, obj, text="", **extra):
        row["gaps"].append(dict(kind=kind, **{"class": type(obj).__name__},
                                objectName=obj.objectName(), text=_text(text), **extra))
        if kind in ("buttons", "headers", "tabs"):
            row[kind] += 1

    actions = set()
    for widget in widgets:
        if isinstance(widget, QLabel) and widget.objectName() == "moduleIntro":
            row["intro"] = True
        interactive = isinstance(widget, (QAbstractButton, QComboBox, QAbstractSpinBox, QLineEdit, QSlider))
        if isinstance(widget, QLineEdit):
            parent = widget.parentWidget()
            while parent is not None:
                if isinstance(parent, (QComboBox, QAbstractSpinBox)):
                    interactive = False
                    break
                parent = parent.parentWidget()
        if interactive and not _text(widget.toolTip()):
            text = widget.text() if hasattr(widget, "text") else ""
            if isinstance(widget, QComboBox):
                text = widget.currentText()
            gap("buttons", widget, text)
        if isinstance(widget, (QToolBar, QMenu)):
            for action in widget.actions():
                if id(action) in actions or action.isSeparator():
                    continue
                actions.add(id(action))
                tooltip = _text(action.toolTip())
                if not _text(action.statusTip()) and (
                    not tooltip or _action_text(tooltip) == _action_text(action.text())
                ):
                    gap("buttons", action, action.text())
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                if not _text(widget.tabToolTip(index)):
                    gap("tabs", widget, widget.tabText(index), section=index)
        if not isinstance(widget, QAbstractItemView):
            continue
        header = None
        if hasattr(widget, "horizontalHeader"):
            header = widget.horizontalHeader()
        elif hasattr(widget, "header"):
            header = widget.header()
        model = widget.model()
        if header is None or model is None:
            continue
        for section in range(header.count()):
            if header.isSectionHidden(section):
                continue
            item = None
            if isinstance(widget, QTableWidget):
                item = widget.horizontalHeaderItem(section)
            elif isinstance(widget, QTreeWidget):
                item = widget.headerItem()
            if isinstance(widget, QTreeWidget) and item is not None:
                tooltip, label = item.toolTip(section), item.text(section)
            elif isinstance(widget, QTableWidget):
                tooltip = item.toolTip() if item else None
                label = item.text() if item else str(section + 1)
            else:
                tooltip = model.headerData(section, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
                label = model.headerData(section, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            if not _text(tooltip):
                gap("headers", widget, label, section=section)
    if not row["intro"]:
        gap("intro", root, "Missing moduleIntro label")
    row["total"] = len(row["gaps"])
    return row


def _add_source_locations(row, module):
    """Cheap, conservative source lookup: unique literal constructor matches.

    Dynamic labels and controls created in shared helpers remain unlocated;
    never present a module's class definition as the widget's creation site.
    """
    filename = inspect.getsourcefile(type(module))
    if not filename:
        return
    try:
        path = Path(filename)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError) as exc:
        import logging
        logging.getLogger(__name__).warning("Cannot locate audit controls in %s: %s", filename, exc)
        return
    locations = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        cls = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                locations.setdefault((cls, _text(arg.value)), set()).add(node.lineno)
    for gap in row["gaps"]:
        matches = locations.get((gap["class"], gap["text"]), set())
        if len(matches) == 1 and gap["text"]:
            gap["file"] = path.resolve().relative_to(Path(__file__).resolve().parents[2]).as_posix()
            gap["line"] = next(iter(matches))


def audit_modules(app_data_dir, module_name=None):
    """Build registered modules with real services, but never activate them.

    QThreadPool.start/tryStart are blocked even for pre-existing eager builders.
    AppFacts uses the same empty cache as tests/conftest.py. Composite lazy hooks
    are blocked so a parent cannot activate, duplicate, or hide a child's gaps.
    """
    from PyQt6.QtCore import QThreadPool, QTimer
    from PyQt6.QtWidgets import QApplication, QWidget
    from app import App
    from core.app_facts import AppFactsIndex
    from core.composite_module import CompositeModule
    from core.module_registry import ModuleRegistry
    from main import register_all_modules

    qapp = QApplication.instance() or QApplication([])
    rows = []
    requested = module_name.casefold() if module_name else None
    previous_app = App.instance
    with ExitStack() as stack:
        stack.enter_context(patch.object(QThreadPool, "start", lambda *a, **kw: None))
        stack.enter_context(patch.object(QThreadPool, "tryStart", lambda *a, **kw: False))
        stack.enter_context(patch.object(AppFactsIndex, "_cached", AppFactsIndex()))
        stack.enter_context(patch.object(AppFactsIndex, "invalidate", classmethod(lambda cls: None)))
        stack.enter_context(patch.object(CompositeModule, "_ensure_built", lambda *a: None))
        stack.enter_context(patch.object(CompositeModule, "_on_tab_changed", lambda *a: None))
        App.instance = None
        app = App(app_data_dir=app_data_dir)
        stack.callback(setattr, App, "instance", previous_app)
        stack.callback(app.shutdown)
        stub = SimpleNamespace(module_registry=ModuleRegistry())
        register_all_modules(stub)

        def build(module, label):
            root = None
            try:
                module.on_start(app)
                root = module.create_widget()
                if not isinstance(root, QWidget):
                    raise TypeError("create_widget() did not return a QWidget")
                row = inspect_widget(root, label)
                _add_source_locations(row, module)
            except Exception as exc:
                row = dict(module=label, buttons=0, headers=0, tabs=0,
                           intro=False, total=0, gaps=[], error=f"{type(exc).__name__}: {exc}")
            finally:
                if isinstance(root, QWidget):
                    for timer in root.findChildren(QTimer):
                        timer.stop()
                try:
                    module.on_stop()
                except Exception as exc:
                    rows.append(dict(module=label + " [teardown]", buttons=0,
                                     headers=0, tabs=0, intro=False, total=0,
                                     gaps=[], error=f"{type(exc).__name__}: {exc}"))
                if isinstance(root, QWidget):
                    root.deleteLater()
            rows.append(row)

        def visit(module, prefix="", include=False):
            label = f"{prefix} > {module.name}" if prefix else module.name
            selected = include or requested is None or requested in (label.casefold(), module.name.casefold())
            if selected:
                build(module, label)
            if isinstance(module, CompositeModule):
                for child in module.children:
                    visit(child, label, selected)

        for module in stub.module_registry.modules:
            visit(module)
    # Keep QApplication alive until all construction/teardown has completed.
    assert qapp is not None
    if requested and not rows:
        rows.append(dict(module=module_name, buttons=0, headers=0, tabs=0,
                         intro=False, total=0, gaps=[], error="No matching module"))
    return sorted(rows, key=lambda row: (-row["total"], row["module"]))


def regressions(rows, baseline):
    """Return actionable failures; an unbuildable module never passes silently."""
    previous = {row["module"]: row for row in baseline}
    failures = []
    for row in rows:
        name = row["module"]
        if row.get("error"):
            failures.append(f"{name}: audit failed: {row['error']}")
            continue
        old = previous.get(name, {"total": 0, "gaps": []})
        if row["total"] <= old["total"]:
            continue
        def signature(gap):
            return tuple(gap.get(key, "") for key in ("kind", "class", "objectName", "text", "section"))
        remaining = Counter(signature(gap) for gap in old["gaps"])
        added = []
        for gap in row["gaps"]:
            key = signature(gap)
            if remaining[key]:
                remaining[key] -= 1
            else:
                added.append(f"{gap['class']} objectName={gap['objectName']!r} text={gap['text']!r}")
        failures.append(f"{name}: {old['total']} -> {row['total']} gaps; new gaps:\n  " + "\n  ".join(added))
    return failures


def format_table(rows):
    lines = ["Module | Buttons | Headers | Tabs | Intro | Total", "--- | ---: | ---: | ---: | :---: | ---:"]
    for row in rows:
        lines.append(f"{row['module']} | {row['buttons']} | {row['headers']} | {row['tabs']} | {'yes' if row['intro'] else 'no'} | {row['total']}")
        if row.get("error"):
            lines.append(f"ERROR: {row['module']}: {row['error']}")
    return "\n".join(lines)
