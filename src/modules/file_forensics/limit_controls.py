"""Search result budget controls and approximate retained row memory."""
import sys
from dataclasses import fields, is_dataclass
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSlider, QSpinBox

LIMIT_KEY = 'modules.file_forensics.result_limit'
LIMIT_STEPS = (1000, 5000, 10000, 50000, 100000, 500000, None)


def valid_limit(value):
    if value is None or (type(value) is int and value == 0):
        return None
    return value if type(value) is int and 100 <= value <= 10000000 else 5000


def limit_text(value):
    return 'Unlimited' if value is None else f'{value:,}'


def row_bytes(row):
    """Measure owned Python data once per sample; exclude global class objects."""
    seen = set()
    def size(value):
        if id(value) in seen:
            return 0
        seen.add(id(value))
        total = sys.getsizeof(value)
        if is_dataclass(value):
            total += sys.getsizeof(vars(value))
            total += sum(size(getattr(value, field.name)) for field in fields(value))
        elif isinstance(value, (tuple, list)):
            total += sum(size(item) for item in value)
        return total
    return size(row) + 160  # list references, path index and proxy row mapping


class LimitControls:
    def _build_limit_controls(self, bar):
        config = getattr(getattr(self, 'app', None), 'config', None)
        self._result_limit = valid_limit(config.get(LIMIT_KEY, 5000) if config else 5000)
        self._search_limit = self._result_limit
        self._bytes_per_row = 0
        self._memory_sample = []
        self._limit_slider = QSlider(Qt.Orientation.Horizontal)
        self._limit_slider.setRange(0, len(LIMIT_STEPS) - 1)
        self._limit_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._limit_slider.setTickInterval(1)
        self._limit_slider.setToolTip('Choose 1,000 / 5,000 / 10,000 / 50,000 / 100,000 / 500,000 / Unlimited matches. More rows use more memory and time. Stop always works.')
        self._limit_spin = QSpinBox()
        self._limit_spin.setRange(100, 10000000)
        self._limit_spin.setGroupSeparatorShown(True)
        self._limit_spin.setKeyboardTracking(False)
        self._limit_spin.setToolTip('Enter an exact maximum from 100 to 10,000,000 files. The slider shows the nearest step while keeping your exact number. More rows use more memory and time. Stop always works.')
        self._limit_state = QLabel('Unlimited')
        self._limit_state.setToolTip('Unlimited keeps every matching file until the walk ends. Large searches can use several GB of memory and take minutes. Stop always works.')
        line = QHBoxLayout()
        for widget in (self._limit_slider, self._limit_spin, self._limit_state):
            line.addWidget(widget)
        bar.addWidget(QLabel('Show up to'), 6, 0)
        bar.addLayout(line, 6, 1, 1, 3)
        self._sync_limit_controls()
        self._limit_slider.valueChanged.connect(self._slider_limit_changed)
        self._limit_spin.valueChanged.connect(self._spin_limit_changed)

    def _sync_limit_controls(self):
        value = self._result_limit
        index = len(LIMIT_STEPS) - 1 if value is None else min(
            range(len(LIMIT_STEPS) - 1), key=lambda i: abs(LIMIT_STEPS[i] - value))
        self._limit_slider.blockSignals(True)
        self._limit_spin.blockSignals(True)
        self._limit_slider.setValue(index)
        self._limit_spin.setValue(value if value is not None else 500000)
        self._limit_spin.setEnabled(value is not None)
        self._limit_state.setVisible(value is None)
        self._limit_slider.blockSignals(False)
        self._limit_spin.blockSignals(False)

    def _set_result_limit(self, value):
        self._result_limit = value
        self._sync_limit_controls()
        config = getattr(getattr(self, 'app', None), 'config', None)
        if config:
            config.set(LIMIT_KEY, value)

    def _slider_limit_changed(self, index):
        self._set_result_limit(LIMIT_STEPS[index])

    def _spin_limit_changed(self, value):
        self._set_result_limit(value)

    def _sample_memory(self, rows):
        self._memory_sample.extend(row_bytes(row) for row in rows[:32 - len(self._memory_sample)])
        if self._memory_sample:
            self._bytes_per_row = sum(self._memory_sample) // len(self._memory_sample)

    def _memory_status(self, count):
        if self._search_limit is None or self._search_limit > 100000:
            from core.formatting import format_size
            return f'; estimated row memory {format_size(count * self._bytes_per_row)} (approximate, from a measured sample; excludes scan/handle workspace)'
        return ''
