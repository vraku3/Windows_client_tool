"""Small themed, construction-only widgets for the navigation pane editor."""
import logging
import os

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from core.semantic_colors import chrome, semantic
from core.table_ui import set_role
from .engine.model import Health

logger = logging.getLogger(__name__)


class PathLabel(QLabel):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setMinimumWidth(80)
        self.setToolTip(path or "No filesystem target was reported for this shell entry.")
        set_role(self, "muted")

    def resizeEvent(self, event):
        self.setText(self.fontMetrics().elidedText(self.path, Qt.TextElideMode.ElideMiddle, self.width()))
        super().resizeEvent(event)


class EntryCard(QFrame):
    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setToolTip("Select this entry to inspect its folder, appearance and available actions.")
        layout = QVBoxLayout(self)
        title = QLabel("📁  " + entry.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        title.setToolTip(entry.name)
        layout.addWidget(title)
        layout.addWidget(PathLabel(entry.resolved_target or entry.target, self))
        chip = QLabel(entry.health.value)
        chip.setToolTip(entry.detail or "The registry entry and folder were checked during the last refresh.")
        role = "statusSuccess" if entry.health == Health.OK else "statusWarning"
        set_role(chip, "statusError" if entry.health == Health.UNREADABLE else role)
        layout.addWidget(chip)
        badge = QLabel(entry.kind.value)
        badge.setToolTip("Made here can be undone. Outside entries are backed up before changes. Windows entries are read-only.")
        set_role(badge, "muted")
        layout.addWidget(badge)


def preview_rows(name, order, show_all, entries=(), selected_guid=None):
    rows = [(64, "Home", False), (65, "Gallery", False), (66, "OneDrive", False)]
    rows.extend((e.sort_order, e.name, False) for e in entries
                if e.guid != selected_guid and e.target)
    rows.append((order, name or "Your folder", True))
    result = [(label, 1 if show_all else 0, selected) for _, label, selected in sorted(rows, key=lambda r: r[0])]
    return [("Desktop", 0, False), *result] if show_all else result


class Preview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = preview_rows("Your folder", 48, False)
        self.setMinimumHeight(185)
        self.setToolTip("A preview of the entry's order. The Desktop root appears only when Show all folders is enabled.")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(chrome("surface")))
        for index, (name, depth, selected) in enumerate(self.rows[:9]):
            y = 12 + index * 23
            if selected:
                painter.fillRect(5, y - 3, self.width() - 10, 23, QColor(chrome("surface_selected")))
            painter.setPen(QColor(semantic("info") if selected else chrome("text")))
            text = self.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, max(20, self.width() - 55))
            painter.drawText(14 + depth * 20, y + 13, "▸  " + text)

    def sizeHint(self):
        return QSize(340, 190)


def extract_icon(location):
    """QImage is safe on a worker; QPixmap is created only by the UI receiver."""
    import win32gui
    path, _, index = location.rpartition(",")
    large, small = win32gui.ExtractIconEx(os.path.expandvars(path), int(index), 1)
    handles = large + small
    try:
        if not handles:
            raise OSError(f"No icon found in {location}")
        return QImage.fromHICON(handles[0]).copy()
    finally:
        for handle in handles:
            win32gui.DestroyIcon(handle)


def icon_from_image(image):
    return QIcon(QPixmap.fromImage(image))
