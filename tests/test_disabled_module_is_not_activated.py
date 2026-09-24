r"""An admin-only module, unelevated, is a placeholder -- not a pane to activate.

Found 2026-09-24 driving every real module through the main window
unelevated: selecting Firewall Rules called `on_activate()` on a module whose
widget was never built (`AttributeError: _refresh_btn`), and the refresh timer
would have done the same on every tick.
"""
import tempfile

import pytest
from PyQt6.QtWidgets import QLabel, QWidget

from core.base_module import BaseModule


class _AdminOnly(BaseModule):
    name = "AdminOnly"
    icon = ""
    description = "test double"
    group = "TOOLS"
    requires_admin = True

    def __init__(self):
        super().__init__()
        self.built = self.activated = self.timer_asked = 0

    def create_widget(self) -> QWidget:
        self.built += 1
        return QLabel("real pane")

    def on_activate(self) -> None:
        self.activated += 1
        raise AttributeError("_refresh_btn")     # what the real pane did

    def get_refresh_interval(self):
        self.timer_asked += 1
        return 1000


@pytest.fixture
def window(qapp, monkeypatch):
    import core.module_registry as reg
    import ui.main_window as mw
    from app import App

    monkeypatch.setattr(reg, "is_admin", lambda: False)
    monkeypatch.setattr(mw, "is_admin", lambda: False)
    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = _AdminOnly()
    app.module_registry.register(module)
    app.module_registry.start_all(app)
    win = mw.MainWindow(app)
    win.register_module(module)
    yield win, module
    win.close()
    try:
        app.shutdown()
    except Exception:  # noqa: BLE001 - teardown
        pass


def test_the_module_really_is_disabled_here(window):
    win, module = window
    assert module in win._app.module_registry.disabled_modules


def test_selecting_a_disabled_module_never_activates_or_builds_it(window):
    win, module = window
    win._on_module_selected("AdminOnly")
    assert module.activated == 0 and module.built == 0
    assert module.timer_asked == 0
    assert "AdminOnly" not in win._module_refresh_timers
