"""The Script Library console's colour schemes: blue PowerShell by default,
saved choices survive a round trip, and malformed config never yields an
unreadable console."""
import pathlib

from core.semantic_colors import CONSOLE_PRESETS, DEFAULT_CONSOLE
from modules.scripts_hub import console_style as cs

STYLES = pathlib.Path(__file__).resolve().parent.parent / "src" / "ui" / "styles"


def test_the_default_is_the_blue_powershell_console():
    style = cs.ConsoleStyle()
    assert DEFAULT_CONSOLE == "PowerShell"
    assert style.color("background") == "#012456"
    assert cs.from_config(None).color("background") == "#012456"


def test_every_preset_defines_every_role():
    for name, colors in CONSOLE_PRESETS.items():
        assert set(colors) == set(cs.ROLES), name
        for role, value in colors.items():
            assert value.startswith("#") or (value == "" and role.endswith("_back")), (name, role)


def test_a_saved_style_round_trips():
    style = cs.ConsoleStyle("Windows Terminal (Campbell)", {"ok": "#123456", "warn_back": ""},
                            "consolas", 12)
    back = cs.from_config(style.to_config())
    assert back == style
    assert back.color("ok") == "#123456" and back.color("warn_back") == ""
    assert back.color("error") == CONSOLE_PRESETS["Windows Terminal (Campbell)"]["error"]


def test_malformed_config_falls_back_rather_than_breaking_the_console():
    style = cs.from_config({"preset": "Nope", "font": "comic", "size": 99,
                            "colors": {"ok": "green", "text": "", "bogus": "#000000",
                                       "warn_back": "", "info": "#00ff00"}})
    assert style.preset == DEFAULT_CONSOLE
    assert (style.font, style.size) == (cs.DEFAULT_FONT, cs.DEFAULT_SIZE)
    # "" is only allowed for a span background; a word is never a colour.
    assert style.overrides == {"warn_back": "", "info": "#00ff00"}
    assert cs.from_config("not a dict") == cs.ConsoleStyle()


def test_the_widget_rule_carries_the_chosen_colours():
    qss = cs.widget_qss(cs.ConsoleStyle(overrides={"background": "#abcdef"}))
    assert "background-color: #abcdef" in qss and "#eeedf0" in qss


def test_both_themes_carry_every_font_and_size_rule():
    for sheet in ("dark.qss", "light.qss"):
        text = (STYLES / sheet).read_text(encoding="utf-8")
        for rule in cs.qss_rules().splitlines():
            assert rule in text, (sheet, rule)
