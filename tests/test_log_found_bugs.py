"""Bugs found in the user's own app.log (2026-10-10), each pinned so it stays fixed.

1. The global crash dialog never appeared: the handler asked
   `w.WindowType.Dialog` of a widget, which raises AttributeError, for
   every visible window -- so an unexpected error was only ever logged.
2. The frozen Startup tab listed no scheduled tasks: pywin32 imports
   win32timezone lazily to convert COM dates, and the build never bundled it.
3. Cleanup warned "Failed to stop service wuauserv: 1062" whenever the
   trigger-started Windows Update service was already stopped -- the very
   state the guard wants.
"""
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)


def test_the_crash_dialog_is_shown_while_the_main_window_is_open(qapp, monkeypatch, caplog):
    from PyQt6.QtWidgets import QMessageBox, QWidget
    import main as app_main

    window = QWidget()
    window.show()                                    # a visible top-level window, as in the app
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    try:
        raise ValueError("boom")
    except ValueError:
        with caplog.at_level(logging.WARNING):
            app_main._global_exception_handler(*sys.exc_info())
    window.close()
    assert shown and "unexpected error" in shown[0].lower()
    assert "If dialog fails" not in caplog.text


def test_win32timezone_is_bundled():
    import pyinstaller_common
    assert "win32timezone" in pyinstaller_common.HIDDEN_IMPORTS


def test_an_already_stopped_update_service_is_not_a_warning_and_is_not_started(monkeypatch, caplog):
    import pywintypes
    import win32serviceutil
    from modules.cleanup import cleanup_history
    from modules.cleanup.cleanup_scanner import scanners_system as ss

    started = []

    def stop(name):
        raise pywintypes.error(1062, "ControlService", "The service has not been started.")
    monkeypatch.setattr(win32serviceutil, "StopService", stop)
    monkeypatch.setattr(win32serviceutil, "StartService", lambda name: started.append(name))
    monkeypatch.setattr(cleanup_history, "record_deleted", lambda *a, **k: None)
    with caplog.at_level(logging.WARNING):
        assert ss.delete_items([], stop_wuauserv=True) == (0, 0)
    assert "Failed to stop service" not in caplog.text
    assert started == []                     # we did not stop it, so we must not start it
