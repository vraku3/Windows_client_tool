"""A category's name must be readable in whichever theme is in force.

This test once guarded `_SliceCard`, the legend card beside the old donut,
whose label colour was written into its markup -- `<span style='color:#e0e0e0'>`
-- where no stylesheet can reach, so it stayed a dark-theme grey forever.
The overview (WP6) replaced those cards with painted category rows
(`category_group._CategoryHeader`) and a legend the donut draws itself; both
read their colours at paint time. The rule is the same and is still measured
on the rendered widget, because the point is what reaches the screen.
"""
import os
import sys

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QFrame, QVBoxLayout

QApplication.instance() or QApplication(sys.argv)

from core import semantic_colors as sc  # noqa: E402
from modules.cleanup import cleanup_scanner as cs  # noqa: E402
from modules.cleanup.components.category_group import CategoryGroup, row_columns  # noqa: E402


def _luminance(colour: QColor) -> float:
    def channel(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(x) for x in (colour.red(), colour.green(), colour.blue()))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: QColor, b: QColor) -> float:
    low, high = sorted((_luminance(a), _luminance(b)))
    return (high + 0.05) / (low + 0.05)


def _render_name_region(theme: str):
    """Just the category NAME's column of the row, on a card-coloured surface."""
    qapp = QApplication.instance()
    qss = open(os.path.join(os.path.dirname(__file__), "..", "src", "ui", "styles",
                            f"{theme}.qss"), encoding="utf-8").read()
    previous = qapp.styleSheet()
    sc.set_theme(theme)
    try:
        # Hosted the way the overview hosts it: on a chartCard, with the
        # rows' own surfaces transparent. The theme goes on the CARD, not the
        # application: app.setStyleSheet repolishes every widget earlier tests
        # left alive and crashed the suite intermittently (2026-10-21).
        card = QFrame()
        card.setObjectName("chartCard")
        card.setStyleSheet(qss + "\nQWidget#categoryGroup, QWidget#categoryRow "
                           "{ background: transparent; }")
        lay = QVBoxLayout(card)
        group = CategoryGroup("Temp Files", cs.scan_temp_files)
        group.set_result(cs.ScanResult(items=[cs.ScanItem(path=r"C:\t", size=10, is_dir=True)],
                                       total_size=10))
        lay.addWidget(group)
        card.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        card.resize(900, 90)
        card.show()
        qapp.processEvents()
        header = group._header
        name = row_columns(header.rect())["name"]
        origin = header.mapTo(card, header.rect().topLeft())
        image = card.grab().toImage().copy(origin.x() + name.x(), origin.y() + 4, 120, 22)
    finally:
        assert qapp.styleSheet() == previous
        sc.set_theme("dark")
    return image


def _background_and_darkest_ink(image):
    from collections import Counter
    counts = Counter()
    for y in range(image.height()):
        for x in range(image.width()):
            counts[image.pixel(x, y)] += 1
    background = QColor(counts.most_common(1)[0][0])
    ink = max((QColor(p) for p in counts),
              key=lambda c: abs(_luminance(c) - _luminance(background)))
    return background, ink


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_category_name_is_readable_on_its_card(theme):
    background, ink = _background_and_darkest_ink(_render_name_region(theme))
    ratio = _contrast(background, ink)
    assert ratio >= 4.5, (
        f"{theme}: the category name reaches only {ratio:.2f}:1 against its "
        f"background ({ink.name()} on {background.name()})")
