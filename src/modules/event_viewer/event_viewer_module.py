"""Event Viewer as a Diagnose tab.

A triage view rather than a raw dump: preset filters for the events that
usually explain a bad machine (with live counts), a quick time window, text
filter, grouping by source + ID, and a detail panel that says what an event
means, how often it happened and what else went wrong around it. The table is
`LogPane`; the reading is `event_reader`/`event_query`; the thinking is
`event_analysis`/`event_detail` (both Qt-free).
"""
import logging
from datetime import datetime

from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit

from core.admin_utils import is_admin
from core.log_reader_module import LogReaderModule

from modules.event_viewer import event_analysis as ea
from modules.event_viewer.event_detail import detail_html
from modules.event_viewer.event_reader import read_logs
from modules.event_viewer.event_search_provider import EventViewerSearchProvider

logger = logging.getLogger(__name__)

HOURS_MAP = {
    "1 hour": 1, "6 hours": 6, "12 hours": 12,
    "24 hours": 24, "48 hours": 48, "7 days": 168, "30 days": 720,
}

#: The client-side window: narrows what is already loaded, no re-read.
WITHIN_MAP = {"All loaded": None, "Last hour": 1, "Last 6 hours": 6, "Last 24 hours": 24, "Last 7 days": 168}

MAX_PER_LOG = 5000


class EventViewerModule(LogReaderModule):
    name = "Event Viewer"
    icon = "📋"
    description = "Windows event logs across every channel"
    requires_admin = False
    provider_class = EventViewerSearchProvider

    def __init__(self) -> None:
        super().__init__()
        self._notes: list = []
        self._range_label = "last 24 hours"

    # -- pane wiring ------------------------------------------------------
    def pane_options(self) -> dict:
        return {
            "extra_columns": ["ID", "Count"],
            "extra_values": ea.row_values,
            "detail_enricher": self._detail,
            "summarizer": self._summary,
        }

    def build_controls(self, toolbar, extra) -> None:
        toolbar.addWidget(QLabel("Load:"))
        combo = QComboBox()
        combo.addItems(list(HOURS_MAP))
        combo.setCurrentIndex(3)  # Default: 24 hours
        combo.setToolTip("How far back to read. Changing it needs a Refresh.")
        toolbar.addWidget(combo)
        extra["hours_combo"] = combo

        toolbar.addWidget(QLabel("Show:"))
        presets = QComboBox()
        for p in ea.PRESETS:
            presets.addItem(p.label, p.key)
            presets.setItemData(presets.count() - 1, p.description, 3)  # ToolTipRole
        presets.setMinimumWidth(220)
        toolbar.addWidget(presets)
        extra["preset_combo"] = presets

        within = QComboBox()
        within.addItems(list(WITHIN_MAP))
        within.setToolTip("Narrow what is already loaded to a recent window")
        toolbar.addWidget(within)
        extra["within_combo"] = within

        text = QLineEdit()
        text.setPlaceholderText("Filter source, ID or text")
        text.setClearButtonEnabled(True)
        text.setMinimumWidth(180)
        toolbar.addWidget(text)
        extra["text_edit"] = text

        group = QCheckBox("Group by source + ID")
        group.setToolTip("One row per distinct event with its count (click the Count header to put the most frequent first)")
        toolbar.addWidget(group)
        extra["group_check"] = group

        presets.currentIndexChanged.connect(self._apply_view)
        within.currentIndexChanged.connect(self._apply_view)
        text.textChanged.connect(self._apply_view)
        group.toggled.connect(self._apply_view)

    def _control(self, key: str):
        if self._pane is None:
            return None
        return self._pane.extra.get(key)

    # -- loading ----------------------------------------------------------
    def load_entries(self, worker):
        # The combo is read here, on the way in, so the worker never touches a
        # widget from its own thread.
        hours_back = 24
        combo = self._control("hours_combo")
        if combo is not None:
            hours_back = HOURS_MAP.get(combo.currentText(), 24)
            self._range_label = combo.currentText().replace("1 hour", "last hour")
            if not self._range_label.startswith("last"):
                self._range_label = "last " + self._range_label
        entries, notes = read_logs(
            hours_back=hours_back,
            max_events_per_log=MAX_PER_LOG,
            include_security=is_admin(),
            progress_callback=lambda p: worker.signals.progress.emit(p),
        )
        self._notes = notes
        return entries

    def _summary(self, entries) -> str:
        self._refresh_preset_counts(entries)
        return ea.summary_text(entries, self._notes, self._range_label)

    def _refresh_preset_counts(self, entries) -> None:
        combo = self._control("preset_combo")
        if combo is None:
            return
        counts = ea.preset_counts(entries)
        for i in range(combo.count()):
            key = combo.itemData(i)
            label = ea.preset(key).label
            combo.setItemText(i, label if key == "all" else f"{label} ({counts.get(key, 0)})")

    # -- the view ---------------------------------------------------------
    def _apply_view(self, *_args) -> None:
        if self._pane is None:
            return
        self._pane.set_view_transform(self._build_transform())

    def _build_transform(self):
        preset_combo = self._control("preset_combo")
        within_combo = self._control("within_combo")
        text_edit = self._control("text_edit")
        group_check = self._control("group_check")
        key = preset_combo.currentData() if preset_combo is not None else "all"
        hours = WITHIN_MAP.get(within_combo.currentText()) if within_combo is not None else None
        text = text_edit.text() if text_edit is not None else ""
        grouped = bool(group_check.isChecked()) if group_check is not None else False

        def transform(entries):
            shown = ea.filter_since(entries, hours, datetime.now())
            shown = ea.apply_preset(shown, key or "all")
            shown = ea.filter_text(shown, text)
            return ea.group_entries(shown) if grouped else shown

        return transform

    def _detail(self, entry) -> str:
        if self._pane is None:
            return ""
        return detail_html(entry, self._pane.source_entries())
