"""Colours that mean something, resolved for whichever theme is in force.

A pane that writes `#4ec9b0` has picked a colour for the dark theme and frozen
it. That reads 7.7:1 on the dark pane and 1.98:1 on the light one, so the light
theme got a pale smear where the dark theme got a clear "OK". The fix is the
same one the stylesheets use: name the MEANING, let the theme choose.

Kept out of the .qss files on purpose. These are applied from Python at moments
a stylesheet cannot express -- a table cell's foreground that depends on a
reading, a status word that changes as a job runs -- so they need to be
callable, not declarative.

Every value here is held to 4.5:1 against its own theme's pane background by
`tests/test_semantic_colors.py`, which computes the ratios rather than
trusting this comment.
"""
from typing import Dict

#: What each theme paints a pane, and therefore what these colours sit on.
PANE_BACKGROUND: Dict[str, str] = {
    "dark": "#1e1e1e",
    "light": "#f5f5f5",
}

SEMANTIC_PALETTES: Dict[str, Dict[str, str]] = {
    "dark": {
        "success": "#4ec9b0",
        "warning": "#e5c07b",
        "error": "#f44747",
        "info": "#4fc3f7",
        # What the Filter or Find box is looking for, picked out inside the
        # message. Violet on purpose: far enough from the error red, warning
        # amber, info blue and success teal that a match never reads as a
        # severity. 5.99:1 here, 5.97:1 on the light pane.
        "match": "#c586c0",
    },
    # Darkened rather than recoloured: the same hues, taken down until they
    # clear 4.5:1 on a light pane. A light-theme "OK" should still read green.
    "light": {
        "success": "#00695c",
        "warning": "#8a5300",
        "error": "#b3261e",
        "info": "#00639c",
        "match": "#6f42c1",
    },
}

#: Chrome for widgets that PAINT themselves, where a stylesheet cannot reach:
#: a QPainter canvas has no QSS rule to consult, and hardcoding a dark-theme
#: grey in the widget freezes it for the light theme too.
#:
#: Deliberately separate from SEMANTIC_PALETTES. These are surfaces and
#: outlines, not text on a pane, so the 4.5:1 rule that governs the palette
#: above does not apply and `test_semantic_colors.py` does not walk them --
#: holding a panel fill to a text contrast ratio would be meaningless. The
#: two text roles here ARE held to it, by the test that covers this dict.
CHROME_PALETTES: Dict[str, Dict[str, str]] = {
    "dark": {
        "surface": "#2b2b2b",
        "surface_selected": "#2f3b33",
        "surface_inactive": "#262626",
        "outline": "#5a5a5a",
        "text": "#e0e0e0",
        # Lighter than QLabel#muted's #858585, and deliberately: that value
        # is chosen against the PANE (#1e1e1e), where it reads 4.70:1. On a
        # card surface (#2b2b2b) the same grey is 3.84:1 and fails. Measured,
        # not guessed — the chrome test computes the ratio.
        "text_muted": "#949494",
        # The identify/countdown overlay is a HUD drawn over whatever is on
        # screen, so it stays dark in both themes on purpose.
        "overlay_surface": "#141414",
        "overlay_text": "#f0f0f0",
        "overlay_text_muted": "#b0b0b0",
        "notice_border": "#4a4335",
    },
    "light": {
        "surface": "#ffffff",
        "surface_selected": "#e3f2e6",
        "surface_inactive": "#ededed",
        "outline": "#9e9e9e",
        "text": "#202020",
        "text_muted": "#5f5f5f",
        "overlay_surface": "#141414",
        "overlay_text": "#f0f0f0",
        "overlay_text_muted": "#b0b0b0",
        "notice_border": "#c8b96a",
    },
}

_current_theme = "dark"


def set_theme(theme: str) -> None:
    """Follow `theme`. Unknown names are ignored -- the palette in force stays,
    which is better than blanking every status colour in the app."""
    global _current_theme
    if theme in SEMANTIC_PALETTES:
        _current_theme = theme


def current_theme() -> str:
    return _current_theme


def semantic(meaning: str) -> str:
    """The colour for `meaning` in the current theme.

    Raises KeyError on an unknown meaning: a typo that silently returned
    nothing would paint invisible text, which is the bug this module exists
    to stop.
    """
    return SEMANTIC_PALETTES[_current_theme][meaning]


def chrome(role: str) -> str:
    """A surface, outline or text colour for a widget that paints itself.

    Same contract as `semantic`, including raising on an unknown role: a
    typo returning nothing paints an invisible panel, and finding that by
    looking at it is exactly what this module exists to avoid.
    """
    return CHROME_PALETTES[_current_theme][role]
