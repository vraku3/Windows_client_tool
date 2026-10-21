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
from typing import Dict, List

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

# The phosphor themes are derived from dark, so their palettes are built rather
# than typed: see core/phosphor.py. They join the same dicts, so every reader
# (semantic(), chrome(), the contrast tests) treats them like dark and light.
from core import phosphor as _phosphor       # noqa: E402  (after the dicts it extends)

for _name in _phosphor.PHOSPHOR_THEMES:
    _sem, _chr, _bg = _phosphor.palettes(_name)
    SEMANTIC_PALETTES[_name] = _sem
    CHROME_PALETTES[_name] = _chr
    PANE_BACKGROUND[_name] = _bg

#: Categorical colours for charts (Cleanup's donut, bars and trend): eight
#: series plus a ninth, neutral one for the folded "Other (N)" slice.
#:
#: These are graphics, not text, so the rule is WCAG 2.x 1.4.11 (non-text
#: contrast): every one is held to 3:1 against `chrome("surface")`, the
#: colour charts are painted on, and neighbouring series are held apart, by
#: `tests/test_chart_palette.py`. The ORDER is part of the design: it puts
#: unlike hues next to each other, because neighbours are what share an edge
#: in a donut. Light is the dark set darkened, not recoloured, so a category
#: keeps its hue across a theme switch.
CHART_SERIES: Dict[str, List[str]] = {
    "dark": [
        "#5b9cf6",  # blue
        "#e8a33d",  # amber
        "#3fb68b",  # teal green
        "#d673b0",  # magenta
        "#45c0d6",  # cyan
        "#e5705f",  # coral
        "#9d84f2",  # violet
        "#b5ad3c",  # olive
        "#8c8c8c",  # Other: neutral grey
    ],
    "light": [
        "#1f66c9",
        "#b05f00",
        "#137a55",
        "#a63a78",
        "#0a7891",
        "#bf3b2a",
        "#6a48c9",
        "#6e6a00",
        "#6b6b6b",
    ],
}

#: How many distinct series there are before the palette repeats.
CHART_SERIES_COUNT = 8

#: A phosphor theme has one hue by definition, so its series are that hue at
#: alternating brightness ("r", along the background->foreground ramp) and
#: alternating paleness ("w", the foreground mixed toward white). Neighbours
#: alternate dark/light so a shared edge is always visible; nothing goes below
#: about 0.55 on the ramp, which is where amber drops under 3:1.
_PHOSPHOR_CHART_SPEC = (("r", 1.0), ("r", 0.58), ("w", 0.45), ("r", 0.82), ("w", 0.22),
                        ("r", 0.62), ("w", 0.66), ("r", 0.74), ("r", 0.55))
#: Monochrome's foreground is already near white, so paleness cannot tell its
#: series apart (#ececec vs #f1f1f1): it uses brightness alone.
_MONO_CHART_SPEC = (("r", 1.0), ("r", 0.52), ("r", 0.86), ("r", 0.44), ("r", 0.74),
                    ("r", 0.6), ("r", 0.94), ("r", 0.48), ("r", 0.67))

for _name, (_fg, _bg) in _phosphor.PHOSPHOR_THEMES.items():
    _spec = _MONO_CHART_SPEC if _name == "mono" else _PHOSPHOR_CHART_SPEC
    CHART_SERIES[_name] = [
        _phosphor.mix(_bg, _fg, _t) if _kind == "r" else _phosphor.mix(_fg, "#ffffff", _t)
        for _kind, _t in _spec
    ]

#: Script Library console colour schemes. Roles: background, text, selection,
#: ok / warn / error / info / muted (span foregrounds), and warn_back /
#: error_back -- a span BACKGROUND ("" = none). "PowerShell" is Windows
#: PowerShell's own console: #012456 blue, #EEEDF0 text, and errors/warnings
#: written red/yellow on black, the way the console host draws them.
CONSOLE_PRESETS: Dict[str, Dict[str, str]] = {
    "PowerShell": {
        "background": "#012456", "text": "#eeedf0", "selection": "#3a6ea5",
        "ok": "#00ff00", "warn": "#ffff00", "error": "#ff0000", "info": "#00ffff",
        "muted": "#c0c0c0", "warn_back": "#000000", "error_back": "#000000",
    },
    "PowerShell ISE": {
        "background": "#ffffff", "text": "#000000", "selection": "#add6ff",
        "ok": "#008000", "warn": "#ff8c00", "error": "#ff0000", "info": "#0000ff",
        "muted": "#696969", "warn_back": "", "error_back": "",
    },
    "Windows Terminal (Campbell)": {
        "background": "#0c0c0c", "text": "#cccccc", "selection": "#3a3a3a",
        "ok": "#16c60c", "warn": "#f9f1a5", "error": "#e74856", "info": "#61d6d6",
        "muted": "#767676", "warn_back": "", "error_back": "",
    },
    "Dark": {
        "background": "#1e1e1e", "text": "#d4d4d4", "selection": "#264f78",
        "ok": "#4ec9b0", "warn": "#e5c07b", "error": "#f44747", "info": "#4fc3f7",
        "muted": "#949494", "warn_back": "", "error_back": "",
    },
}
DEFAULT_CONSOLE = "PowerShell"

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


def chart(index: int) -> str:
    """Series colour `index` for a chart in the current theme.

    Cycles after CHART_SERIES_COUNT, so any index is valid. A chart with more
    series than that should fold the rest into "Other" (`chart_other()`)
    rather than repeat a colour two slices apart.
    """
    return CHART_SERIES[_current_theme][index % CHART_SERIES_COUNT]


def chart_other() -> str:
    """The neutral colour of a chart's folded "Other" series."""
    return CHART_SERIES[_current_theme][CHART_SERIES_COUNT]
