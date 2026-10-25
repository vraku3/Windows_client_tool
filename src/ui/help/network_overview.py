"""Shared presentation pieces for the network panes; no system reads here."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

from ui.glance import GlanceStrip, GlanceTile


def add_glance(layout, specs):
    """Add measured tiles and return them by their pane-local key."""
    tiles = {key: GlanceTile(caption, severity=role, tooltip=tip)
             for key, caption, role, tip in specs}
    for tile in tiles.values():
        tile.set_unreadable("Not read yet")
        tile.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tile.setCursor(Qt.CursorShape.ArrowCursor)
    strip = GlanceStrip(tiles.values())
    strip.setToolTip('Measurements from this pane. Hover over a value for its meaning and any read limitation.')
    layout.addWidget(strip)
    return tiles


def unreadable(tiles, reason):
    for tile in tiles.values():
        tile.set_unreadable(reason)


def feature_card(title, detail, *, status="", role="statusInfo", tooltip=""):
    """A compact, selectable-text card for a small set of feature facts."""
    card = QFrame()
    card.setObjectName("glanceTile")
    card.setToolTip(tooltip or detail)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(8)
    for text, name in ((title, "heading"), (status, role), (detail, "")):
        if not text:
            continue
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setToolTip(tooltip or detail)
        label.setObjectName(name)
        layout.addWidget(label)
    return card
