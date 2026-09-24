r"""A module's minimum size must not become the main window's.

Real bug (found 2026-09-24): Driver Manager's rows of buttons add up to a
3476px minimum width, and selecting it made the main window 3660px wide --
wider than a 2560px monitor -- and it stayed that way for every tab after.
Log Viewer (2172px), TreeSize (1581px) and Monitor Control (1280px) did the
same. The page now hosts each module widget in a scroll area, so a pane wider
than the window scrolls instead of stretching the window off-screen.
"""
import tempfile

import pytest
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QScrollArea, QWidget

from core.base_module import BaseModule


class _WideModule(BaseModule):
    """A pane whose row of buttons cannot shrink below ~3000px."""

    name = "Wide"
    icon = ""
    description = "test double"
    group = "TOOLS"

    def create_widget(self) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        for i in range(30):
            button = QPushButton(f"A rather long button label {i}")
            button.setMinimumWidth(100)
            row.addWidget(button)
        return widget


@pytest.fixture
def window(qapp, monkeypatch):
    import ui.main_window as mw
    from app import App

    monkeypatch.setattr(mw, "is_admin", lambda: True)
    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    win = mw.MainWindow(app)
    win.register_module(_WideModule())
    win.resize(1280, 800)
    win.show()
    yield win
    win.close()
    try:
        app.shutdown()
    except Exception:  # noqa: BLE001 - teardown
        pass


def test_the_pane_really_is_wider_than_the_window(window):
    """Guards the test itself: without this the next one proves nothing."""
    window._on_module_selected("Wide")
    widget = window._module_widgets["Wide"]
    assert widget.minimumSizeHint().width() > 2500


def _settle(qapp):
    """Qt applies a layout's minimum size over several event passes."""
    for _ in range(10):
        qapp.processEvents()


def test_selecting_a_very_wide_module_does_not_widen_the_window(window, qapp):
    window._on_module_selected("Wide")
    _settle(qapp)
    assert window.width() <= 1300, window.width()


def test_the_window_can_still_be_made_small_with_a_wide_module_shown(window, qapp):
    window._on_module_selected("Wide")
    _settle(qapp)
    window.resize(900, 600)
    _settle(qapp)
    assert window.width() < 1000, window.width()


def test_the_module_widget_is_hosted_in_a_scroll_area(window):
    window._on_module_selected("Wide")
    widget = window._module_widgets["Wide"]
    assert isinstance(widget.parent().parent(), QScrollArea)
