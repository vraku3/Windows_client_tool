import os

import pytest

from core import phosphor, semantic_colors
from core.theme_manager import ThemeManager

STYLES = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "styles")


def _dark():
    with open(os.path.join(STYLES, "dark.qss"), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("name", sorted(phosphor.PHOSPHOR_THEMES))
def test_recolouring_replaces_every_dark_accent_with_the_themes_own_ramp(name):
    out = phosphor.recolour_qss(_dark(), name)
    assert "#007acc" not in out.lower() and "#094771" not in out.lower()
    fg, bg = phosphor.PHOSPHOR_THEMES[name]
    assert bg.lower() in out.lower()                  # the pane maps to the theme background
    assert len(out) == len(_dark())                   # colours swap 1:1, nothing else moves


def test_the_ramp_runs_from_background_to_foreground_and_clamps():
    assert phosphor.mix("#000000", "#ffffff", 0.0) == "#000000"
    assert phosphor.mix("#000000", "#ffffff", 1.0) == "#ffffff"
    assert phosphor.mix("#000000", "#ffffff", 5.0) == "#ffffff"
    assert phosphor.ramp_position("#1e1e1e") == 0.0 and phosphor.ramp_position("#ffffff") == 1.0


@pytest.mark.parametrize("name", sorted(phosphor.PHOSPHOR_THEMES))
def test_applying_a_phosphor_theme_changes_the_sheet_and_the_python_palette(qapp, name):
    manager = ThemeManager(STYLES)
    seen = []
    manager.theme_changed.connect(seen.append)
    manager.apply_theme(name)
    try:
        assert manager.current_theme == name and seen == [name]
        assert semantic_colors.current_theme() == name
        assert semantic_colors.semantic("success") == phosphor.PHOSPHOR_THEMES[name][0]
        assert phosphor.PHOSPHOR_THEMES[name][1].lower() in qapp.styleSheet().lower()
    finally:
        manager.apply_theme("dark")


def test_every_theme_has_a_label_for_the_settings_dialog():
    for name in ThemeManager.THEMES:
        assert name in phosphor.LABELS
    assert ThemeManager.THEMES[:2] == ("dark", "light")
