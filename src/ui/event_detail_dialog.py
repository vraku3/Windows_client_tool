import html
import json
import logging
import urllib.parse
import webbrowser

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from core.semantic_colors import chrome, semantic
from core.types import LogEntry

logger = logging.getLogger(__name__)


class EventDetailDialog(QDialog):
    """Resizable dialog showing a log event with formatted text, error lookup, and remediation."""

    def __init__(self, entry: LogEntry, parent=None):
        super().__init__(parent)
        self._entry = entry
        self.setWindowTitle(f"Event Detail — {entry.source}")
        self.resize(860, 640)
        self.setMinimumSize(520, 380)
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
        muted = chrome("text_muted")
        lbl_time = QLabel(f"<span style='color:{muted};font-size:11px;'>TIME</span><br>"
                          f"<span style='font-size:13px;'>{ts_str}</span>")
        row1.addWidget(lbl_time)

        lbl_source = QLabel(f"<span style='color:{muted};font-size:11px;'>SOURCE</span><br>"
                            f"<span style='font-size:13px;'>{entry.source}</span>")
        row1.addWidget(lbl_source)

        eid = (entry.raw or {}).get("event_id")
        if eid:
            lbl_eid = QLabel(f"<span style='color:{muted};font-size:11px;'>EVENT ID</span><br>"
                             f"<span style='font-size:13px;'>{eid}</span>")
            row1.addWidget(lbl_eid)

        lvl = (entry.level or "Info").lower()
        if lvl in ("error", "critical"):
            bg = semantic("error")
        elif lvl == "warning":
            bg = semantic("warning")
        elif lvl == "info":
            bg = semantic("info")
        else:
            bg = chrome("text_muted")
        fg = chrome("overlay_text")
        lbl_level = QLabel(
            f"<span style='color:{muted};font-size:11px;'>LEVEL</span><br>"
            f"<span style='background:{bg};color:{fg};"
            f"padding:2px 10px;border-radius:3px;font-weight:bold;font-size:12px;'>&nbsp;{entry.level}&nbsp;</span>"
        )
        row1.addWidget(lbl_level)
        row1.addStretch()
        header_layout.addLayout(row1)

        layout.addWidget(header_card)

        # ── Inspector Tabs ─────────────────────────────────────────────
        self._tabs = QTabWidget()

        # Tab 1: Formatted Message
        msg_widget = QWidget()
        msg_layout = QVBoxLayout(msg_widget)
        msg_layout.setContentsMargins(4, 4, 4, 4)
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 10))
        self._text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._text.setHtml(self._format_html())
        msg_layout.addWidget(self._text)
        self._tabs.addTab(msg_widget, "Message")

        # Tab 2: Error Code Analysis
        self._error_text = QTextEdit()
        self._error_text.setReadOnly(True)
        self._error_text.setFont(QFont("Segoe UI", 9))
        self._tabs.addTab(self._error_text, "Error Analysis")
        self._populate_error_tab()

        # Tab 3: Raw Data
        raw_widget = QWidget()
        raw_layout = QVBoxLayout(raw_widget)
        raw_layout.setContentsMargins(4, 4, 4, 4)
        self._raw_text = QTextEdit()
        self._raw_text.setReadOnly(True)
        self._raw_text.setFont(QFont("Consolas", 9))
        self._raw_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._raw_text.setPlainText(self._format_raw())
        raw_layout.addWidget(self._raw_text)
        self._tabs.addTab(raw_widget, "Raw Data")

        # Tab 4: Remediation
        remediation_widget = QWidget()
        rem_layout = QVBoxLayout(remediation_widget)
        rem_layout.setContentsMargins(8, 8, 8, 8)
        rem_layout.setSpacing(8)

        self._rem_info = QLabel("No automated remediation available.")
        self._rem_info.setWordWrap(True)
        rem_layout.addWidget(self._rem_info)

        self._action_btn = QPushButton()
        self._action_btn.setVisible(False)
        self._action_btn.clicked.connect(self._on_action_clicked)
        self._action_target = ""
        rem_layout.addWidget(self._action_btn)
        rem_layout.addStretch()

        self._tabs.addTab(remediation_widget, "Remediation")
        self._populate_remediation()

        layout.addWidget(self._tabs, 1)

        # ── Buttons ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        copy_all_btn = QPushButton("Copy All")
        copy_all_btn.setToolTip("Copy full content to clipboard")
        copy_all_btn.clicked.connect(self._copy_all)
        btn_row.addWidget(copy_all_btn)

        copy_json_btn = QPushButton("Copy JSON")
        copy_json_btn.setToolTip("Copy event payload as formatted JSON")
        copy_json_btn.clicked.connect(self._copy_json)
        btn_row.addWidget(copy_json_btn)

        web_btn = QPushButton("Web Search")
        web_btn.setToolTip("Search Microsoft documentation online")
        web_btn.clicked.connect(self._search_web)
        btn_row.addWidget(web_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.accept)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.accept)

    def _populate_error_tab(self) -> None:
        try:
            from modules.log_viewer.error_codes import find_codes, describe, advice
            text_to_scan = f"{self._entry.message} {self._entry.raw}"
            codes = find_codes(text_to_scan)
            if codes:
                esc = html.escape
                doc = "<h3>Detected Error Codes:</h3>"
                for c in codes:
                    hex_code = f"0x{c:08X}"
                    desc = describe(c)
                    adv = advice(c)
                    code_lbl = f"<code>{hex_code}</code>"
                    if 0 < c <= 65535:
                        code_lbl += f" (Decimal: <code>{c}</code>)"
                    doc += f"<p><b>Code:</b> {code_lbl}"
                    if desc:
                        doc += f"<br><b>Meaning:</b> {esc(desc)}"
                    if adv:
                        doc += f"<br><b>Probable Cause:</b> {esc(adv.cause)}"
                        doc += f"<br><b>Recommended Remedy:</b> <code>{esc(adv.remedy)}</code>"
                        if adv.reference:
                            doc += f"<br><i>Reference: {esc(adv.reference)}</i>"
                    doc += "</p><hr>"
                self._error_text.setHtml(doc)
                self._tabs.setTabText(1, f"Error Analysis ({len(codes)})")
            else:
                self._error_text.setHtml("<p><i>No failing error codes detected in this record.</i></p>")
        except Exception as exc:
            logger.debug("Error analysis lookup failed: %s", exc)
            self._error_text.setPlainText("Error code lookup unavailable.")

    def _populate_remediation(self) -> None:
        from core.diagnostic_bridge import match_suggested_fix
        fix = match_suggested_fix(str(self._entry.source or ""), str(self._entry.message or ""))
        if fix:
            self._action_btn.setText(f"🛠️ Fix: {fix.title}")
            self._action_btn.setToolTip(f"{fix.reason} (Click to open {fix.target_module})")
            self._action_btn.setVisible(True)
            self._action_target = fix.target_module
            self._rem_info.setText(
                f"<b>Recommended Action:</b> {html.escape(fix.title)}<br>"
                f"<b>Reason:</b> {html.escape(fix.reason)}<br>"
                f"<b>Target Module:</b> {html.escape(fix.target_module)}"
            )
            self._tabs.setTabText(3, "Remediation (1)")
        else:
            self._action_btn.setVisible(False)
            self._action_target = ""
            self._rem_info.setText("No automated one-click fix pattern matched for this record.")

    def _on_action_clicked(self) -> None:
        if not self._action_target:
            return
        try:
            from core.app import App
            from core.events import NAV_REQUEST_MODULE, NavRequestData
            App.get().event_bus.publish(NAV_REQUEST_MODULE, NavRequestData(module_name=self._action_target))
            self.accept()
        except Exception as ex:
            logger.debug("Failed publishing navigation request to %s: %s", self._action_target, ex)

    def _search_web(self) -> None:
        e = self._entry
        event_id = (e.raw or {}).get("event_id", "")
        terms = [str(e.source)]
        if event_id:
            terms.append(f"Event ID {event_id}")
        else:
            terms.append((e.message or "").splitlines()[0][:80])
        query = "Windows " + " ".join(terms)
        url = f"https://learn.microsoft.com/en-us/search/?terms={urllib.parse.quote_plus(query)}"
        try:
            webbrowser.open(url)
        except Exception as ex:
            logger.debug("Failed opening browser for search %s: %s", url, ex)

    def _copy_json(self) -> str:
        data = {
            "timestamp": self._entry.timestamp.isoformat(),
            "source": str(self._entry.source),
            "level": str(self._entry.level),
            "message": str(self._entry.message),
            "raw": self._entry.raw or {},
        }
        text = json.dumps(data, indent=2, default=str)
        try:
            QApplication.clipboard().setText(text)
        except Exception as ex:
            logger.debug("Failed copying JSON to clipboard: %s", ex)
        return text

    def _format_html(self) -> str:
        from core.semantic_colors import chrome
        muted = chrome("text_muted")
        entry = self._entry
        ts_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        message_html = entry.message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        message_html = message_html.replace("\n", "<br>")
        html_doc = f"""
        <div style='font-family:Consolas,monospace; font-size:10pt;'>
            <span style='color:{muted};'>Timestamp :</span> <b>{ts_str}</b><br>
            <span style='color:{muted};'>Source    :</span> <b>{entry.source}</b><br>
            <span style='color:{muted};'>Level     :</span> <b>{entry.level}</b><br>
            <br>
            <span style='color:{muted};'>─ Message ─</span><br>
            <div style='padding:8px; border-radius:4px; margin-top:4px;'>
                {message_html}
            </div>
        </div>
        """
        return html_doc

    def _format_raw(self) -> str:
        lines = []
        for k, v in (self._entry.raw or {}).items():
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
