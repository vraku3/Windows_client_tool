"""Presentation regression checks; never install or launch a repair."""
from types import SimpleNamespace
from PyQt6.QtWidgets import QApplication
from modules.updates.overview import UpdatesOverview


def test_scan_counts_and_failed_scan_are_distinct():
    app = QApplication.instance() or QApplication([])
    overview = UpdatesOverview()
    overview.set_apps([])
    assert overview.tiles_by_key["apps"].value.text() == "0"
    overview.set_windows([object()], [object(), object()])
    assert overview.tiles_by_key["windows"].value.text() == "1"
    assert overview.tiles_by_key["held"].value.text() == "2"
    overview.scan_failed("apps", "Access denied")
    assert overview.tiles_by_key["apps"].reason == "Access denied"
    assert overview.tiles_by_key["apps"].severity == "unknown"


def test_task_refusal_is_never_no_task():
    app = QApplication.instance() or QApplication([])
    overview = UpdatesOverview()
    overview.set_health(SimpleNamespace(exists=None, error="Refused"), None, "Registry denied")
    assert overview.tiles_by_key["task"].reason == "Refused"
    assert overview.tiles_by_key["restart"].reason == "Registry denied"
    overview.set_health(SimpleNamespace(exists=False, last_run=None), False)
    assert overview.tiles_by_key["task"].value.text() == "Not scheduled"
    assert overview.tiles_by_key["restart"].value.text() == "No"
