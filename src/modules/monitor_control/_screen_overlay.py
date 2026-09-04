r"""A borderless panel drawn on one physical screen.

Two things need this and they are the same widget: **Identify**, which puts a
big number on each screen so you can tell three 2560x1440 panels apart, and
the **revert countdown**, which has to appear on the display a change just
affected rather than on whichever one happens to hold the main window.

The countdown case is the reason this exists at all. A modal dialog on the
primary monitor is no use when the display you just broke is a different one:
you are looking at a black screen and the button that would save you is
somewhere you cannot see.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QScreen
from PyQt6.QtWidgets import QApplication, QWidget

from core.semantic_colors import chrome

logger = logging.getLogger(__name__)


def _translucent(hex_colour: str, alpha: int) -> QColor:
    """The overlay panel, dark enough to read against anything."""
    colour = QColor(hex_colour)
    colour.setAlpha(alpha)
    return colour


class ScreenOverlay(QWidget):
    """A frameless, always-on-top panel filling one screen.

    Click-through by default (`WindowTransparentForInput`) so Identify cannot
    swallow a click the user meant for something else. The countdown turns
    that off, because its whole job is to be clickable.
    """

    def __init__(self, screen: QScreen, *, interactive: bool = False,
                 parent=None):
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool)
        super().__init__(parent, flags)
        self._screen = screen
        self._headline = ""
        self._subtitle = ""

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if not interactive:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.setGeometry(screen.geometry())

    @property
    def screen_name(self) -> str:
        return self._screen.name()

    def show_text(self, headline: str, subtitle: str = "") -> None:
        self._headline = headline
        self._subtitle = subtitle
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        box_w = min(rect.width() // 3, 520)
        box_h = min(rect.height() // 3, 360)
        box = rect.adjusted((rect.width() - box_w) // 2,
                            (rect.height() - box_h) // 2,
                            -(rect.width() - box_w) // 2,
                            -(rect.height() - box_h) // 2)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_translucent(chrome('overlay_surface'), 225))
        painter.drawRoundedRect(box, 24, 24)

        painter.setPen(QColor(chrome("overlay_text")))
        font = QFont(self.font())
        font.setPointSize(max(48, box_h // 4))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self._headline)

        if self._subtitle:
            font.setPointSize(max(11, box_h // 20))
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor(chrome("overlay_text_muted")))
            footer = box.adjusted(16, box.height() - box_h // 4, -16, -20)
            painter.drawText(footer, Qt.AlignmentFlag.AlignHCenter
                             | Qt.AlignmentFlag.AlignBottom, self._subtitle)


class IdentifyOverlays:
    """The Identify action: a number on every screen, for a few seconds.

    Holds its overlays for their whole lifetime. A `QWidget` with no parent
    that nothing references is collected while still on screen, which is how
    this kind of thing ends up flickering once and vanishing.
    """

    def __init__(self, duration_ms: int = 3000):
        self._duration_ms = duration_ms
        self._overlays: List[ScreenOverlay] = []
        self._timer: Optional[QTimer] = None

    def show(self, labels: Optional[dict] = None) -> List[ScreenOverlay]:
        """Number every screen. `labels` maps a QScreen name to a subtitle."""
        self.hide()
        labels = labels or {}
        for index, screen in enumerate(QApplication.screens(), start=1):
            overlay = ScreenOverlay(screen)
            overlay.show_text(str(index), labels.get(screen.name(), screen.name()))
            overlay.show()
            self._overlays.append(overlay)

        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._timer.start(self._duration_ms)
        return list(self._overlays)

    def hide(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        for overlay in self._overlays:
            overlay.close()
            overlay.deleteLater()
        self._overlays.clear()


def screen_at(position) -> Optional[QScreen]:
    """The QScreen whose geometry contains this desktop point, or None.

    The bridge from the engine's world (desktop coordinates, from
    `display_config`) to Qt's (QScreen objects). Used to put the countdown on
    the display a change actually touched.
    """
    if position is None:
        return None
    x, y = position
    for screen in QApplication.screens():
        if screen.geometry().contains(int(x), int(y)):
            return screen
    return None


def screen_still_present(screen: Optional[QScreen]) -> bool:
    """True if this screen is still attached.

    After a topology change the QScreen a dialog was going to use may be
    gone. Asking Qt for the live list is the only honest way to know — a
    stored pointer says nothing about whether the monitor is still there.
    """
    if screen is None:
        return False
    try:
        return screen in QApplication.screens()
    except RuntimeError:
        # The QScreen was destroyed with its monitor.
        return False
