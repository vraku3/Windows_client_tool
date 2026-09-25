r"""The status bar's module text went stale after an asynchronous load.

Seen 2026-09-25: System Restore said "Restore Manager -- 0 points" above a
list of three restore points, because get_status_info() was read once when the
tab was selected, before the load finished.
"""
import tempfile

import pytest
from PyQt6.QtWidgets import QLabel

from core.base_module import BaseModule


class _Loading(BaseModule):
    name = "Loading"
    icon = ""
    description = "test double"
    group = "TOOLS"

    def __init__(self):
        super().__init__()
        self.count = 0

    def create_widget(self):
        return QLabel("x")

    def get_status_info(self) -> str:
        return f"{self.count} items"


@pytest.fixture
def window(qapp, monkeypatch):
    import ui.main_window as mw
    from app import App

    monkeypatch.setattr(mw, "is_admin", lambda: True)
    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = _Loading()
    app.module_registry.register(module)
    win = mw.MainWindow(app)
    win.register_module(module)
    win._on_module_selected("Loading")
    yield win, module
    win.close()
    try:
        app.shutdown()
    except Exception:  # noqa: BLE001
        pass


def test_the_text_is_read_when_the_tab_is_selected(window):
    win, _m = window
    assert win._status_bar._module_label.text() == "0 items"


def test_it_follows_the_module_after_a_later_load(window):
    win, module = window
    module.count = 3                     # the asynchronous load finishing
    win._status_info_timer.timeout.emit()
    assert win._status_bar._module_label.text() == "3 items"


def test_a_module_whose_status_raises_does_not_break_the_window(window, caplog):
    win, module = window
    module.get_status_info = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    win._status_info_timer.timeout.emit()          # must not raise
    assert win._status_bar._module_label.text() == "0 items"   # left as it was


def test_the_refresh_timer_is_running(window):
    win, _m = window
    assert win._status_info_timer.isActive()
