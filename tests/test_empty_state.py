"""A pane with nothing to show yet should say so where the eye is.

Disk Health rendered a one-line grey hint in the toolbar and then ~750px of
nothing. The information was present and unfindable. An empty state belongs in
the space it is explaining, not tucked into a corner of it.
"""
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

QApplication.instance() or QApplication(sys.argv)

from ui.empty_state import EmptyState  # noqa: E402


def test_it_shows_the_title_and_the_hint():
    state = EmptyState("drive", "Nothing scanned yet", "Press Scan to begin.")
    assert "Nothing scanned yet" in state.title
    assert "Press Scan to begin." in state.hint


def test_the_message_is_centred_in_the_space_it_explains():
    state = EmptyState("drive", "Nothing scanned yet", "Press Scan to begin.")
    assert state.layout().alignment() & Qt.AlignmentFlag.AlignCenter


def test_an_action_button_appears_only_when_one_is_wanted():
    assert EmptyState("drive", "t", "h").action_button is None
    with_action = EmptyState("drive", "t", "h", action_text="Scan Drives")
    assert with_action.action_button is not None
    assert with_action.action_button.text() == "Scan Drives"


def test_the_action_button_reports_being_pressed():
    state = EmptyState("drive", "t", "h", action_text="Scan Drives")
    pressed = []
    state.action_triggered.connect(lambda: pressed.append(True))
    state.action_button.click()
    assert pressed == [True]


def test_it_sets_no_colour_of_its_own():
    """Whatever it paints must come from the theme -- see light.qss."""
    state = EmptyState("drive", "t", "h")
    assert "color:" not in state.styleSheet()
