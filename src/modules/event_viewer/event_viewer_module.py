"""Event Viewer as a Diagnose tab.

A triage view rather than a raw dump: preset filters for the events that
usually explain a bad machine (with live counts), a quick time window, text
filter, grouping by source + ID, and a detail panel that says what an event
means, how often it happened and what else went wrong around it. The table is
`LogPane`; the reading is `event_reader`/`event_query`; the thinking is
`event_analysis`/`event_detail` (both Qt-free).
"""
import logging
import subprocess
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QLineEdit, QPushButton

from core.admin_utils import is_admin
from core.log_reader_module import LogReaderModule
from core.table_ui import set_role

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
        #: The "how far back" choice, copied on the GUI thread whenever it
        #: changes: load_entries runs on LogPane's worker and must not touch
        #: the combo itself.
        self._hours_text = ""
        self._storm_tile = None
        self._last_storm_report: Optional[ea.StormReport] = None
        self._muted_providers: set[str] = set()
        self._isolated_provider: Optional[str] = None
        self._hide_noise: bool = False

    @property
    def muted_providers(self) -> set[str]:
        return self._muted_providers

    @property
    def isolated_provider(self) -> Optional[str]:
        return self._isolated_provider

    @property
    def hide_noise(self) -> bool:
        return self._hide_noise

    # -- pane wiring ------------------------------------------------------
    def pane_options(self) -> dict:
        from ui.glance import GlanceTile
        self._storm_tile = GlanceTile(
            "Storm Detector",
            severity="ok",
            tooltip="Monitors for runaway event storms (>30 events/minute). Click to inspect.",
        )
        self._storm_tile.clicked.connect(self.open_storm_detector)
        return {
            "extra_columns": ["ID", "Count"],
            "extra_values": ea.row_values,
            "detail_enricher": self._detail,
            "summarizer": self._summary,
            "copy_formatter": ea.entry_as_text,
            "read_problems": lambda: self._notes,
            "glance_tiles": [self._storm_tile],
        }

    def build_controls(self, toolbar, extra) -> None:
        toolbar.addWidget(QLabel("Load:"))
        combo = QComboBox()
        combo.addItems(list(HOURS_MAP))
        combo.setCurrentIndex(3)  # Default: 24 hours
        combo.setToolTip("How far back to read. Changing it needs a Refresh; each log is capped at 5,000 events, so a longer range may still be incomplete. This does not change logging or clear records; Security reads require administrator rights.")
        toolbar.addWidget(combo)
        extra["hours_combo"] = combo
        self._hours_text = combo.currentText()
        combo.currentTextChanged.connect(self._remember_hours)

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
        within.setToolTip("Narrow what is already loaded to a recent window. This does not read older events or change Windows; choose All loaded to remove this time filter.")
        toolbar.addWidget(within)
        extra["within_combo"] = within

        text = QLineEdit()
        text.setPlaceholderText("Filter source, ID or text")
        text.setClearButtonEnabled(True)
        text.setMinimumWidth(180)
        toolbar.addWidget(text)
        extra["text_edit"] = text

        group = QCheckBox("Group by source + ID")
        group.setToolTip("One row per distinct event with its count (click the Count header to put the most frequent first). Grouping combines the same provider and event ID even if message details differ; uncheck to see individual loaded events. No Windows logs change.")
        toolbar.addWidget(group)
        extra["group_check"] = group

        noise_check = QCheckBox("Quick Noise Filter")
        noise_check.setToolTip("Hide known benign background noise (DCOM 10016, benign Kerberos 4768, routine time syncs).")
        toolbar.addWidget(noise_check)
        extra["noise_check"] = noise_check

        storm_btn = QPushButton("⚡ Storms (0)")
        storm_btn.setToolTip("Storm Detector: analyze event storms (>30 events/min), runaway bursts, and top noisy providers/event IDs.")
        storm_btn.clicked.connect(self.open_storm_detector)
        toolbar.addWidget(storm_btn)
        extra["storm_btn"] = storm_btn

        badge_btn = QPushButton("")
        badge_btn.setVisible(False)
        badge_btn.setToolTip("Click to clear muted or isolated provider filters.")
        badge_btn.clicked.connect(self.clear_provider_filters)
        toolbar.addWidget(badge_btn)
        extra["provider_filter_badge"] = badge_btn

        ev_btn = QPushButton("Open eventvwr")
        ev_btn.setToolTip("Launch native Windows Event Viewer (eventvwr.msc)")
        ev_btn.clicked.connect(lambda: subprocess.Popen(["eventvwr.msc"], shell=True))
        toolbar.addWidget(ev_btn)
        extra["eventvwr_btn"] = ev_btn

        presets.currentIndexChanged.connect(self._apply_view)
        within.currentIndexChanged.connect(self._apply_view)
        text.textChanged.connect(self._apply_view)
        group.toggled.connect(self._apply_view)
        noise_check.toggled.connect(self._on_noise_filter_toggled)

    def _remember_hours(self, text: str) -> None:
        self._hours_text = text

    def _control(self, key: str):
        if self._pane is None:
            return None
        return self._pane.extra.get(key)

    # -- loading ----------------------------------------------------------
    def load_entries(self, worker):
        # Runs on LogPane's worker thread: it reads the copy the GUI thread
        # keeps (`_remember_hours`), never the combo.
        hours_back = 24
        text = self._hours_text
        if text:
            hours_back = HOURS_MAP.get(text, 24)
            self._range_label = text.replace("1 hour", "last hour")
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
        report = ea.detect_event_storms(entries)
        self._last_storm_report = report
        self._update_storm_indicator(report)
        return ea.summary_text(entries, self._notes, self._range_label)

    def _update_storm_indicator(self, report: ea.StormReport) -> None:
        if self._storm_tile is not None:
            if report.has_storm:
                storm_count = len(report.storms)
                peak_rate = report.storms[0].peak_burst_count
                self._storm_tile.set(
                    f"{storm_count} storm{'s' if storm_count > 1 else ''}",
                    f"Peak: {peak_rate} ev/60s",
                    severity="danger",
                    tooltip=f"{storm_count} runaway provider(s) generating bursts (>30 events/min). Click to inspect.",
                )
            else:
                self._storm_tile.set(
                    "0 storms",
                    "No bursts detected",
                    severity="ok",
                    tooltip="No event storms (>30 events/minute) detected. Click to inspect top noisy providers.",
                )

        storm_btn = self._control("storm_btn")
        if storm_btn is not None:
            if report.has_storm:
                storm_btn.setText(f"⚡ Storms ({len(report.storms)})")
                set_role(storm_btn, "statusError")
            else:
                storm_btn.setText("⚡ Storms (0)")
                set_role(storm_btn, "")

    def _refresh_preset_counts(self, entries) -> None:
        combo = self._control("preset_combo")
        if combo is None:
            return
        counts = ea.preset_counts(entries)
        for i in range(combo.count()):
            key = combo.itemData(i)
            label = ea.preset(key).label
            combo.setItemText(i, label if key == "all" else f"{label} ({counts.get(key, 0)})")

    # -- Provider Muting, Isolating & Noise Actions -----------------------
    def mute_provider(self, provider: str) -> None:
        """Temporarily mute (exclude) a provider from the view."""
        if not provider:
            return
        self._muted_providers.add(provider)
        if self._isolated_provider and self._isolated_provider.lower() == provider.lower():
            self._isolated_provider = None
        self._update_provider_filter_badge()
        self._apply_view()

    def unmute_provider(self, provider: str) -> None:
        """Unmute a previously muted provider."""
        to_remove = {m for m in self._muted_providers if m.lower() == provider.lower()}
        self._muted_providers -= to_remove
        self._update_provider_filter_badge()
        self._apply_view()

    def isolate_provider(self, provider: str) -> None:
        """Isolate a provider (show ONLY this provider in the view)."""
        if not provider:
            self._isolated_provider = None
        else:
            self._isolated_provider = provider
            to_remove = {m for m in self._muted_providers if m.lower() == provider.lower()}
            self._muted_providers -= to_remove
        self._update_provider_filter_badge()
        self._apply_view()

    def clear_provider_filters(self) -> None:
        """Clear all muted and isolated provider filters."""
        self._muted_providers.clear()
        self._isolated_provider = None
        self._update_provider_filter_badge()
        self._apply_view()

    def set_noise_filter(self, hide: bool) -> None:
        """Enable or disable hiding benign background noise."""
        self._hide_noise = bool(hide)
        check = self._control("noise_check")
        if check is not None and check.isChecked() != self._hide_noise:
            check.setChecked(self._hide_noise)
        self._apply_view()

    def _on_noise_filter_toggled(self, checked: bool) -> None:
        self._hide_noise = bool(checked)
        self._apply_view()

    def _update_provider_filter_badge(self) -> None:
        badge = self._control("provider_filter_badge")
        if badge is None:
            return
        if self._isolated_provider:
            badge.setText(f"🎯 Isolated: {self._isolated_provider} [✕]")
            badge.setVisible(True)
        elif self._muted_providers:
            badge.setText(f"🔇 Muted: {len(self._muted_providers)} [✕]")
            badge.setVisible(True)
        else:
            badge.setVisible(False)

    def open_storm_detector(self) -> None:
        """Launch the Storm Detector dialog listing runaway storms and top noisy providers/IDs."""
        from modules.event_viewer.storm_detector_dialog import StormDetectorDialog
        entries = self._pane.source_entries() if self._pane else []
        report = self._last_storm_report or ea.detect_event_storms(entries)
        dlg = StormDetectorDialog(report=report, module=self, parent=self._pane)
        dlg.exec()

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
        hide_noise = self._hide_noise
        muted = set(self._muted_providers)
        isolated = self._isolated_provider

        def transform(entries):
            shown = ea.filter_since(entries, hours, datetime.now())
            shown = ea.apply_preset(shown, key or "all")
            if hide_noise:
                shown = ea.filter_noise(shown, hide_noise=True)
            if isolated or muted:
                shown = ea.filter_providers(shown, muted=muted, isolated=isolated)
            shown = ea.filter_text(shown, text)
            return ea.group_entries(shown) if grouped else shown

        return transform

    def _detail(self, entry) -> str:
        if self._pane is None:
            return ""
        return detail_html(entry, self._pane.source_entries())
