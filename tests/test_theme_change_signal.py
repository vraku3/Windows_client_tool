"""ThemeManager must announce a theme change.

A stylesheet reaches every widget that paints through the style, and nothing
else. A custom `paintEvent` -- PerfMon's charts -- and a QStyledItemDelegate --
TreeSize's proportion bars -- both paint outside it, so they can only follow
the theme if something tells them it changed. `apply_theme()` used to just
call `QApplication.setStyleSheet()` and return, which is why the charts stayed
dark-theme coloured forever.
"""
import os
import tempfile


from core.theme_manager import ThemeManager

import pytest

# App-wide restyles must not repolish windows earlier modules left (conftest).
pytestmark = pytest.mark.usefixtures("fresh_widgets")

STYLES = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "styles")


def test_applying_a_theme_emits_its_name():
    manager = ThemeManager(STYLES)
    seen = []
    manager.theme_changed.connect(seen.append)
    manager.apply_theme("light")
    assert seen == ["light"]


def test_toggling_emits_the_theme_switched_to():
    manager = ThemeManager(STYLES)
    seen = []
    manager.theme_changed.connect(seen.append)
    manager.toggle()
    assert seen == ["light"]
    manager.toggle()
    assert seen == ["light", "dark"]


def test_a_theme_that_cannot_be_loaded_announces_nothing():
    """No signal when nothing changed -- listeners must not repaint on a lie."""
    manager = ThemeManager(tempfile.mkdtemp())
    seen = []
    manager.theme_changed.connect(seen.append)
    manager.apply_theme("light")
    assert seen == []
