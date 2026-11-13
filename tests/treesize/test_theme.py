"""Tests for TreeSize dark/light theme switching and dark default."""

from PyQt6.QtGui import QColor

from modules.treesize.ui.options_dialog import DEFAULTS, OptionsDialog
from modules.treesize.ui.shell import TreeSizeShell
from modules.treesize.ui.theme import DARK, LIGHT, is_light_theme, palette, stylesheet


def test_theme_defaults_to_dark_even_if_windows_prefers_light(monkeypatch):
    """The default must be dark regardless of Windows AppsUseLightTheme."""
    monkeypatch.setattr("modules.treesize.ui.theme.windows_prefers_light", lambda: True)
    assert is_light_theme() is False
    assert is_light_theme(None) is False
    assert is_light_theme("dark") is False
    assert is_light_theme(False) is False
    assert palette() == DARK
    assert DARK["bg"] in stylesheet()


def test_theme_explicit_light():
    assert is_light_theme("light") is True
    assert is_light_theme(True) is True
    assert palette("light") == LIGHT
    assert LIGHT["bg"] in stylesheet("light")


def test_theme_system_follows_windows_preference(monkeypatch):
    monkeypatch.setattr("modules.treesize.ui.theme.windows_prefers_light", lambda: True)
    assert is_light_theme("system") is True
    assert palette("system") == LIGHT

    monkeypatch.setattr("modules.treesize.ui.theme.windows_prefers_light", lambda: False)
    assert is_light_theme("system") is False
    assert palette("system") == DARK


def test_options_dialog_theme_defaults_and_round_trip(qapp):
    assert DEFAULTS["theme"] == "dark"

    dialog = OptionsDialog(dict(DEFAULTS))
    assert dialog.theme.currentData() == "dark"

    dialog.theme.setCurrentIndex(dialog.theme.findData("light"))
    values = dialog.values()
    assert values["theme"] == "light"

    dialog.restore_defaults()
    assert dialog.values()["theme"] == "dark"


def test_shell_theme_switching(qapp):
    shell = TreeSizeShell()
    shell.set_theme("dark")
    assert shell._current_theme == "dark"
    assert shell.ribbon.action("theme.dark").isChecked()
    assert not shell.ribbon.action("theme.light").isChecked()

    # Treemap and SliceCharts have dark bg
    assert shell.chart.treemap._bg_color == QColor(DARK["bg"])
    assert shell.chart.pie._bg_color == QColor(DARK["bg"])
    assert shell.chart.bars._bg_color == QColor(DARK["bg"])

    shell.set_theme("light")
    assert shell._current_theme == "light"
    assert shell.ribbon.action("theme.light").isChecked()
    assert not shell.ribbon.action("theme.dark").isChecked()

    # Treemap and SliceCharts have light bg
    assert shell.chart.treemap._bg_color == QColor(LIGHT["bg"])
    assert shell.chart.pie._bg_color == QColor(LIGHT["bg"])
    assert shell.chart.bars._bg_color == QColor(LIGHT["bg"])


def test_shell_cycle_theme(qapp):
    shell = TreeSizeShell()
    shell.set_theme("dark")
    shell._cycle_theme()
    assert shell._current_theme == "light"
    shell._cycle_theme()
    assert shell._current_theme == "dark"
