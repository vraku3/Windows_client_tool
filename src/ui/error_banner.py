from PyQt6.QtWidgets import QLabel, QPushButton, QHBoxLayout, QWidget
from PyQt6.QtCore import pyqtSignal
from core.table_ui import set_role


class ErrorBanner(QWidget):
    """Dismissible error banner widget for module-level error display."""
    dismissed = pyqtSignal()

    def __init__(self, message: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("errorBanner")
        self.setMaximumHeight(40)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self._label = QLabel(f"\u26a0 {message}" if message else "")
        self._label.setObjectName("errorBannerLabel")
        set_role(self._label, "statusError")
        layout.addWidget(self._label)
        layout.addStretch()
        close_btn = QPushButton("\u00d7")
        close_btn.setObjectName("errorBannerClose")
        close_btn.clicked.connect(self.hide)
        close_btn.clicked.connect(self.dismissed.emit)
        layout.addWidget(close_btn)
        self.hide()

    def set_error(self, message: str) -> None:
        """Show the banner with the given error message."""
        self._label.setText(f"\u26a0 {message}")
        self.show()

    def clear(self) -> None:
        """Hide the banner without emitting dismissed."""
        self.hide()

    def text(self) -> str:
        """The message currently shown, without the warning glyph."""
        return self._label.text().lstrip("⚠ ").strip()
