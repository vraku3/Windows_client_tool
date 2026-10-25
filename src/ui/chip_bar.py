"""Counted, keyboard-accessible filter pills."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QPushButton, QButtonGroup, QHBoxLayout, QWidget, QSizePolicy


class FilterChip(QPushButton):
    def __init__(self, label, count=0, parent=None, tooltip=""):
        super().__init__(parent)
        self.label = label
        self.setObjectName("chip")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(tooltip or f"Show results matching {label}. The number is the current matching count.")
        self.set_count(count)

    def set_count(self, count):
        self.count = max(0, int(count))
        self.setText(f"{self.label}  {self.count:,}")
        self.setProperty("zero", self.count == 0)
        self.setAccessibleName(f"{self.label}, {self.count} results")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class ChipBar(QWidget):
    selection_changed = pyqtSignal(list)

    def __init__(self, chips=(), *, exclusive=True, parent=None):
        super().__init__(parent)
        self.setToolTip("Filter results by category. Use Tab and Space to select a filter.")
        self.chips = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(exclusive)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for key, label, count in chips:
            self.add_chip(key, label, count)
        if exclusive and self.chips:
            next(iter(self.chips.values())).setChecked(True)

    def add_chip(self, key, label, count=0, tooltip=""):
        chip = FilterChip(label, count, self, tooltip)
        self.chips[key] = chip
        self.group.addButton(chip)
        self._layout.addWidget(chip)
        chip.toggled.connect(self._changed)
        return chip

    def _changed(self, _checked):
        self.selection_changed.emit(self.selected())

    def selected(self):
        return [key for key, chip in self.chips.items() if chip.isChecked()]

    def set_counts(self, counts):
        for key, count in counts.items():
            if key in self.chips:
                self.chips[key].set_count(count)
