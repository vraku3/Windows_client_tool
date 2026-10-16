"""The Script Library console's look: colour scheme, font and size. Qt-free.

Stored in the app config as `modules.scripts.console` =
{"preset": name, "colors": {role: "#rrggbb"}, "font": key, "size": int}.
`colors` holds only the roles the person changed from the preset, so a
preset that improves later still reaches them. The font is chosen through a
QSS property selector (the application stylesheet sets every widget's font,
which a plain setFont() cannot beat), so only the fonts and sizes listed here
exist -- `dark.qss`/`light.qss` carry one rule per entry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional

from core.semantic_colors import CONSOLE_PRESETS, DEFAULT_CONSOLE

ROLES = ("background", "text", "selection", "ok", "warn", "error", "info", "muted",
         "warn_back", "error_back")
ROLE_LABELS = {"background": "Background", "text": "Text", "selection": "Selection",
               "ok": "[OK] lines", "warn": "Warnings / [WARN]", "error": "Errors / [BAD]",
               "info": "[INFO] lines", "muted": "NOT AVAILABLE lines",
               "warn_back": "Warning background", "error_back": "Error background"}
#: key -> (label, CSS font-family list). Keys are the QSS property values.
FONTS = {"lucida": ("Lucida Console", '"Lucida Console", Consolas, monospace'),
         "consolas": ("Consolas", "Consolas, monospace"),
         "cascadia": ("Cascadia Mono", '"Cascadia Mono", Consolas, monospace'),
         "courier": ("Courier New", '"Courier New", monospace')}
SIZES = (9, 10, 11, 12, 13, 14, 16)
DEFAULT_FONT, DEFAULT_SIZE = "lucida", 10
CONFIG_KEY = "modules.scripts.console"

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class ConsoleStyle:
    preset: str = DEFAULT_CONSOLE
    overrides: Dict[str, str] = field(default_factory=dict)
    font: str = DEFAULT_FONT
    size: int = DEFAULT_SIZE

    def color(self, role: str) -> str:
        """The colour for `role`; "" for a span background that is off."""
        if role in self.overrides:
            return self.overrides[role]
        return CONSOLE_PRESETS.get(self.preset, CONSOLE_PRESETS[DEFAULT_CONSOLE]).get(role, "")

    def colors(self) -> Dict[str, str]:
        return {r: self.color(r) for r in ROLES}

    def to_config(self) -> dict:
        return {"preset": self.preset, "colors": dict(self.overrides), "font": self.font,
                "size": self.size}


def from_config(data: Optional[dict]) -> ConsoleStyle:
    """A style from saved config; anything unknown or malformed falls back to
    the default rather than producing an unreadable console."""
    if not isinstance(data, dict):
        return ConsoleStyle()
    preset = data.get("preset") if data.get("preset") in CONSOLE_PRESETS else DEFAULT_CONSOLE
    raw = data.get("colors") if isinstance(data.get("colors"), dict) else {}
    overrides = {r: v for r, v in raw.items()
                 if r in ROLES and isinstance(v, str) and (_HEX.match(v) or (v == "" and r.endswith("_back")))}
    font = data.get("font") if data.get("font") in FONTS else DEFAULT_FONT
    size = data.get("size") if data.get("size") in SIZES else DEFAULT_SIZE
    return ConsoleStyle(preset, overrides, font, size)


def widget_qss(style: ConsoleStyle) -> str:
    """The console's own colours, set on the widget itself. These are the
    person's chosen scheme, the same whatever theme the app is in, so they
    belong on the widget; `palette(base)` in the app sheet was tried and Qt
    resolves it against the application palette, not the widget's."""
    c = style.colors()
    return (f"QPlainTextEdit#iseConsole {{ background-color: {c['background']}; "
            f"color: {c['text']}; selection-background-color: {c['selection']}; "
            f"selection-color: {c['text']}; }}")


def qss_rules() -> str:
    """The font rules both theme files carry, one per font and per size --
    generated here so the two lists cannot drift (a test compares them)."""
    out = []
    for key, (_label, family) in FONTS.items():
        out.append(f'QPlainTextEdit#iseConsole[consoleFont="{key}"] {{ font-family: {family}; }}')
    for size in SIZES:
        out.append(f'QPlainTextEdit#iseConsole[consoleSize="{size}"] {{ font-size: {size}pt; }}')
    return "\n".join(out)
