"""Storm Detector dialog for inspecting runaway event bursts and noisy providers."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.semantic_colors import chrome, semantic
from core.table_ui import set_role

if TYPE_CHECKING:
    from modules.event_viewer.event_analysis import StormReport
    from modules.event_viewer.event_viewer_module import EventViewerModule

logger = logging.getLogger(__name__)


class StormDetectorDialog(QDialog):
    """Dialog displaying event storms, runaway bursts (>30 events/min), and top noisy providers."""

    def __init__(
        self,
        report: StormReport,
        module: EventViewerModule,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.module = module

        self.setWindowTitle("Storm Detector & Noise Intelligence")
        self.resize(760, 560)
        self.setMinimumSize(600, 420)

        self._setup_ui()
        self._refresh_status()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        # ── Header Banner ────────────────────────────────────────────────
        header_card = QFrame()
        header_card.setObjectName("card")
        hl = QVBoxLayout(header_card)
        hl.setSpacing(4)
        hl.setContentsMargins(12, 10, 12, 10)

        has_storm = self.report.has_storm
        storm_count = len(self.report.storms)

        if has_storm:
            status_color = semantic("error")
            status_title = f"🚨 {storm_count} Event Storm{'s' if storm_count > 1 else ''} Detected (>30 events/min)"
            status_desc = (
                f"Identified {storm_count} runaway provider(s) generating severe event bursts. "
                "These bursts can flood event logs, degrade I/O performance, and obscure genuine faults."
            )
        else:
            status_color = semantic("success")
            status_title = "✓ No Active Event Storms"
            status_desc = (
                "All event providers are operating within normal rate thresholds (<= 30 events/minute). "
                "Review top noisy providers below if you want to declutter the triage view."
            )

        title_lbl = QLabel(f"<b style='font-size:15px; color:{status_color};'>{status_title}</b>")
        desc_lbl = QLabel(status_desc)
        set_role(desc_lbl, "muted")
        desc_lbl.setWordWrap(True)

        hl.addWidget(title_lbl)
        hl.addWidget(desc_lbl)
        layout.addWidget(header_card)

        # ── Tabs ─────────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # Tab 1: Runaway Storms
        storm_tab = QWidget()
        stl = QVBoxLayout(storm_tab)
        stl.setContentsMargins(4, 8, 4, 4)
        stl.setSpacing(8)

        self.storm_table = self._create_table(["Provider", "Peak Burst (60s)", "Rate (/min)", "Total Events", "Top Event ID", "Actions"])
        self._populate_storms_table()
        stl.addWidget(self.storm_table)

        tab1_label = f"⚡ Active Storms ({storm_count})" if has_storm else "⚡ Active Storms (0)"
        self.tabs.addTab(storm_tab, tab1_label)

        # Tab 2: Top Noisy Providers
        noisy_prov_tab = QWidget()
        npl = QVBoxLayout(noisy_prov_tab)
        npl.setContentsMargins(4, 8, 4, 4)
        npl.setSpacing(8)

        self.prov_table = self._create_table(["Provider", "Event Count", "% of Loaded", "Actions"])
        self._populate_providers_table()
        npl.addWidget(self.prov_table)
        self.tabs.addTab(noisy_prov_tab, f"📢 Top Noisy Providers ({len(self.report.noisy_providers)})")

        # Tab 3: Top Noisy Event IDs
        noisy_id_tab = QWidget()
        nil = QVBoxLayout(noisy_id_tab)
        nil.setContentsMargins(4, 8, 4, 4)
        nil.setSpacing(8)

        self.id_table = self._create_table(["Provider", "Event ID", "Count", "Actions"])
        self._populate_ids_table()
        nil.addWidget(self.id_table)
        self.tabs.addTab(noisy_id_tab, f"🔢 Top Event IDs ({len(self.report.noisy_event_ids)})")

        # ── Bottom Status & Action Bar ───────────────────────────────────
        bottom_bar = QFrame()
        bottom_bar.setObjectName("card")
        bl = QHBoxLayout(bottom_bar)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.setSpacing(10)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        bl.addWidget(self.status_lbl, 1)

        unmute_btn = QPushButton("🔇 Unmute All")
        unmute_btn.setToolTip("Unmute all temporarily muted providers.")
        unmute_btn.clicked.connect(self._unmute_all)
        bl.addWidget(unmute_btn)

        show_all_btn = QPushButton("🎯 Clear All Filters")
        show_all_btn.setToolTip("Clear all provider isolation and mute filters.")
        show_all_btn.clicked.connect(self._clear_all_filters)
        bl.addWidget(show_all_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bl.addWidget(close_btn)

        layout.addWidget(bottom_bar)

    def _create_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        return table

    def _populate_storms_table(self) -> None:
        self.storm_table.setRowCount(0)
        if not self.report.storms:
            self.storm_table.setRowCount(1)
            item = QTableWidgetItem("No providers exceeded >30 events/minute threshold.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.storm_table.setItem(0, 0, item)
            self.storm_table.setSpan(0, 0, 1, self.storm_table.columnCount())
            return

        self.storm_table.setRowCount(len(self.report.storms))
        for row, storm in enumerate(self.report.storms):
            p_item = QTableWidgetItem(storm.provider)
            p_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            self.storm_table.setItem(row, 0, p_item)

            burst_item = QTableWidgetItem(f"{storm.peak_burst_count} ev/60s")
            self.storm_table.setItem(row, 1, burst_item)

            rate_item = QTableWidgetItem(f"{storm.rate_per_minute:.1f}/min")
            self.storm_table.setItem(row, 2, rate_item)

            total_item = QTableWidgetItem(f"{storm.total_count:,}")
            self.storm_table.setItem(row, 3, total_item)

            top_id_str = f"ID {storm.event_id}" if storm.event_id is not None else "—"
            id_item = QTableWidgetItem(top_id_str)
            self.storm_table.setItem(row, 4, id_item)

            act_widget = self._make_action_widget(storm.provider)
            self.storm_table.setCellWidget(row, 5, act_widget)

        self.storm_table.resizeColumnsToContents()

    def _populate_providers_table(self) -> None:
        self.prov_table.setRowCount(0)
        total_events = sum(cnt for _, cnt in self.report.noisy_providers) or 1

        self.prov_table.setRowCount(len(self.report.noisy_providers))
        for row, (prov, count) in enumerate(self.report.noisy_providers):
            p_item = QTableWidgetItem(prov)
            self.prov_table.setItem(row, 0, p_item)

            c_item = QTableWidgetItem(f"{count:,}")
            self.prov_table.setItem(row, 1, c_item)

            pct = (count / total_events) * 100.0
            pct_item = QTableWidgetItem(f"{pct:.1f}%")
            self.prov_table.setItem(row, 2, pct_item)

            act_widget = self._make_action_widget(prov)
            self.prov_table.setCellWidget(row, 3, act_widget)

        self.prov_table.resizeColumnsToContents()

    def _populate_ids_table(self) -> None:
        self.id_table.setRowCount(0)
        self.id_table.setRowCount(len(self.report.noisy_event_ids))
        for row, (prov, eid, count) in enumerate(self.report.noisy_event_ids):
            p_item = QTableWidgetItem(prov)
            self.id_table.setItem(row, 0, p_item)

            eid_str = str(eid) if eid is not None else "—"
            id_item = QTableWidgetItem(eid_str)
            self.id_table.setItem(row, 1, id_item)

            c_item = QTableWidgetItem(f"{count:,}")
            self.id_table.setItem(row, 2, c_item)

            act_widget = self._make_action_widget(prov, eid=eid)
            self.id_table.setCellWidget(row, 3, act_widget)

        self.id_table.resizeColumnsToContents()

    def _make_action_widget(self, provider: str, eid: Optional[int] = None) -> QWidget:
        container = QWidget()
        hl = QHBoxLayout(container)
        hl.setContentsMargins(2, 2, 2, 2)
        hl.setSpacing(4)

        is_muted = provider.lower() in {m.lower() for m in self.module.muted_providers}
        is_isolated = (self.module.isolated_provider or "").lower() == provider.lower()

        mute_btn = QPushButton("🔇 Unmute" if is_muted else "🔇 Mute")
        mute_btn.setMaximumWidth(75)
        mute_btn.setToolTip(f"Unmute {provider}" if is_muted else f"Temporarily hide events from {provider}")
        if is_muted:
            mute_btn.clicked.connect(lambda: self._unmute_provider(provider))
        else:
            mute_btn.clicked.connect(lambda: self._mute_provider(provider))
        hl.addWidget(mute_btn)

        iso_btn = QPushButton("🎯 Unisolate" if is_isolated else "🎯 Isolate")
        iso_btn.setMaximumWidth(80)
        iso_btn.setToolTip(f"Show all events again" if is_isolated else f"Show ONLY events from {provider}")
        if is_isolated:
            iso_btn.clicked.connect(self._clear_isolation)
        else:
            iso_btn.clicked.connect(lambda: self._isolate_provider(provider))
        hl.addWidget(iso_btn)

        return container

    def _mute_provider(self, provider: str) -> None:
        self.module.mute_provider(provider)
        self._refresh_all_tables()
        self._refresh_status()

    def _unmute_provider(self, provider: str) -> None:
        self.module.unmute_provider(provider)
        self._refresh_all_tables()
        self._refresh_status()

    def _isolate_provider(self, provider: str) -> None:
        self.module.isolate_provider(provider)
        self._refresh_all_tables()
        self._refresh_status()

    def _clear_isolation(self) -> None:
        self.module.isolate_provider("")
        self._refresh_all_tables()
        self._refresh_status()

    def _unmute_all(self) -> None:
        for prov in list(self.module.muted_providers):
            self.module.unmute_provider(prov)
        self._refresh_all_tables()
        self._refresh_status()

    def _clear_all_filters(self) -> None:
        self.module.clear_provider_filters()
        self._refresh_all_tables()
        self._refresh_status()

    def _refresh_all_tables(self) -> None:
        self._populate_storms_table()
        self._populate_providers_table()
        self._populate_ids_table()

    def _refresh_status(self) -> None:
        parts = []
        if self.module.isolated_provider:
            parts.append(f"<b>Isolated:</b> <span style='color:{semantic('info')};'>{self.module.isolated_provider}</span>")
        if self.module.muted_providers:
            muted_str = ", ".join(sorted(self.module.muted_providers))
            parts.append(f"<b>Muted ({len(self.module.muted_providers)}):</b> <span style='color:{chrome('text_muted')};'>{muted_str}</span>")
        if getattr(self.module, "hide_noise", False):
            parts.append("<b>Noise Filter:</b> Active")

        if parts:
            self.status_lbl.setText(" | ".join(parts))
        else:
            self.status_lbl.setText("<span style='color:" + chrome("text_muted") + ";'>No provider filters active (all events shown).</span>")
