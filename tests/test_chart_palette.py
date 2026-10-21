"""The chart palette and the Cleanup QSS roles hold up in every theme.

Chart marks are graphics, so the rule is WCAG 1.4.11 (non-text contrast,
3:1) against the surface they are painted on -- ``chrome("surface")``, which
is also what ``QFrame#chartCard`` paints. Neighbouring series must also be
told apart, because neighbours are what share an edge in a donut.

The QSS half checks that every shared Cleanup objectName has a rule in BOTH
sheets, and that the text roles among them read at 4.5:1 on the background
they sit on -- computed from the sheets, including the phosphor themes'
recoloured copy of dark.qss, rather than trusted from a comment.
"""
import pathlib
import re

import pytest

from core import phosphor
from core.semantic_colors import (
    CHART_SERIES, CHART_SERIES_COUNT, CHROME_PALETTES, PANE_BACKGROUND,
    chart, chart_other, set_theme,
)
from tests.test_semantic_colors import _contrast

ALL_THEMES = {"dark", "light", "green", "amber", "blue", "mono"}
STYLES = pathlib.Path(__file__).resolve().parent.parent / "src" / "ui" / "styles"

#: The QSS contract of the Cleanup redesign (ui-plan section 4). safetyBadge
#: is delegate-painted and deliberately has no rule.
SHARED_ROLES = ("cleanupHeader", "kpiTile", "kpiValue", "kpiCaption", "recoCard",
                "recoCardHigh", "detailPane", "chartCard", "emptyState")

#: Smallest RGB distance allowed between neighbouring series (cyclic, Other
#: included). The phosphor themes' closest pair is ~54.
MIN_NEIGHBOUR_DISTANCE = 50


@pytest.fixture(autouse=True)
def _restore_theme():
    yield
    set_theme("dark")


def _rgb(colour):
    h = colour.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def _distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** 0.5


def test_every_theme_has_a_full_chart_palette():
    assert set(CHART_SERIES) == ALL_THEMES
    for theme, series in CHART_SERIES.items():
        assert len(series) == CHART_SERIES_COUNT + 1, theme
        for value in series:
            assert re.fullmatch(r"#[0-9a-f]{6}", value), f"{theme}: {value}"


@pytest.mark.parametrize("theme", sorted(ALL_THEMES))
def test_every_series_reaches_3_to_1_on_the_chart_surface(theme):
    surface = CHROME_PALETTES[theme]["surface"]
    for i, colour in enumerate(CHART_SERIES[theme]):
        ratio = _contrast(colour, surface)
        assert ratio >= 3.0, f"{theme} series {i} {colour} is {ratio:.2f}:1 on {surface}"


@pytest.mark.parametrize("theme", sorted(ALL_THEMES))
def test_neighbouring_series_are_told_apart(theme):
    series = CHART_SERIES[theme]
    for i, colour in enumerate(series):
        nxt = series[(i + 1) % len(series)]
        d = _distance(colour, nxt)
        assert d >= MIN_NEIGHBOUR_DISTANCE, f"{theme}: {colour} next to {nxt} ({d:.0f})"


@pytest.mark.parametrize("theme", sorted(ALL_THEMES))
def test_no_two_series_are_the_same_colour(theme):
    assert len(set(CHART_SERIES[theme])) == len(CHART_SERIES[theme])


def test_chart_accessor_cycles_and_follows_the_theme():
    set_theme("dark")
    assert chart(0) == CHART_SERIES["dark"][0]
    assert chart(CHART_SERIES_COUNT) == chart(0)
    assert chart(CHART_SERIES_COUNT + 3) == chart(3)
    assert chart_other() == CHART_SERIES["dark"][CHART_SERIES_COUNT]
    set_theme("light")
    assert chart(0) == CHART_SERIES["light"][0]
    assert chart_other() == CHART_SERIES["light"][CHART_SERIES_COUNT]
    set_theme("amber")
    assert chart(1) == CHART_SERIES["amber"][1]


# --------------------------------------------------------------------------
# QSS roles
# --------------------------------------------------------------------------

def _sheet(theme):
    if theme == "light":
        return (STYLES / "light.qss").read_text(encoding="utf-8")
    dark = (STYLES / "dark.qss").read_text(encoding="utf-8")
    return dark if theme == "dark" else phosphor.recolour_qss(dark, theme)


def _rule(sheet, selector):
    """The declarations of the LAST rule whose selector list names ``selector``."""
    found = None
    sheet = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", sheet):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector in selectors:
            found = match.group(2)
    return found


def _prop(decls, name):
    m = re.search(rf"(?<![-\w]){name}\s*:\s*(#[0-9a-fA-F]{{6}})", decls or "")
    return m.group(1).lower() if m else None


@pytest.mark.parametrize("sheet_name", ["dark.qss", "light.qss"])
def test_every_shared_role_has_a_rule_in_both_sheets(sheet_name):
    sheet = (STYLES / sheet_name).read_text(encoding="utf-8")
    missing = [r for r in SHARED_ROLES if not re.search(rf"#{r}\b", sheet)]
    assert not missing, f"{sheet_name} lacks {missing}"


@pytest.mark.parametrize("theme", sorted(ALL_THEMES))
def test_chart_card_is_the_surface_charts_paint(theme):
    """The palette's 3:1 is measured against chrome("surface"); the card must
    be that colour (to within the phosphor recolouring's rounding)."""
    card = _prop(_rule(_sheet(theme), "QFrame#chartCard"), "background-color")
    assert card is not None
    assert _distance(card, CHROME_PALETTES[theme]["surface"]) <= 4, (theme, card)


#: (text role, the selectors whose background it is drawn on)
TEXT_ON = (
    ("QLabel#kpiValue", ("QFrame#kpiTile",)),
    ("QLabel#kpiCaption", ("QFrame#kpiTile", "QFrame#chartCard")),
    ("QLabel#chartTitle", ("QFrame#chartCard",)),
    ("QLabel#emptyState", ("QFrame#kpiTile", "QFrame#chartCard", None)),   # None = pane
    ("QWidget#detailPane", ("QWidget#detailPane",)),
)


@pytest.mark.parametrize("theme", sorted(ALL_THEMES))
def test_text_roles_are_readable_where_they_sit(theme):
    sheet = _sheet(theme)
    for text_sel, backgrounds in TEXT_ON:
        colour = _prop(_rule(sheet, text_sel), "color")
        assert colour, f"{theme}: {text_sel} has no colour"
        for bg_sel in backgrounds:
            bg = (PANE_BACKGROUND[theme] if bg_sel is None
                  else _prop(_rule(sheet, bg_sel), "background-color"))
            assert bg, f"{theme}: {bg_sel} has no background"
            ratio = _contrast(colour, bg)
            assert ratio >= 4.5, f"{theme}: {text_sel} {colour} on {bg_sel} {bg} = {ratio:.2f}:1"
