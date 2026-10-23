"""Construction-only tooltip ratchet; update the baseline only after review."""
import json
from pathlib import Path

# No QT_QPA_PLATFORM here: set at collection it switched the WHOLE session to
# offscreen and test_theme_light_coverage then failed for Scripts. The tool sets it.
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QStandardItemModel
from PyQt6.QtWidgets import (
    QComboBox, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox,
    QTableView, QTableWidget, QTabWidget, QVBoxLayout, QWidget,
)

from ui.tooltip_audit import audit_modules, inspect_widget, regressions


def test_gap_rules(qapp):
    root = QWidget()
    layout = QVBoxLayout(root)
    button = QPushButton("Missing")
    layout.addWidget(button)
    explained = QPushButton("Explained")
    explained.setToolTip("What this does")
    layout.addWidget(explained)
    combo = QComboBox()
    combo.setEditable(True)
    layout.addWidget(combo)
    layout.addWidget(QSpinBox())
    layout.addWidget(QLineEdit())
    table = QTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["Missing header", "Hidden"])
    table.setColumnHidden(1, True)
    layout.addWidget(table)
    view = QTableView()
    model = QStandardItemModel(0, 2, view)
    model.setHeaderData(0, Qt.Orientation.Horizontal, "Explained", Qt.ItemDataRole.ToolTipRole)
    view.setModel(model)
    layout.addWidget(view)
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Missing tab")
    layout.addWidget(tabs)
    intro = QLabel("Introduction")
    intro.setObjectName("moduleIntro")
    layout.addWidget(intro)
    menu = QMenu(root)
    action = QAction("&Open...", menu)
    menu.addAction(action)
    status = menu.addAction("Save")
    status.setStatusTip("Save the report")
    row = inspect_widget(root, "Example")
    assert row["intro"] is True
    assert row["headers"] == 2
    assert row["tabs"] == 1
    assert any(g["class"] == "QAction" for g in row["gaps"])
    # Combo/spin editor children must not become extra gaps.
    assert sum(g["class"] == "QLineEdit" for g in row["gaps"]) == 1
    assert not any(g["text"] in ("Explained", "Hidden", "Save") for g in row["gaps"])
    root.deleteLater()


def test_ratchet_rejects_increases_and_new_modules():
    gap = {"kind": "buttons", "class": "QPushButton", "objectName": "new", "text": "Run"}
    row = {"module": "Example", "total": 1, "gaps": [gap], "error": None}
    assert regressions([row], [])
    assert regressions([row], [{**row, "total": 0, "gaps": []}])
    assert not regressions([{**row, "total": 0, "gaps": []}], [row])


def test_build_failure_does_not_hide_other_modules(qapp, tmp_path, monkeypatch):
    from core.base_module import BaseModule
    from core.composite_module import CompositeModule
    from PyQt6.QtCore import QThreadPool, QRunnable
    import main

    class Broken(BaseModule):
        name = "Broken"

        def create_widget(self):
            raise ValueError("construction failed")

    class Good(BaseModule):
        name = "Good"

        def create_widget(self):
            class Forbidden(QRunnable):
                def run(self):
                    raise AssertionError("A scan must not run")
            QThreadPool.globalInstance().start(Forbidden())
            return QPushButton("Run")

        def on_activate(self):
            raise AssertionError("Never activate during an audit")

    class Host(CompositeModule):
        name = "Host"

        def __init__(self):
            super().__init__()
            self.children = [Good(), Broken()]

    def register(stub):
        for module in (Broken(), Good(), Host()):
            stub.module_registry.register(module)

    monkeypatch.setattr(main, "register_all_modules", register)
    rows = {row["module"]: row for row in audit_modules(str(tmp_path / "app"))}
    assert "construction failed" in rows["Broken"]["error"]
    assert "construction failed" in rows["Host > Broken"]["error"]
    assert rows["Good"]["buttons"] == rows["Host > Good"]["buttons"] == 1
    assert rows["Host"]["error"] is None


def test_tooltip_coverage(qapp, tmp_path):
    rows = audit_modules(app_data_dir=str(tmp_path / "app"))
    baseline = json.loads(Path(__file__).with_name("tooltip_baseline.json").read_text(encoding="utf-8"))
    failures = regressions(rows, baseline["modules"])
    assert not failures, "Tooltip coverage regressed:\n" + "\n".join(failures)
