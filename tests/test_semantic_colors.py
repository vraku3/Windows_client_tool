"""Success / warning / error colours must be readable in BOTH themes.

The values scattered through the modules were all picked against a dark pane:
#4ec9b0 reads 7.7:1 on #1e1e1e and 1.98:1 on #f5f5f5. Once the light theme
actually painted its panes light, every one of them became a pale smear -- the
same failure as the hardcoded backgrounds, one layer up.

A pane asks for the MEANING and the palette supplies the colour for whichever
theme is in force.
"""
import pytest

from core.semantic_colors import (
    PANE_BACKGROUND, SEMANTIC_PALETTES, semantic, set_theme,
)

#: dark and light, plus the four phosphor themes derived from dark (core/phosphor.py).
ALL_THEMES = {"dark", "light", "green", "amber", "blue", "mono"}


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    def channel(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    low, high = sorted((_luminance(a), _luminance(b)))
    return (high + 0.05) / (low + 0.05)


def test_both_themes_offer_the_same_meanings():
    assert set(SEMANTIC_PALETTES) == ALL_THEMES
    assert SEMANTIC_PALETTES["dark"].keys() == SEMANTIC_PALETTES["light"].keys()
    assert "success" in SEMANTIC_PALETTES["dark"]


@pytest.mark.parametrize("theme", sorted(ALL_THEMES))
def test_every_meaning_is_readable_on_its_own_pane(theme):
    background = PANE_BACKGROUND[theme]
    for meaning, colour in SEMANTIC_PALETTES[theme].items():
        ratio = _contrast(colour, background)
        assert ratio >= 4.5, (
            f"{theme}/{meaning}: {colour} on {background} is {ratio:.2f}:1")


def test_semantic_follows_the_theme_that_was_set():
    set_theme("dark")
    dark = semantic("success")
    set_theme("light")
    assert semantic("success") != dark
    assert semantic("success") == SEMANTIC_PALETTES["light"]["success"]
    set_theme("dark")


def test_an_unknown_meaning_is_a_programming_error_not_a_silent_blank():
    set_theme("dark")
    with pytest.raises(KeyError):
        semantic("chartreuse")


def test_applying_a_theme_updates_the_palette():
    """ThemeManager is the only thing that knows the theme changed."""
    import os
    from core.theme_manager import ThemeManager

    set_theme("dark")
    styles = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "styles")
    ThemeManager(styles).apply_theme("light")
    assert semantic("success") == SEMANTIC_PALETTES["light"]["success"]
    set_theme("dark")
