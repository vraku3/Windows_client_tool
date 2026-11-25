"""Side panel showing full details, structured fields, error analysis, correlation, and fixes for a selected log entry."""
from __future__ import annotations

import html
import json
import logging
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from core.table_ui import set_role
from core.semantic_colors import semantic
from core.types import LogEntry

logger = logging.getLogger(__name__)


def _fmt_value(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


class DetailPanel(QWidget):
    """Side panel showing full details of a selected log entry with multi-tab inspection."""

    entry_focus_requested = pyqtSignal(object)              # emits LogEntry
    zoom_window_requested = pyqtSignal(object, object)      # emits (datetime_start, datetime_end)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumWidth(320)
        self.setVisible(False)
        self._current_entry: Optional[LogEntry] = None
        self._correlated_entries: List[LogEntry] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._setup_header_bar(layout)

        self._tabs = QTabWidget()
        self._setup_overview_tab()
        self._setup_structured_tab()
        self._setup_error_tab()
        self._setup_correlated_tab()
        self._setup_remediation_tab()
        layout.addWidget(self._tabs, 1)

    def _setup_header_bar(self, layout: QVBoxLayout) -> None:
        header_bar = QHBoxLayout()
        header_bar.setSpacing(4)

        self._title = QLabel("Details")
        set_role(self._title, "heading")
        header_bar.addWidget(self._title)
        header_bar.addStretch()

        self._copy_all_btn = QPushButton("Copy")
        self._copy_all_btn.setToolTip("Copy formatted event details to clipboard")
        self._copy_all_btn.setMaximumWidth(65)
        self._copy_all_btn.clicked.connect(self._copy_current_entry)
        header_bar.addWidget(self._copy_all_btn)

        self._copy_ticket_btn = QPushButton("Ticket 📋")
        self._copy_ticket_btn.setToolTip("Copy formatted text for IT support ticket")
        self._copy_ticket_btn.setMaximumWidth(70)
        self._copy_ticket_btn.clicked.connect(self._copy_as_it_ticket)
        header_bar.addWidget(self._copy_ticket_btn)

        self._pop_out_btn = QPushButton("⧉ Pop Out")
        self._pop_out_btn.setToolTip("Open full event inspector in a detached floating window")
        self._pop_out_btn.setMaximumWidth(80)
        self._pop_out_btn.clicked.connect(self._pop_out_dialog)
        header_bar.addWidget(self._pop_out_btn)

        self._web_search_btn = QPushButton("Web Search")
        self._web_search_btn.setToolTip("Search Microsoft documentation for this event")
        self._web_search_btn.setMaximumWidth(85)
        self._web_search_btn.clicked.connect(self._search_event_on_web)
        header_bar.addWidget(self._web_search_btn)

        self._close_btn = QPushButton("✕")
        self._close_btn.setToolTip("Close detail panel")
        self._close_btn.setMaximumWidth(28)
        self._close_btn.clicked.connect(self.hide_panel)
        header_bar.addWidget(self._close_btn)

        layout.addLayout(header_bar)

    def _setup_overview_tab(self) -> None:
        overview_widget = QWidget()
        overview_layout = QVBoxLayout(overview_widget)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(4)

        ov_tools = QHBoxLayout()
        ov_tools.setSpacing(4)
        self._wrap_btn = QPushButton("Wrap: ON")
        self._wrap_btn.setCheckable(True)
        self._wrap_btn.setChecked(True)
        self._wrap_btn.setToolTip("Toggle Word Wrap in message text.")
        self._wrap_btn.clicked.connect(self._toggle_word_wrap)
        ov_tools.addWidget(self._wrap_btn)

        self._ov_search = QLineEdit()
        self._ov_search.setPlaceholderText("Find in message...")
        self._ov_search.setToolTip("Filter text within the event overview message")
        self._ov_search.textChanged.connect(self._search_overview_text)
        ov_tools.addWidget(self._ov_search)

        self._zoom_in_btn = QPushButton("A+")
        self._zoom_in_btn.setToolTip("Increase text size")
        self._zoom_in_btn.setMaximumWidth(28)
        self._zoom_in_btn.clicked.connect(lambda: self._content.zoomIn(1))
        ov_tools.addWidget(self._zoom_in_btn)

        self._zoom_out_btn = QPushButton("A-")
        self._zoom_out_btn.setToolTip("Decrease text size")
        self._zoom_out_btn.setMaximumWidth(28)
        self._zoom_out_btn.clicked.connect(lambda: self._content.zoomOut(1))
        ov_tools.addWidget(self._zoom_out_btn)

        self._copy_msg_pill = QPushButton("Copy Msg")
        self._copy_msg_pill.setToolTip("Copy message text only")
        self._copy_msg_pill.setMaximumWidth(70)
        self._copy_msg_pill.clicked.connect(self._copy_message_only)
        ov_tools.addWidget(self._copy_msg_pill)

        overview_layout.addLayout(ov_tools)

        self._content = QTextEdit()
        self._content.setReadOnly(True)
        self._content.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        overview_layout.addWidget(self._content)
        self._tabs.addTab(overview_widget, "Overview")
        self._tabs.setTabToolTip(0, "Formatted event summary and diagnostic diagnosis")

    def _setup_structured_tab(self) -> None:
        raw_widget = QWidget()
        raw_layout = QVBoxLayout(raw_widget)
        raw_layout.setContentsMargins(4, 4, 4, 4)
        raw_layout.setSpacing(4)

        self._raw_table = QTableWidget()
        self._raw_table.setColumnCount(2)
        self._raw_table.setHorizontalHeaderLabels(["Property", "Value"])
        if self._raw_table.horizontalHeaderItem(0):
            self._raw_table.horizontalHeaderItem(0).setToolTip("Property name")
        if self._raw_table.horizontalHeaderItem(1):
            self._raw_table.horizontalHeaderItem(1).setToolTip("Property value (double-click to copy)")
        self._raw_table.horizontalHeader().setStretchLastSection(True)
        self._raw_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._raw_table.setAlternatingRowColors(True)
        self._raw_table.verticalHeader().setVisible(False)
        self._raw_table.cellDoubleClicked.connect(self._on_raw_cell_double_clicked)
        raw_layout.addWidget(self._raw_table)

        raw_btn_row = QHBoxLayout()
        self._copy_json_btn = QPushButton("Copy as JSON")
        self._copy_json_btn.setToolTip("Copy structured event data as JSON")
        self._copy_json_btn.clicked.connect(self._copy_raw_json)
        raw_btn_row.addWidget(self._copy_json_btn)

        self._copy_xml_btn = QPushButton("Copy as XML")
        self._copy_xml_btn.setToolTip("Copy raw event XML to clipboard")
        self._copy_xml_btn.clicked.connect(self._copy_raw_xml)
        raw_btn_row.addWidget(self._copy_xml_btn)

        raw_btn_row.addStretch()
        raw_layout.addLayout(raw_btn_row)

        self._tabs.addTab(raw_widget, "Structured Data")
        self._tabs.setTabToolTip(1, "Structured key-value properties and raw event data")

    def _setup_error_tab(self) -> None:
        self._error_analysis_widget = QWidget()
        error_layout = QVBoxLayout(self._error_analysis_widget)
        error_layout.setContentsMargins(4, 4, 4, 4)
        error_layout.setSpacing(4)

        self._error_analysis_text = QTextEdit()
        self._error_analysis_text.setReadOnly(True)
        error_layout.addWidget(self._error_analysis_text)
        self._tabs.addTab(self._error_analysis_widget, "Error Analysis")
        self._tabs.setTabToolTip(2, "Automatic decoding and explanation of error codes")

    def _setup_correlated_tab(self) -> None:
        corr_widget = QWidget()
        corr_layout = QVBoxLayout(corr_widget)
        corr_layout.setContentsMargins(4, 4, 4, 4)
        corr_layout.setSpacing(4)

        self._corr_hint = QLabel("Events occurring within ± 60 seconds of this record:")
        self._corr_hint.setWordWrap(True)
        set_role(self._corr_hint, "muted")
        corr_layout.addWidget(self._corr_hint)

        self._corr_table = QTableWidget()
        self._corr_table.setColumnCount(4)
        self._corr_table.setHorizontalHeaderLabels(["Time", "Level", "Source", "Message"])
        for idx, tip in enumerate(["Timestamp of event", "Severity level", "Event provider/source", "Event message"]):
            item = self._corr_table.horizontalHeaderItem(idx)
            if item:
                item.setToolTip(tip)
        self._corr_table.horizontalHeader().setStretchLastSection(True)
        self._corr_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._corr_table.setAlternatingRowColors(True)
        self._corr_table.verticalHeader().setVisible(False)
        self._corr_table.doubleClicked.connect(self._on_corr_row_double_clicked)
        corr_layout.addWidget(self._corr_table)

        corr_btn_row = QHBoxLayout()
        self._zoom_window_btn = QPushButton("🔍 Zoom View to Window (± 5 min)")
        self._zoom_window_btn.setToolTip("Filter the table to events within 5 minutes before and after this event.")
        self._zoom_window_btn.clicked.connect(self._on_zoom_window_clicked)
        corr_btn_row.addWidget(self._zoom_window_btn)
        corr_btn_row.addStretch()
        corr_layout.addLayout(corr_btn_row)

        self._tabs.addTab(corr_widget, "Correlated")
        self._tabs.setTabToolTip(3, "Events occurring around the same time across system logs")

    def _setup_remediation_tab(self) -> None:
        remediation_widget = QWidget()
        remediation_layout = QVBoxLayout(remediation_widget)
        remediation_layout.setContentsMargins(4, 4, 4, 4)
        remediation_layout.setSpacing(8)

        self._remediation_info = QLabel("No automated remediation available for this event.")
        self._remediation_info.setWordWrap(True)
        set_role(self._remediation_info, "muted")
        remediation_layout.addWidget(self._remediation_info)

        self._action_btn = QPushButton()
        self._action_btn.setToolTip("Execute recommended repair action")
        self._action_btn.setVisible(False)
        self._action_btn.clicked.connect(self._on_action_clicked)
        self._action_target = ""
        remediation_layout.addWidget(self._action_btn)
        remediation_layout.addStretch()

        self._tabs.addTab(remediation_widget, "Remediation")
        self._tabs.setTabToolTip(4, "Recommended sysadmin actions and one-click fixes")

    def _on_action_clicked(self) -> None:
        if not self._action_target:
            return
        try:
            from core.app import App
            from core.events import NAV_REQUEST_MODULE, NavRequestData
            App.get().event_bus.publish(NAV_REQUEST_MODULE, NavRequestData(module_name=self._action_target))
        except Exception as exc:
            logger.debug("Failed to navigate to target %s: %s", self._action_target, exc)

    def _copy_current_entry(self) -> None:
        if self._current_entry is None:
            return
        e = self._current_entry
        lines = [
            f"Timestamp: {e.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Source:    {e.source}",
            f"Level:     {e.level}",
        ]
        if e.raw:
            for k, v in e.raw.items():
                lines.append(f"{k}: {v}")
        lines.append("")
        lines.append(f"Message:\n{e.message}")
        QApplication.clipboard().setText("\n".join(lines))

    def _copy_as_it_ticket(self) -> None:
        """Format and copy event details as a structured IT support ticket."""
        if self._current_entry is None:
            return
        e = self._current_entry
        raw = e.raw or {}
        lines = [
            "=" * 60,
            "INCIDENT REPORT: Windows Diagnostic Record",
            "=" * 60,
            f"Timestamp:   {e.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Source:      {e.source}",
            f"Level:       {e.level}",
        ]
        if "event_id" in raw:
            lines.append(f"Event ID:    {raw['event_id']}")
        if "computer" in raw:
            lines.append(f"Computer:    {raw['computer']}")
        if "user" in raw:
            lines.append(f"User / SID:  {raw['user']}")
        lines.append("")
        lines.append("DESCRIPTION:")
        lines.append(str(e.message or "").strip())
        lines.append("")
        err_txt = self._error_analysis_text.toPlainText().strip()
        if err_txt:
            lines.append("ERROR ANALYSIS:")
            lines.append(err_txt)
            lines.append("")
        if self._action_target:
            lines.append("RECOMMENDED ACTION:")
            lines.append(f"{self._action_btn.text()} ({self._action_btn.toolTip()})")
            lines.append("")
        lines.append("=" * 60)
        QApplication.clipboard().setText("\n".join(lines))

    def _pop_out_dialog(self) -> None:
        """Pop out the active entry into a detached floating inspector window."""
        if self._current_entry is None:
            return
        from ui.event_detail_dialog import EventDetailDialog
        dlg = EventDetailDialog(self._current_entry, self)
        dlg.exec()

    def _copy_raw_json(self) -> str:
        if self._current_entry is None:
            return ""
        data = {
            "timestamp": self._current_entry.timestamp.isoformat(),
            "source": str(self._current_entry.source),
            "level": str(self._current_entry.level),
            "message": str(self._current_entry.message),
            "raw": self._current_entry.raw or {},
        }
        text = json.dumps(data, indent=2, default=str)
        try:
            QApplication.clipboard().setText(text)
        except Exception as exc:
            logger.debug("Clipboard copy failed: %s", exc)
        return text

    def _copy_raw_xml(self) -> None:
        """Format and copy event details as standard Windows Event XML."""
        if self._current_entry is None:
            return
        e = self._current_entry
        raw = e.raw or {}
        if "xml" in raw:
            QApplication.clipboard().setText(str(raw["xml"]))
            return
        esc = html.escape
        xml_lines = [
            '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">',
            '  <System>',
            f'    <Provider Name="{esc(str(e.source))}" />',
            f'    <TimeCreated SystemTime="{e.timestamp.isoformat()}" />',
            f'    <Level>{esc(str(e.level))}</Level>',
        ]
        if "event_id" in raw:
            xml_lines.append(f'    <EventID>{raw["event_id"]}</EventID>')
        if "computer" in raw:
            xml_lines.append(f'    <Computer>{esc(str(raw["computer"]))}</Computer>')
        if "channel" in raw:
            xml_lines.append(f'    <Channel>{esc(str(raw["channel"]))}</Channel>')
        xml_lines.append('  </System>')
        xml_lines.append('  <EventData>')
        for k, v in raw.items():
            if not k.startswith("_"):
                xml_lines.append(f'    <Data Name="{esc(str(k))}">{esc(_fmt_value(v))}</Data>')
        xml_lines.append(f'    <Data Name="Message">{esc(str(e.message))}</Data>')
        xml_lines.append('  </EventData>')
        xml_lines.append('</Event>')
        QApplication.clipboard().setText("\n".join(xml_lines))

    def _copy_message_only(self) -> None:
        """Copy the message text of the current entry to clipboard."""
        if self._current_entry and self._current_entry.message:
            QApplication.clipboard().setText(str(self._current_entry.message))

    def _on_raw_cell_double_clicked(self, row: int, col: int) -> None:
        """Double-clicking any structured property cell copies its exact text."""
        item = self._raw_table.item(row, col)
        if item and item.text():
            QApplication.clipboard().setText(item.text())

    def _search_event_on_web(self) -> None:
        if self._current_entry is None:
            return
        e = self._current_entry
        event_id = (e.raw or {}).get("event_id", "")
        terms = [str(e.source)]
        if event_id:
            terms.append(f"Event ID {event_id}")
        else:
            first_line = (e.message or "").splitlines()[0][:80]
            terms.append(first_line)
        query = "Windows " + " ".join(terms)
        url = f"https://learn.microsoft.com/en-us/search/?terms={urllib.parse.quote_plus(query)}"
        try:
            webbrowser.open(url)
        except Exception as exc:
            logger.debug("Failed to open web search: %s", exc)

    def _on_corr_row_double_clicked(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._correlated_entries):
            target = self._correlated_entries[row]
            self.entry_focus_requested.emit(target)

    def _on_zoom_window_clicked(self) -> None:
        if self._current_entry and getattr(self._current_entry, "timestamp", None):
            t = self._current_entry.timestamp
            self.zoom_window_requested.emit(t - timedelta(minutes=5), t + timedelta(minutes=5))

    def _populate_overview(self, entry: LogEntry, extra_html: str, esc) -> None:
        doc = extra_html
        if entry.raw and "_user_note" in entry.raw:
            from core.semantic_colors import chrome
            info_col = semantic("info")
            surf_col = chrome("surface")
            txt_col = chrome("text")
            doc += f"""
            <div style="background-color: {surf_col}; color: {txt_col}; border-left: 4px solid {info_col}; padding: 8px; margin-bottom: 8px; border-radius: 4px;">
                <b>📝 User Annotation:</b> {esc(str(entry.raw['_user_note']))}
            </div>
            """
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

    def _populate_raw_table(self, entry: LogEntry) -> None:
        properties = [
            ("Timestamp", entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")),
            ("Source", str(entry.source)),
            ("Level", str(entry.level)),
        ]
        if entry.raw:
            for k, v in entry.raw.items():
                properties.append((str(k), _fmt_value(v)))

        self._raw_table.setRowCount(len(properties))
        for row, (k, v) in enumerate(properties):
            k_item = QTableWidgetItem(k)
            k_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            v_item = QTableWidgetItem(v)
            v_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._raw_table.setItem(row, 0, k_item)
            self._raw_table.setItem(row, 1, v_item)

    def _populate_error_analysis(self, entry: LogEntry, esc) -> None:
        try:
            from modules.log_viewer.error_codes import find_codes, describe, advice
            text_to_scan = f"{entry.message} {entry.raw}"
            codes = find_codes(text_to_scan)
            if codes:
                err_doc = "<h3>Detected Error Codes:</h3>"
                for c in codes:
                    hex_code = f"0x{c:08X}"
                    desc = describe(c)
                    adv = advice(c)
                    code_lbl = f"<code>{hex_code}</code>"
                    if 0 < c <= 65535:
                        code_lbl += f" (Decimal: <code>{c}</code>)"
                    err_doc += f"<p><b>Code:</b> {code_lbl}"
                    if desc:
                        err_doc += f"<br><b>Meaning:</b> {esc(desc)}"
                    if adv:
                        err_doc += f"<br><b>Probable Cause:</b> {esc(adv.cause)}"
                        err_doc += f"<br><b>Recommended Remedy:</b> <code>{esc(adv.remedy)}</code>"
                        if adv.reference:
                            err_doc += f"<br><i>Reference: {esc(adv.reference)}</i>"
                    err_doc += "</p><hr>"
                self._error_analysis_text.setHtml(err_doc)
                self._tabs.setTabText(2, f"Error Analysis ({len(codes)})")
            else:
                self._error_analysis_text.setHtml("<p><i>No failing error codes detected in this record.</i></p>")
                self._tabs.setTabText(2, "Error Analysis")
        except Exception as exc:
            logger.debug("Error analysis lookup failed: %s", exc)
            self._error_analysis_text.setPlainText("Error lookup unavailable.")

    def _populate_correlated_tab(self, entry: LogEntry, all_entries: Optional[List[LogEntry]]) -> None:
        self._correlated_entries = []
        if all_entries and getattr(entry, "timestamp", None):
            t_ref = entry.timestamp
            start_w = t_ref - timedelta(seconds=60)
            end_w = t_ref + timedelta(seconds=60)
            for other in all_entries:
                if other is not entry and getattr(other, "timestamp", None):
                    if start_w <= other.timestamp <= end_w:
                        self._correlated_entries.append(other)
                        if len(self._correlated_entries) >= 50:
                            break

        self._corr_table.setRowCount(len(self._correlated_entries))
        for row, other in enumerate(self._correlated_entries):
            t_item = QTableWidgetItem(other.timestamp.strftime("%H:%M:%S"))
            l_item = QTableWidgetItem(str(other.level))
            s_item = QTableWidgetItem(str(other.source)[:24])
            m_item = QTableWidgetItem(" ".join(str(other.message or "").split())[:80])
            for itm in (t_item, l_item, s_item, m_item):
                itm.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._corr_table.setItem(row, 0, t_item)
            self._corr_table.setItem(row, 1, l_item)
            self._corr_table.setItem(row, 2, s_item)
            self._corr_table.setItem(row, 3, m_item)

        if self._correlated_entries:
            self._tabs.setTabText(3, f"Correlated ({len(self._correlated_entries)})")
        else:
            self._tabs.setTabText(3, "Correlated")

    def _populate_remediation_tab(self, entry: LogEntry) -> None:
        from core.diagnostic_bridge import match_suggested_fix
        fix = match_suggested_fix(str(entry.source or ""), f"{entry.message} {entry.raw or {}}")
        if not fix and entry.raw and (entry.raw.get("is_third_party") or str(entry.raw.get("faulting_module", "")).lower().endswith(".sys")):
            culprit_name = entry.raw.get("culprit_driver") or entry.raw.get("faulting_module")
            from core.diagnostic_bridge import SuggestedFix
            fix = SuggestedFix(
                title=f"Inspect Driver: {culprit_name}",
                target_module="Driver Manager",
                reason=f"Third-party kernel driver {culprit_name} was identified as the crash culprit.",
            )
        if fix:
            self._action_btn.setText(f"🛠️ Fix: {fix.title}")
            self._action_btn.setToolTip(f"{fix.reason} (Click to open {fix.target_module})")
            self._action_btn.setVisible(True)
            self._action_target = fix.target_module
            self._remediation_info.setText(
                f"<b>Recommended Fix:</b> {html.escape(fix.title)}<br>"
                f"<b>Reason:</b> {html.escape(fix.reason)}<br>"
                f"<b>Target Module:</b> {html.escape(fix.target_module)}"
            )
            self._tabs.setTabText(4, "Remediation (1)")
        else:
            self._action_btn.setVisible(False)
            self._action_target = ""
            self._remediation_info.setText("No automated one-click fix pattern matched for this record.")
            self._tabs.setTabText(4, "Remediation")

    def show_entry(self, entry: LogEntry, extra_html: str = "", all_entries: Optional[List[LogEntry]] = None) -> None:
        """Display full details of a log entry across all inspector tabs."""
        self._current_entry = entry
        self._content.clear()
        esc = html.escape
        self._populate_overview(entry, extra_html, esc)
        self._populate_raw_table(entry)
        self._populate_error_analysis(entry, esc)
        self._populate_correlated_tab(entry, all_entries)
        self._populate_remediation_tab(entry)
        self.setVisible(True)

        self.setVisible(True)

    def hide_panel(self) -> None:
        self.setVisible(False)
        self._current_entry = None
        self._correlated_entries = []
        self._content.clear()
        self._raw_table.setRowCount(0)
        self._corr_table.setRowCount(0)
        self._error_analysis_text.clear()
        self._action_btn.setVisible(False)
        self._action_target = ""

    def _toggle_word_wrap(self) -> None:
        if self._wrap_btn.isChecked():
            self._content.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            self._wrap_btn.setText("Wrap: ON")
        else:
            self._content.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            self._wrap_btn.setText("Wrap: OFF")

    def _search_overview_text(self, text: str) -> None:
        if not text:
            cursor = self._content.textCursor()
            cursor.clearSelection()
            self._content.setTextCursor(cursor)
            return
        found = self._content.find(text)
        if not found:
            cursor = self._content.textCursor()
            cursor.setPosition(0)
            self._content.setTextCursor(cursor)
            self._content.find(text)
