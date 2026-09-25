"""Audio outputs: arrange which sound card is Ctrl+1, Ctrl+2, ... and switch."""
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QVBoxLayout)

from core import audio_switch
from core.table_ui import set_role

logger = logging.getLogger(__name__)

CONFIG_ORDER = "audio.output_order"


class AudioOutputsDialog(QDialog):
    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self.setWindowTitle("Audio outputs")
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Ctrl+1 selects the first output below, Ctrl+2 the second, and so on up to "
            "Ctrl+9; Ctrl+0 is the tenth. Arrange the list to choose which is which. "
            "Only devices that are currently active are listed.", self)
        intro.setWordWrap(True)
        set_role(intro, "muted")
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.list = QListWidget(self)
        self.list.itemDoubleClicked.connect(lambda _item: self._use_selected())
        row.addWidget(self.list, 1)
        buttons = QVBoxLayout()
        for text, slot in (("Move up", lambda: self._move(-1)), ("Move down", lambda: self._move(1)),
                           ("Use this output", self._use_selected), ("Refresh", self.reload)):
            button = QPushButton(text, self)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        layout.addLayout(row, 1)

        self.status = QLabel("", self)
        layout.addWidget(self.status)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self.reload()

    # ---- order ------------------------------------------------------------------

    def _saved_order(self):
        config = getattr(self._app, "config", None)
        value = config.get(CONFIG_ORDER, []) if config is not None else []
        return value if isinstance(value, list) else []

    def reload(self) -> None:
        outputs = audio_switch.list_outputs(self._saved_order())
        self.list.clear()
        if outputs is None:
            self.status.setText("Windows would not list the sound outputs.")
            return
        for o in outputs:
            item = QListWidgetItem(f"{o.key_name}    {o.label}" + ("    (default)" if o.is_default else ""))
            item.setData(Qt.ItemDataRole.UserRole, o.endpoint_id)
            self.list.addItem(item)
        if not outputs:
            self.status.setText("No active sound outputs were found.")
        elif self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

    def _current_ids(self):
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        ids = self._current_ids()
        target = row + delta
        if row < 0 or not 0 <= target < len(ids):
            return
        ids[row], ids[target] = ids[target], ids[row]
        self._save_order(ids)
        self.reload()
        self.list.setCurrentRow(target)

    def _save_order(self, ids) -> None:
        config = getattr(self._app, "config", None)
        if config is None:
            return
        config.set(CONFIG_ORDER, list(ids))
        try:
            config.save()
        except OSError as e:
            logger.warning("could not save the audio output order: %s", e)
            self.status.setText(f"The order could not be saved: {e}")

    def _use_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        number = self.list.currentRow() + 1
        result = audio_switch.switch_to_number(number, self._saved_order())
        self.status.setText(result.message)
        self.reload()
