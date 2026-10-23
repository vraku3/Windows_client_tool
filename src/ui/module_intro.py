"""The plain-language "what is this?" paragraph at the top of a pane.

One helper, so every pane's introduction looks and behaves the same, and so
`ui.tooltip_audit` can find it by its objectName (`moduleIntro`). The look
comes from the `QLabel#moduleIntro` rule in dark.qss / light.qss, never an
inline sheet, so it follows the theme.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

INTRO_NAME = "moduleIntro"


def module_intro(text: str, parent=None) -> QLabel:
    """A word-wrapped, selectable label explaining the pane to a newcomer.

    Write it for someone who has never opened the tool: what the pane shows,
    what the buttons can change on the PC, and whether that can be undone.
    Two or three sentences; the details belong in the tooltips.
    """
    label = QLabel(text, parent)
    label.setObjectName(INTRO_NAME)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label
