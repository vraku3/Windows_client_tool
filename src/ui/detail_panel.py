import html

from PyQt6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QWidget

from core.table_ui import set_role
from core.types import LogEntry


def _fmt_value(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


class DetailPanel(QWidget):
    """Side panel showing full details of a selected log entry."""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._title = QLabel("Details")
        set_role(self._title, "heading")
        layout.addWidget(self._title)

        self._content = QTextEdit()
        self._content.setReadOnly(True)
        layout.addWidget(self._content)

    def show_entry(self, entry: LogEntry, extra_html: str = "") -> None:
        """Display full details of a log entry.

        `extra_html` is trusted markup a reader builds itself (already escaped
        where it embeds log text) and is shown above the entry's own fields.
        """
        self._content.clear()
        esc = html.escape
        doc = extra_html
        doc += f"""
        <b>Time:</b> {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}<br>
        <b>Source:</b> {esc(str(entry.source))}<br>
        <b>Level:</b> {esc(str(entry.level))}<br>
        <hr>
        <b>Message:</b><br>
        <pre style="white-space: pre-wrap;">{esc(str(entry.message))}</pre>
        """
        if entry.raw:
            doc += "<hr><b>Raw Data:</b><br><pre style=\"white-space: pre-wrap;\">"
            for k, v in entry.raw.items():
                doc += esc(f"{k}: {_fmt_value(v)}") + "\n"
            doc += "</pre>"
        self._content.setHtml(doc)
        self.setVisible(True)

    def hide_panel(self) -> None:
        self.setVisible(False)
        self._content.clear()
