from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QVBoxLayout, QFrame,
)

from core.types import LogEntry

_LEVEL_COLORS = {
    "Error": ("#f44336", "#ffffff"),
    "Warning": ("#ff9800", "#000000"),
    "Critical": ("#d32f2f", "#ffffff"),
    "Info": ("#2196f3", "#ffffff"),
    "Debug": ("#757575", "#ffffff"),
}


class EventDetailDialog(QDialog):
    """Resizable dialog showing a log event with beautifully formatted text."""

    def __init__(self, entry: LogEntry, parent=None):
        super().__init__(parent)
        self._entry = entry
        self.setWindowTitle(f"Event Detail — {entry.source}")
        self.resize(820, 620)
        self.setMinimumSize(500, 350)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        # ── Header card ─────────────────────────────────────────────────
        header_card = QFrame()
        header_card.setObjectName("card")
        header_layout = QVBoxLayout(header_card)
        header_layout.setSpacing(4)

        row1 = QHBoxLayout()
        ts_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        lbl_time = QLabel(f"<span style='color:#b0b0b0;font-size:11px;'>TIME</span><br>"
                          f"<span style='font-size:13px;'>{ts_str}</span>")
        row1.addWidget(lbl_time)

        lbl_source = QLabel(f"<span style='color:#b0b0b0;font-size:11px;'>SOURCE</span><br>"
                            f"<span style='font-size:13px;'>{entry.source}</span>")
        row1.addWidget(lbl_source)

        bg, fg = _LEVEL_COLORS.get(entry.level, ("#555555", "#ffffff"))
        lbl_level = QLabel(
            f"<span style='color:#b0b0b0;font-size:11px;'>LEVEL</span><br>"
            f"<span style='background:{bg};color:{fg};"
            f"padding:2px 10px;border-radius:3px;font-weight:bold;font-size:12px;'>&nbsp;{entry.level}&nbsp;</span>"
        )
        row1.addWidget(lbl_level)
        row1.addStretch()
        header_layout.addLayout(row1)

        layout.addWidget(header_card)

        # ── Separator ───────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # ── Message area ────────────────────────────────────────────────
        msg_label = QLabel("<b style='font-size:12px;'>MESSAGE</b>")
        layout.addWidget(msg_label)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 9))
        self._text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._text.setHtml(self._format_html())
        layout.addWidget(self._text, 1)

        # ── Raw data section ────────────────────────────────────────────
        if entry.raw:
            raw_sep = QFrame()
            raw_sep.setFrameShape(QFrame.Shape.HLine)
            layout.addWidget(raw_sep)

            raw_label = QLabel("<b style='font-size:12px;'>RAW DATA</b>")
            layout.addWidget(raw_label)

            self._raw_text = QTextEdit()
            self._raw_text.setReadOnly(True)
            self._raw_text.setFont(QFont("Consolas", 9))
            self._raw_text.setMaximumHeight(160)
            self._raw_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            self._raw_text.setPlainText(self._format_raw())
            layout.addWidget(self._raw_text)

        # ── Buttons ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        copy_all_btn = QPushButton("Copy All")
        copy_all_btn.setToolTip("Copy full content to clipboard")
        copy_all_btn.clicked.connect(self._copy_all)
        btn_row.addWidget(copy_all_btn)

        copy_sel_btn = QPushButton("Copy Selection")
        copy_sel_btn.setToolTip("Copy selected text to clipboard")
        copy_sel_btn.clicked.connect(self._copy_selection)
        btn_row.addWidget(copy_sel_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.accept)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.accept)

    def _format_html(self) -> str:
        entry = self._entry
        ts_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        message_html = entry.message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        message_html = message_html.replace("\n", "<br>")
        html = f"""
        <div style='font-family:Consolas,monospace; font-size:10pt;'>
            <span style='color:#888;'>Timestamp :</span> <b>{ts_str}</b><br>
            <span style='color:#888;'>Source    :</span> <b>{entry.source}</b><br>
            <span style='color:#888;'>Level     :</span> <b>{entry.level}</b><br>
            <br>
            <span style='color:#888;'>─ Message ─</span><br>
            <div style='padding:8px; border-radius:4px; margin-top:4px;'>
                {message_html}
            </div>
        </div>
        """
        return html

    def _format_raw(self) -> str:
        lines = []
        for k, v in self._entry.raw.items():
            lines.append(f"{k}: {v}")
        return "\n".join(lines)

    def _copy_all(self):
        parts = [
            f"Timestamp: {self._entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Source: {self._entry.source}",
            f"Level: {self._entry.level}",
            f"Message: {self._entry.message}",
        ]
        if self._entry.raw:
            parts.append(f"\nRaw Data:\n{self._format_raw()}")
        QApplication.clipboard().setText("\n".join(parts))

    def _copy_selection(self):
        cursor = self._text.textCursor()
        selected = cursor.selectedText()
        if selected:
            QApplication.clipboard().setText(selected)
        else:
            self._copy_all()
