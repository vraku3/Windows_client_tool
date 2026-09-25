"""Phosphor-style themes, derived from the dark theme. No Qt.

Green, amber and blue phosphor and a plain monochrome: one hue on a near-black
screen, like a terminal. Rather than four more 470-line stylesheets to keep in
step with `dark.qss` forever, each theme is `dark.qss` recoloured on load: every
colour in it is mapped onto a ramp from the theme's background to its
foreground by brightness, so a rule added to `dark.qss` tomorrow is themed for
free. The handful of colours that carry meaning (accent, selection, status) are
mapped explicitly instead of by brightness, because brightness would make a
blue accent and a dark selection nearly invisible.

`palettes()` builds the matching semantic and chrome palettes for Python-drawn
colours; `tests/test_semantic_colors.py` and `test_chrome_colors.py` hold every
one of them to the same contrast rules as dark and light.
"""
import re
from typing import Dict, Tuple

#: name -> (foreground, background). Foregrounds are the classic phosphor hues.
PHOSPHOR_THEMES: Dict[str, Tuple[str, str]] = {
    "green": ("#4dff88", "#031003"),
    "amber": ("#ffb000", "#140a00"),
    "blue": ("#5cc8ff", "#00101c"),
    "mono": ("#e6e6e6", "#000000"),
}

LABELS = {"dark": "Dark", "light": "Light", "green": "Green phosphor",
          "amber": "Amber phosphor", "blue": "Blue phosphor", "mono": "Monochrome"}

#: The dark theme's own pane and text greys: the two ends of the ramp.
_DARK_BG_LEVEL, _DARK_FG_LEVEL = 30, 212
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")

#: Colours that mean something. value = ramp position 0..1, or a semantic role.
_EXPLICIT = {
    "#007acc": 0.42, "#0a5a8f": 0.34, "#094771": 0.24, "#264f78": 0.28,
    "#4ec9b0": "success", "#f44747": "error", "#f44336": "error",
    "#e5c07b": "warning", "#4fc3f7": "info",
}

#: Status colours shared by every phosphor theme; success follows the hue.
_STATUS = {"warning": "#ffd24d", "error": "#ff7070", "info": "#6fd3ff", "match": "#e08cff"}


def _rgb(hex_colour: str) -> Tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(background: str, foreground: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    a, b = _rgb(background), _rgb(foreground)
    return "#{:02x}{:02x}{:02x}".format(*(round(x + (y - x) * t) for x, y in zip(a, b)))


def _level(hex_colour: str) -> float:
    r, g, b = _rgb(hex_colour)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ramp_position(hex_colour: str) -> float:
    """Where a dark-theme colour sits between the dark pane (0) and dark text (1)."""
    return max(0.0, min(1.0, (_level(hex_colour) - _DARK_BG_LEVEL) / (_DARK_FG_LEVEL - _DARK_BG_LEVEL)))


def recolour_qss(dark_qss: str, theme: str) -> str:
    """`dark.qss` with every colour mapped into `theme`'s ramp."""
    fg, bg = PHOSPHOR_THEMES[theme]
    status = {"success": fg, **_STATUS}

    def swap(match) -> str:
        original = match.group(0).lower()
        target = _EXPLICIT.get(original)
        if isinstance(target, str):
            return status[target]
        if target is not None:
            return mix(bg, fg, target)
        return mix(bg, fg, ramp_position(original))

    return _HEX.sub(swap, dark_qss)


def palettes(theme: str) -> Tuple[Dict[str, str], Dict[str, str], str]:
    """(semantic palette, chrome palette, pane background) for `theme`."""
    fg, bg = PHOSPHOR_THEMES[theme]
    semantic = {"success": fg, **_STATUS}
    chrome = {
        "surface": mix(bg, fg, 0.07),
        "surface_selected": mix(bg, fg, 0.16),
        "surface_inactive": mix(bg, fg, 0.04),
        "outline": mix(bg, fg, 0.38),
        "text": mix(bg, fg, 0.96),
        "text_muted": mix(bg, fg, 0.72),
        "overlay_surface": "#141414",
        "overlay_text": "#f0f0f0",
        "overlay_text_muted": "#b0b0b0",
        "notice_border": mix(bg, fg, 0.30),
    }
    return semantic, chrome, bg
