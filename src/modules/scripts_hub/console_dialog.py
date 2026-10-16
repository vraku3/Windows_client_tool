"""The "Console colors" dialog: a preset, per-role colour overrides, font and
size, previewed live on the real output pane."""
from __future__ import annotations

from typing import Callable, Dict, Optional

from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QPushButton, QVBoxLayout, QWidget)

from core.semantic_colors import CONSOLE_PRESETS

from . import console_style as cs


def _swatch(color: str) -> QIcon:
    pix = QPixmap(28, 14)
    pix.fill(QColor(color) if color else QColor(0, 0, 0, 0))
    return QIcon(pix)


class ConsoleDialog(QDialog):
    def __init__(self, style: cs.ConsoleStyle, preview: Callable[[cs.ConsoleStyle], None],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Console colors")
        self._original = cs.ConsoleStyle(style.preset, dict(style.overrides), style.font, style.size)
        self.style = cs.ConsoleStyle(style.preset, dict(style.overrides), style.font, style.size)
        self._preview = preview
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self._preset = QComboBox(self)
        self._preset.addItems(list(CONSOLE_PRESETS))
        self._preset.setCurrentText(self.style.preset)
        self._preset.currentTextChanged.connect(self._preset_changed)
        form.addRow("Scheme:", self._preset)
        self._font = QComboBox(self)
        for key, (label, _family) in cs.FONTS.items():
            self._font.addItem(label, key)
        self._font.setCurrentIndex(list(cs.FONTS).index(self.style.font))
        self._font.currentIndexChanged.connect(self._font_changed)
        form.addRow("Font:", self._font)
        self._size = QComboBox(self)
        for size in cs.SIZES:
            self._size.addItem(f"{size} pt", size)
        self._size.setCurrentIndex(cs.SIZES.index(self.style.size))
        self._size.currentIndexChanged.connect(self._size_changed)
        form.addRow("Size:", self._size)
        self._buttons: Dict[str, QPushButton] = {}
        for role in cs.ROLES:
            row = QHBoxLayout()
            b = QPushButton(self)
            b.clicked.connect(lambda _=False, r=role: self._pick(r))
            row.addWidget(b, 1)
            if role.endswith("_back"):
                off = QPushButton("None", self)
                off.clicked.connect(lambda _=False, r=role: self._set(r, ""))
                row.addWidget(off)
            self._buttons[role] = b
            form.addRow(cs.ROLE_LABELS[role] + ":", row)
        lay.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel
                               | QDialogButtonBox.StandardButton.RestoreDefaults, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._defaults)
        lay.addWidget(box)
        self._refresh()

    def _refresh(self) -> None:
        for role, b in self._buttons.items():
            color = self.style.color(role)
            b.setIcon(_swatch(color))
            b.setText(f"{color or 'none'}{'  (changed)' if role in self.style.overrides else ''}")
        self._preview(self.style)

    def _preset_changed(self, name: str) -> None:
        self.style.preset = name
        self.style.overrides.clear()          # a new scheme starts clean
        self._refresh()

    def _font_changed(self, _index: int) -> None:
        self.style.font = self._font.currentData()
        self._refresh()

    def _size_changed(self, _index: int) -> None:
        self.style.size = self._size.currentData()
        self._refresh()

    def _pick(self, role: str) -> None:
        start = QColor(self.style.color(role)) if self.style.color(role) else QColor(0, 0, 0)
        chosen = QColorDialog.getColor(start, self, f"{cs.ROLE_LABELS[role]} colour")
        if chosen.isValid():
            self._set(role, chosen.name())

    def _set(self, role: str, value: str) -> None:
        self.style.overrides[role] = value
        self._refresh()

    def _defaults(self) -> None:
        self.style = cs.ConsoleStyle()
        for combo, value in ((self._preset, self.style.preset),):
            combo.blockSignals(True)
            combo.setCurrentText(value)
            combo.blockSignals(False)
        self._font.setCurrentIndex(list(cs.FONTS).index(self.style.font))
        self._size.setCurrentIndex(cs.SIZES.index(self.style.size))
        self._refresh()

    def reject(self) -> None:
        self._preview(self._original)            # undo the live preview
        super().reject()
