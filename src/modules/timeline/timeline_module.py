"""Unified Change Timeline ('Flight Recorder') as a Diagnose tab.

Presents a chronological audit trail of all changes made to the machine:
- WinClientTool maintenance actions (tweaks, game mode sessions, debloat, updates).
- Windows Update installations and failures.
- PnP Driver updates and device installations.
- MSI application installations and removals.
- Clean and dirty reboots (Kernel-General 12/13, Kernel-Power 41, Eventlog 6005/6006/6008).
- System Restore point creations and restorations.

Includes interactive Reboot Session grouping and Crash correlation.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from core.flight_recorder import (
    SessionBlock,
    correlate_sessions,
    filter_entries_by_session,
    read_flight_recorder_entries,
    timeline_summary_text,
)
from core.log_reader_module import LogReaderModule
from core.semantic_colors import chrome, semantic
from core.types import LogEntry
from modules.timeline.timeline_search_provider import TimelineSearchProvider

logger = logging.getLogger(__name__)


def _detail_html(entry: LogEntry) -> str:
    """Format rich details for a timeline change event, including session correlation."""
    import html
    import json

    raw = entry.raw or {}
    lvl = (entry.level or "info").lower()
    sem_color = (
        semantic("error") if ("err" in lvl or "crit" in lvl)
        else semantic("warning") if "warn" in lvl
        else semantic("success") if ("succ" in lvl or "ok" in lvl)
        else semantic("info")
    )
    out = [f"<b>{html.escape(entry.source)}</b> &mdash; <i style='color:{sem_color};'>{html.escape(entry.level)}</i><br>"]
    out.append(f"<b>Event Time:</b> {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}<br>")
    out.append(f"<b>Summary:</b> {html.escape(entry.message)}<br>")

    # Correlated Reboot Session metadata
    sess_id = raw.get("session_id")
    if sess_id is not None:
        shut_type = raw.get("session_shutdown_type", "Active")
        uptime = raw.get("session_uptime_str", "")
        is_crash = raw.get("session_is_crash", False)
        crash_badge = f" &mdash; <b style='color:{semantic('error')}'>⚠️ CRASH / ABNORMAL TERMINATION</b>" if is_crash else ""
        uptime_info = f" (Uptime: {uptime})" if uptime else ""
        out.append(
            f"<b>Reboot Session:</b> Session {sess_id} &mdash; <i>{html.escape(str(shut_type))}</i>{uptime_info}{crash_badge}<br>"
        )

    category = raw.get("category", "")
    if category:
        out.append(f"<b>Category:</b> {html.escape(str(category))}<br>")

    if category == "system_state":
        evt_type = raw.get("event_type", "")
        if evt_type in ("dirty_shutdown", "unexpected_shutdown", "crash", "bsod") or raw.get("event_id") in (41, 6008):
            err_col = semantic("error")
            bg_col = chrome("surface")
            out.append(
                f"<div style='background:{bg_col}; border-left: 4px solid {err_col}; padding: 6px; margin: 4px 0;'>"
                f"<b>Abnormal System Termination:</b> This reboot was preceded by an unexpected power loss, freeze, or system crash."
                f"</div>"
            )

    detail = raw.get("detail")
    if detail and isinstance(detail, dict):
        bg = chrome("surface")
        fg = chrome("text")
        border = chrome("outline")
        out.append(f"<b>Details:</b><pre style='background:{bg}; color:{fg}; border:1px solid {border}; border-radius:4px; padding:6px;'>")
        out.append(html.escape(json.dumps(detail, indent=2, ensure_ascii=False)))
        out.append("</pre>")

    data = raw.get("data")
    if data and isinstance(data, dict):
        out.append("<b>Event Data:</b><br>")
        for k, v in data.items():
            if v:
                out.append(f"&nbsp;&nbsp;<b>{html.escape(str(k))}:</b> {html.escape(str(v))}<br>")

    return "".join(out) + "<hr>"


class TimelineModule(LogReaderModule):
    name = "Timeline"
    icon = "⏱️"
    description = "Unified machine change timeline and flight recorder"
    requires_admin = False
    provider_class = TimelineSearchProvider

    def __init__(self) -> None:
        super().__init__()
        self._notes: List[str] = []
        self._sessions: List[SessionBlock] = []
        self._session_combo = None

    @property
    def sessions(self) -> List[SessionBlock]:
        """All correlated reboot session blocks, newest first."""
        return self._sessions

    @property
    def current_session(self) -> Optional[SessionBlock]:
        """The active/latest reboot session block."""
        return self._sessions[0] if self._sessions else None

    @property
    def session_combo(self):
        """The interactive session filter QComboBox widget."""
        return self._session_combo

    def build_controls(self, toolbar, extra) -> None:
        """Add interactive Session filter dropdown to the LogPane toolbar."""
        from PyQt6.QtWidgets import QComboBox, QLabel
        label = QLabel("<b>Session:</b>")
        toolbar.addWidget(label)

        self._session_combo = QComboBox()
        self._session_combo.addItems([
            "All Sessions",
            "Current Session",
            "Previous Session",
            "Session before Crash",
        ])
        self._session_combo.setToolTip("Filter timeline events by reboot session or crash boundary.")
        self._session_combo.setItemData(0, "Show all events across all reboot sessions", 3)  # ToolTipRole
        self._session_combo.setItemData(1, "Show only events from the active boot session", 3)
        self._session_combo.setItemData(2, "Show events from the session immediately prior to current boot", 3)
        self._session_combo.setItemData(3, "Show events from the most recent session preceding a crash or dirty reboot", 3)

        self._session_combo.currentTextChanged.connect(self._on_session_filter_changed)
        toolbar.addWidget(self._session_combo)
        extra["session_combo"] = self._session_combo

    def create_widget(self):
        widget = super().create_widget()
        if self._pane is not None:
            self._pane.set_view_transform(self._apply_session_filter)
        return widget

    def pane_options(self) -> dict:
        return {
            "detail_enricher": _detail_html,
            "summarizer": lambda entries: timeline_summary_text(entries, self._notes, self._sessions),
            "empty_text": "No timeline changes or maintenance events recorded for the selected session filter",
            "healthy_empty": False,
            "read_problems": lambda: self._notes,
        }

    def load_entries(self, worker):
        app_data_dir = ""
        if self.app:
            if hasattr(self.app, "app_data_dir"):
                app_data_dir = self.app.app_data_dir
            elif hasattr(self.app, "config") and hasattr(self.app.config, "app_data_dir"):
                app_data_dir = self.app.config.app_data_dir
        if not app_data_dir:
            try:
                from app import _get_app_data_dir
                app_data_dir = _get_app_data_dir()
            except Exception:
                app_data_dir = ""

        entries, notes = read_flight_recorder_entries(
            app_data_dir=app_data_dir,
            days=30,
            progress_callback=lambda p: worker.signals.progress.emit(p),
        )
        self._sessions = correlate_sessions(entries)
        self._notes = notes
        return entries

    def _apply_session_filter(self, entries: List[LogEntry]) -> List[LogEntry]:
        """Filter entries according to the active dropdown selection."""
        if not hasattr(self, "_session_combo") or self._session_combo is None:
            return entries
        mode = self._session_combo.currentText()
        return filter_entries_by_session(entries, mode, self._sessions)

    def _on_session_filter_changed(self, text: str) -> None:
        """Refresh the pane view when the session filter changes."""
        if self._pane is not None:
            self._pane.refresh_view()

    def set_session_filter(self, mode: str) -> None:
        """Programmatically select a session filter option."""
        if self._session_combo is not None:
            idx = self._session_combo.findText(mode)
            if idx >= 0:
                self._session_combo.setCurrentIndex(idx)
            else:
                self._session_combo.setEditText(mode)
