"""Picking the two colours painted inside a message.

Deliberately small: two swatches, a live preview and a Reset. The highlight
rules editor next door is a table because it holds a list the user builds;
this holds exactly the two colours the delegate paints, so a table would be
ceremony.

The preview is the point of the dialog. A hex swatch does not answer "can I
read that on a log row" -- a real message with a real match coloured in it
does, and it shows in the same breath that a match is a COLOUR and not a
block behind the text.
"""

from modules.log_viewer.help_widgets import (
    describe_control, explain_widget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QColorDialog, QDialog, QDialogButtonBox,
                             QGridLayout, QLabel, QPushButton, QVBoxLayout)

from .log_delegate import LogMessageDelegate
from .match_colours import LABELS, MEANINGS, _clean, default_colour
from .palette import is_valid_hex_colour, readable_text_on

#: A line with both painted things in it, so the preview answers for both at
#: once. Real shapes: this is what a CBS failure actually looks like.
_SAMPLE = "Failed to open package alpha [HRESULT = 0x800f0805]"
_SAMPLE_NEEDLE = "package"


class MatchColourDialog(QDialog):
    def __init__(self, colours, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Message colours")
        self._chosen = _clean(colours)
        self._swatches = {}
        self._labels = {}

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "These colour the text itself inside a message. The row keeps "
            "its own severity colour.", self)
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        grid = QGridLayout()
        for row, meaning in enumerate(MEANINGS):
            label = QLabel(LABELS[meaning], self)
            self._labels[meaning] = label
            grid.addWidget(label, row, 0)

            swatch = QPushButton(self)
            describe_control(swatch, 'swatch')
            swatch.setMinimumWidth(120)
            swatch.clicked.connect(
                lambda _checked=False, m=meaning: self._pick(m))
            self._swatches[meaning] = swatch
            grid.addWidget(swatch, row, 1)
        grid.setColumnStretch(0, 1)
        layout.addLayout(grid)

        self._preview = QLabel(self)
        self._preview.setTextFormat(Qt.TextFormat.RichText)
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        box.button(
            QDialogButtonBox.StandardButton.RestoreDefaults
        ).clicked.connect(self.reset)
        layout.addWidget(box)

        self._refresh()
        explain_widget(self)

    # ---- what the caller reads ------------------------------------------

    def chosen(self) -> dict:
        """The overrides, which is NOT the same as the colours on screen: a
        swatch showing the themed colour reports nothing, so an untouched
        dialog leaves the app following the theme."""
        return dict(self._chosen)

    def shown_colour(self, meaning: str) -> str:
        return self._chosen.get(meaning) or default_colour(meaning)

    def label_for(self, meaning: str) -> str:
        return self._labels[meaning].text()

    def swatch(self, meaning: str):
        return self._swatches.get(meaning)

    def preview_html(self) -> str:
        return LogMessageDelegate.rich_text(
            _SAMPLE, self.palette().color(
                self.foregroundRole()).name(),
            LogMessageDelegate.compiled([_SAMPLE_NEEDLE]),
            match_colour=self._chosen.get("match"),
            error_colour=self._chosen.get("error"))

    # ---- changing them ---------------------------------------------------

    def set_colour(self, meaning: str, colour) -> None:
        if meaning not in MEANINGS or not is_valid_hex_colour(colour):
            return
        self._chosen[meaning] = colour
        self._refresh()

    def reset(self) -> None:
        self._chosen = {}
        self._refresh()

    def _pick(self, meaning: str) -> None:
        chosen = QColorDialog.getColor(
            QColor(self.shown_colour(meaning)), self,
            f"Colour for {LABELS[meaning].lower()}")
        if chosen.isValid():
            self.set_colour(meaning, chosen.name())

    def _refresh(self) -> None:
        for meaning in MEANINGS:
            colour = self.shown_colour(meaning)
            swatch = self._swatches[meaning]
            swatch.setText(colour)
            # Text picked for contrast against the swatch, or the label
            # vanishes on a colour close to the button's own.
            swatch.setStyleSheet(
                f"background-color: {colour}; "
                f"color: {readable_text_on(colour)};")
        self._preview.setText(self.preview_html())
