r"""Chrome for widgets that paint themselves.

A QPainter canvas has no stylesheet rule to consult, so its surfaces and
outlines have to come from Python. Writing them inline freezes a dark-theme
grey into the light theme too — the same failure `semantic()` already exists
to prevent for status colours.

These are surfaces and outlines rather than text on a pane, so the 4.5:1
rule that governs `SEMANTIC_PALETTES` does not apply to most of them and
`test_semantic_colors.py` deliberately does not walk this dict. The two
roles that ARE text are held to it here.
"""
import pytest

from core.semantic_colors import (
    CHROME_PALETTES, PANE_BACKGROUND, chrome, set_theme,
)
from tests.test_semantic_colors import _contrast as contrast_ratio


@pytest.fixture(autouse=True)
def _restore_theme():
    yield
    set_theme("dark")


def test_both_themes_offer_the_same_chrome_roles():
    assert set(CHROME_PALETTES) == {"dark", "light"}
    assert CHROME_PALETTES["dark"].keys() == CHROME_PALETTES["light"].keys()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_every_chrome_value_is_a_colour(theme):
    for role, value in CHROME_PALETTES[theme].items():
        assert value.startswith("#") and len(value) == 7, f"{theme}/{role}"
        int(value[1:], 16)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_text_roles_are_readable_on_their_own_surface(theme):
    """`text` and `text_muted` are read, so they answer to the ratio."""
    surface = CHROME_PALETTES[theme]["surface"]
    for role in ("text", "text_muted"):
        ratio = contrast_ratio(CHROME_PALETTES[theme][role], surface)
        assert ratio >= 4.5, (
            f"{theme}/{role} is {ratio:.2f}:1 on its own surface")


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_overlay_text_is_readable_on_the_overlay(theme):
    """The overlay stays dark in both themes; its text must still read."""
    surface = CHROME_PALETTES[theme]["overlay_surface"]
    for role in ("overlay_text", "overlay_text_muted"):
        ratio = contrast_ratio(CHROME_PALETTES[theme][role], surface)
        assert ratio >= 4.5, f"{theme}/{role} is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_a_surface_is_distinguishable_from_the_pane_behind_it(theme):
    """A panel the same colour as the pane is an invisible panel."""
    pane = PANE_BACKGROUND[theme]
    for role in ("surface", "surface_selected", "surface_inactive"):
        assert CHROME_PALETTES[theme][role] != pane, f"{theme}/{role}"


def test_chrome_follows_the_theme_that_was_set():
    set_theme("light")
    assert chrome("surface") == CHROME_PALETTES["light"]["surface"]
    set_theme("dark")
    assert chrome("surface") == CHROME_PALETTES["dark"]["surface"]


def test_an_unknown_role_raises_rather_than_painting_nothing():
    with pytest.raises(KeyError):
        chrome("not-a-role")
