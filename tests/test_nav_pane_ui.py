"""Offscreen construction and preview checks with fake engine state."""
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit, QSpinBox

from modules.nav_pane.engine.model import NavEntry
from modules.nav_pane.engine.operations import Operations
from modules.nav_pane.engine.registry import MemoryRegistry
from modules.nav_pane.nav_pane_module import NavPane, NavPaneModule
from modules.nav_pane.widgets import preview_rows


def test_widget_builds_with_tooltips(qapp, tmp_path):
    engine = Operations(MemoryRegistry(), tmp_path / "journal", account_check=lambda: "",
                        profile=str(tmp_path), root_paths=[], notify=lambda: None)
    pane = NavPane(engine, QThreadPool.globalInstance())
    assert pane.findChild(QLabel, "moduleIntro") is not None
    for widget in pane.findChildren((QAbstractButton, QComboBox, QLineEdit, QSpinBox)):
        assert widget.toolTip() or widget.parentWidget().toolTip(), type(widget).__name__
    pane.resize(1100, 1000)
    pane.show()
    qapp.processEvents()
    pane.cancel()
    pane.close()


def test_preview_places_entry_under_desktop_or_beside_home():
    for show_all in (False, True):
        rows = preview_rows("Aplicații", 48, show_all)
        selected = next(row for row in rows if row[2])
        assert selected == ("Aplicații", int(show_all), True)
        assert rows.index(selected) < next(i for i, row in enumerate(rows) if row[0] == "Home")
        if show_all:
            assert rows[0] == ("Desktop", 0, False)
        below = preview_rows("Last", 80, show_all)
        assert below[-1][0] == "Last"


def test_lifecycle_before_widget_is_safe():
    module = NavPaneModule()
    module.on_deactivate()
    module.on_activate()
    module.on_stop()
