"""CMTrace's Error Lookup: paste a code or a whole line, get the meaning.

A shell over `error_codes` -- no lookup logic lives here. It exists because
the tooltip and the detail pane can only explain a code that is already on a
visible row, and the code you are chasing is often one someone read out to
you over the phone.
"""

from modules.log_viewer.help_widgets import (
    describe_control, explain_widget,
)
from PyQt6.QtWidgets import (QDialog, QLabel, QLineEdit, QPlainTextEdit,
                             QPushButton, QVBoxLayout)

from .error_codes import advice, describe, find_codes


class ErrorLookupDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Error lookup")
        self.resize(560, 320)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Paste a code, or a whole log line:", self))
        self.input = QLineEdit(self)
        describe_control(self.input, 'input')
        self.input.setPlaceholderText("0x80070005")
        self.input.returnPressed.connect(self._look_up_input)
        layout.addWidget(self.input)
        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)
        button = QPushButton("Look up", self)
        describe_control(button, 'button')
        button.clicked.connect(self._look_up_input)
        layout.addWidget(button)
        explain_widget(self)

    def look_up(self, text: str) -> str:
        codes = find_codes(text)
        if not codes:
            return "No error code found in that text."
        lines = []
        for code in codes:
            meaning = describe(code)
            # Silence beats a guess, which is why `describe` returns "" --
            # but the person asked, so say that nothing is known rather
            # than showing them a blank box.
            lines.append(f"0x{code:08X}  —  "
                         f"{meaning or 'not a code this tool knows.'}")
            # Only for the few codes with a documented cause. Every other
            # code keeps the name-only answer: a plausible-sounding fix for
            # the wrong error costs more than no fix at all, because it is
            # acted on.
            note = advice(code)
            if note is not None:
                lines.append(f"    Why : {note.cause}")
                lines.append(f"    Try : {note.remedy}")
                lines.append(f"    Ref : {note.reference}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _look_up_input(self) -> None:
        self.output.setPlainText(self.look_up(self.input.text()))

    def show_for(self, text: str) -> None:
        self.input.setText(text)
        self._look_up_input()
        self.show()
        self.raise_()
