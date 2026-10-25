"""Centred empty, loading and failed-read states with an optional next action."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QProgressBar, QVBoxLayout, QWidget


class EmptyState(QWidget):
    action_triggered = pyqtSignal()

    def __init__(self, glyph, title, hint, action_text="", parent=None,
                 *, variant="empty", reason="", last_checked=""):
        super().__init__(parent)
        self.title, self.hint = title, hint
        self.setObjectName("emptyState")
        self.setToolTip(reason or hint)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)
        self.glyph_label = self._label(glyph, "emptyGlyph", layout)
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
        label = QLabel(text)
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
