"""Unified Change Timeline ('Flight Recorder') as a Diagnose tab.

Presents a chronological audit trail of all changes made to the machine:
- WinClientTool maintenance actions (tweaks, game mode sessions, debloat, updates).
- Windows Update installations and failures.
- PnP Driver updates and device installations.
- MSI application installations and removals.
- Clean and dirty reboots (Kernel-Power 41, 6005, 6006, 6008).
- System Restore point creations and restorations.
"""
from __future__ import annotations

import logging
from typing import List

from core.flight_recorder import read_flight_recorder_entries, timeline_summary_text
from core.log_reader_module import LogReaderModule
from core.types import LogEntry
from modules.timeline.timeline_search_provider import TimelineSearchProvider

logger = logging.getLogger(__name__)


def _detail_html(entry: LogEntry) -> str:
    """Format rich details for a timeline change event."""
    import html
    import json

    raw = entry.raw or {}
    out = [f"<b>{html.escape(entry.source)}</b> &mdash; <i>{html.escape(entry.level)}</i><br>"]
    out.append(f"<b>Event Time:</b> {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}<br>")
    out.append(f"<b>Summary:</b> {html.escape(entry.message)}<br>")

    category = raw.get("category", "")
    if category:
        out.append(f"<b>Category:</b> {html.escape(category)}<br>")

    detail = raw.get("detail")
    if detail and isinstance(detail, dict):
        out.append("<b>Details:</b><pre style='background:#222; padding:6px;'>")
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

    def pane_options(self) -> dict:
        return {
            "detail_enricher": _detail_html,
            "summarizer": lambda entries: timeline_summary_text(entries, self._notes),
            "empty_text": "No timeline changes or maintenance events recorded",
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
        self._notes = notes
        return entries

