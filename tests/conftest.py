import os
import sys

# Add src/ to path so tests can import `core.*` directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
# ...and tests/ itself, for shared helpers such as `_sparse`. APPENDED, never
# first: tests/ has its own `modules` package, which would shadow src/modules.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pytest
from PyQt6.QtWidgets import QApplication

@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def fresh_widgets(qapp):
    """Delete windows earlier test modules left behind, before an app-wide restyle.

    `app.setStyleSheet` repolishes EVERY live widget. Thousands of half-torn-down
    widgets from earlier modules made the theme tests crash natively in whole-suite
    runs (access violation / 0xC0000409, 2 of 2 runs on 2026-10-22), never alone.
    Only `qapp` is session-scoped, so nothing later still needs these windows.
    Opt in per module (pytestmark); an autouse version broke other tests before.
    """
    import gc
    from PyQt6.QtCore import QCoreApplication, QEvent
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    qapp.processEvents()
    gc.collect()
    yield


@pytest.fixture(autouse=True)
def _cleanup_scan_cache_cleared():
    """Don't let one test's measurement answer another test's scan.

    The cleanup scan cache is keyed on the scanner's `__name__`, which is
    right in the app (a scanner name is unique and stable, and the catalog
    builds a fresh callable per tab so the function OBJECT is not) and
    wrong across tests, where two files can each define a local
    `scan_slow`. Guarded on the module already being imported, so the tests
    that never touch cleanup pay nothing.
    """
    module = sys.modules.get("modules.cleanup.cleanup_scanner.scan_cache")
    if module is not None:
        module.invalidate()
    yield
    module = sys.modules.get("modules.cleanup.cleanup_scanner.scan_cache")
    if module is not None:
        module.invalidate()


@pytest.fixture(autouse=True)
def _cleanup_deletion_log_isolated(tmp_path, monkeypatch):
    """delete_items() logs every path it removes to %APPDATA%; dozens of
    tests delete scratch files through it, and none of that belongs in the
    real machine's audit log."""
    from modules.cleanup import cleanup_history
    monkeypatch.setattr(cleanup_history, "_deleted_path",
                        lambda: str(tmp_path / "cleanup_deleted.json"))


@pytest.fixture(autouse=True)
def _catalog_readers_restored():
    """Put back any security-catalog reader a test swapped out.

    `catalog/<category>.py` holds its CONTROLS at module level, so every
    load_catalog() hands back the SAME SecurityControl objects. A test that
    does `object.__setattr__(control, "reader", ...)` -- which several must,
    to prove a tab reads only its own controls -- changes them for the rest
    of the session. That is how test_security_reader_defender.py started
    failing in a whole-suite run while passing on its own.

    Guarded on the module already being imported, so the ~2,400 tests that
    never touch the catalog pay nothing.
    """
    module = sys.modules.get("modules.security_dashboard.catalog")
    if module is None:
        yield
        return
    catalog = module.load_catalog()
    originals = {cid: control.reader for cid, control in catalog.items()}
    yield
    for control_id, reader in originals.items():
        control = catalog.get(control_id)
        if control is not None and control.reader is not reader:
            object.__setattr__(control, "reader", reader)


@pytest.fixture(autouse=True)
def _app_facts_no_machine_scan(monkeypatch):
    """Populating a fake app list must never start a real registry/usage scan."""
    from core.app_facts import AppFactsIndex
    monkeypatch.setattr(AppFactsIndex, '_cached', AppFactsIndex())
    monkeypatch.setattr(AppFactsIndex, '_failure', None)
    yield
