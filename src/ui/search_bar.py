from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)


class SearchBar(QWidget):
    """Global search bar with regex toggle and filter expand button."""

    search_requested = pyqtSignal(str, bool)  # text, regex_enabled
    filter_toggled = pyqtSignal(bool)  # expanded

    DEBOUNCE_MS = 300

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._filter_expanded = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Search all logs, events, recommendations...")
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._emit_search)
        layout.addWidget(self._input, stretch=1)

        self._search_btn = QPushButton("Search")
        self._search_btn.clicked.connect(self._emit_search)
        layout.addWidget(self._search_btn)

        self._regex_cb = QCheckBox("Regex")
        layout.addWidget(self._regex_cb)

        self._filter_btn = QPushButton("Filters")
        self._filter_btn.setCheckable(True)
        self._filter_btn.toggled.connect(self._on_filter_toggled)
        layout.addWidget(self._filter_btn)

        # Debounce timer
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_search)

    def _on_text_changed(self, text: str):
        self._debounce.start()

    def _emit_search(self):
        self.search_requested.emit(self._input.text(), self._regex_cb.isChecked())

    def _on_filter_toggled(self, checked: bool):
        self._filter_expanded = checked
        self.filter_toggled.emit(checked)

    def text(self) -> str:
        return self._input.text()

    def is_regex(self) -> bool:
        return self._regex_cb.isChecked()

    def focus_search(self):
        self._input.setFocus()
        self._input.selectAll()

    def focus_search_with_filters(self):
        self.focus_search()
        if not self._filter_expanded:
            self._filter_btn.setChecked(True)

    def clear(self):
        self._input.clear()
        if self._filter_expanded:
            self._filter_btn.setChecked(False)
