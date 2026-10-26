"""Centred empty, loading and failed-read states with an optional next action."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QPainter, QPalette
from PyQt6.QtWidgets import QLabel, QPushButton, QProgressBar, QVBoxLayout, QWidget, QStyle


# Windows symbol fonts share these MDL2 code points. Names keep callers
# independent of font selection and avoid emoji/letter placeholders.
ICONS = {
    "drive": (0xEDA2, "SP_DriveHDIcon"), "record": (0xE7C8, "SP_MediaPlay"),
    "power": (0xE7E8, "SP_ComputerIcon"), "search": (0xE721, "SP_FileDialogContentsView"),
    "registry": (0xE8A5, "SP_DirIcon"), "log": (0xE8A5, "SP_FileIcon"),
    "check": (0xE73E, "SP_DialogApplyButton"), "warning": (0xE7BA, "SP_MessageBoxWarning"),
    "error": (0xEA39, "SP_MessageBoxCritical"), "shield": (0xEA18, "SP_VistaShield"),
    "network": (0xE968, "SP_DriveNetIcon"), "folder": (0xE8B7, "SP_DirIcon"),
    "refresh": (0xE72C, "SP_BrowserReload"), "temperature": (0xE9CA, "SP_ComputerIcon"),
}


class _IconLabel(QLabel):
    """Paint with the symbol font even when the app QSS sets a text font."""
    symbol_font = None

    def paintEvent(self, event):
        if self.symbol_font is None:
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setFont(self.symbol_font)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()


def set_state_icon(label, name):
    codepoint, standard = ICONS[name]
    families = QFontDatabase.families()
    family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families), None)
    if family:
        label.symbol_font = QFont(family, 32)
        label.setFont(label.symbol_font)
        label.setMinimumSize(56, 56)
        label.setText(chr(codepoint))
    else:
        label.setPixmap(label.style().standardIcon(getattr(QStyle.StandardPixmap, standard)).pixmap(48, 48))
    label.setToolTip(name.replace("_", " ").capitalize())


class EmptyState(QWidget):
    action_triggered = pyqtSignal()

    def __init__(self, icon, title, hint, action_text="", parent=None,
                 *, variant="empty", reason="", last_checked=""):
        super().__init__(parent)
        self.title, self.hint = title, hint
        self.setObjectName("emptyState")
        self.setToolTip(reason or hint)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)
        self.glyph_label = self._label("", "emptyGlyph", layout)
        self.icon_name = icon
        set_state_icon(self.glyph_label, icon)
        self.title_label = self._label(title, "emptyTitle", layout)
        self.hint_label = self._label(hint, "emptyHint", layout)
        self.hint_label.setWordWrap(True)
        self.hint_label.setMaximumWidth(460)
        self.hint_label.setMinimumHeight(self.hint_label.fontMetrics().height() * 3)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(220)
        self.progress.setToolTip("Waiting for this pane's current read to finish.")
        layout.addWidget(self.progress, alignment=Qt.AlignmentFlag.AlignCenter)
        self.last_checked_label = self._label(last_checked, "emptyHint", layout)
        self.last_checked_label.setVisible(bool(last_checked))
        self.action_button = None
        if action_text:
            self.action_button = QPushButton(action_text)
            self.action_button.setObjectName("accentButton")
            self.action_button.setToolTip(f"{action_text}: {hint}")
            self.action_button.clicked.connect(self.action_triggered.emit)
            layout.addWidget(self.action_button, alignment=Qt.AlignmentFlag.AlignCenter)
        self.set_variant(variant, reason=reason)

    def _label(self, text, role, layout):
        label = _IconLabel(text) if role == "emptyGlyph" else QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName(role)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setToolTip(text or self.hint)
        layout.addWidget(label, alignment=Qt.AlignmentFlag.AlignCenter)
        return label

    def set_variant(self, variant, *, reason="", progress_text=""):
        if variant not in ("empty", "loading", "error"):
            raise ValueError(f"Unknown empty-state variant: {variant}")
        self.variant = variant
        self.progress.setVisible(variant == "loading")
        self.progress.setRange(0, 0 if variant == "loading" else 1)
        self.setToolTip(reason or progress_text or self.hint)
        self.hint_label.setText(reason or progress_text or self.hint)
        self.hint_label.setToolTip(reason or progress_text or self.hint)
        self.title_label.setObjectName("statusError" if variant == "error" else "emptyTitle")
        self.title_label.style().unpolish(self.title_label)
        self.title_label.style().polish(self.title_label)

    def set_last_checked(self, text):
        self.last_checked_label.setText(text)
        self.last_checked_label.setToolTip(text)
        self.last_checked_label.setVisible(bool(text))
