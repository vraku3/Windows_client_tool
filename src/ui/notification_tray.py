import logging
from typing import List

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from core.semantic_colors import semantic

logger = logging.getLogger(__name__)


class NotificationItem:
    def __init__(self, title: str, message: str, level: str = "info"):
        self.title = title
        self.message = message
        self.level = level  # info, warning, error


class NotificationTray(QWidget):
    """In-app notification area showing recent alerts."""

    MAX_VISIBLE = 50

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._notifications: List[NotificationItem] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QHBoxLayout()
        header.addWidget(QLabel("Notifications"))
        header.addStretch()
        self._clear_btn = QPushButton("Clear All")
        self._clear_btn.clicked.connect(self.clear_all)
        header.addWidget(self._clear_btn)
        layout.addLayout(header)

        # Scroll area for notifications
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_content = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._scroll_content)
        layout.addWidget(self._scroll)

    def add_notification(self, item: NotificationItem) -> None:
        self._notifications.insert(0, item)
        if len(self._notifications) > self.MAX_VISIBLE:
            self._notifications = self._notifications[: self.MAX_VISIBLE]
        self._render_item(item)

    def _render_item(self, item: NotificationItem) -> None:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        colors = {"info": semantic("info"), "warning": semantic("warning"),
                 "error": semantic("error")}
        frame.setStyleSheet(
            f"background-color: {colors.get(item.level, colors['info'])}; "
            f"border-radius: 4px; padding: 4px; margin: 2px;"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 4, 8, 4)
        title = QLabel(f"<b>{item.title}</b>")
        title.setStyleSheet("color: white;")
        layout.addWidget(title)
        msg = QLabel(item.message)
        msg.setWordWrap(True)
        layout.addWidget(msg)
        self._scroll_layout.insertWidget(0, frame)

    def clear_all(self) -> None:
        self._notifications.clear()
        while self._scroll_layout.count():
            child = self._scroll_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()


class SystemTrayManager:
    """Manages the system tray icon with context menu and balloon notifications."""

    def __init__(self, main_window, icon: QIcon = None):
        self._window = main_window
        self._tray = QSystemTrayIcon(icon or QIcon(), main_window)
        self._setup_menu()
        self._unread_count = 0

    def _setup_menu(self):
        menu = QMenu()
        show_action = QAction("Show / Hide", self._window)
        show_action.triggered.connect(self._toggle_window)
        menu.addAction(show_action)
        menu.addSeparator()
        exit_action = QAction("Exit", self._window)
        exit_action.triggered.connect(self._force_quit)
        menu.addAction(exit_action)
        self._tray.setContextMenu(menu)

    def show(self) -> None:
        self._tray.show()

    def connect_activated(self, slot) -> None:
        """Connect the tray icon's activated signal to an external slot."""
        self._tray.activated.connect(slot)

    def _toggle_window(self):
        if self._window.isVisible():
            self._window.hide()
        else:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()

    def _force_quit(self):
        """Quit regardless of minimize-to-tray setting."""
        self._tray.hide()
        # Trigger window's normal close path (saves config, stops modules)
        if self._window:
            self._window._app.config.set(
                "app.minimize_to_tray", False
            )
            self._window.close()

    def show_balloon(self, title: str, message: str, icon_type=None) -> None:
        if icon_type is None:
            icon_type = QSystemTrayIcon.MessageIcon.Information
        self._tray.showMessage(title, message, icon_type, 5000)

    def set_unread_count(self, count: int) -> None:
        self._unread_count = count
        tooltip = f"Windows Tweaker — {count} unread" if count else "Windows Tweaker"
        self._tray.setToolTip(tooltip)
