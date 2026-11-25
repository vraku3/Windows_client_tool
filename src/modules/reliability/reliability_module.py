"""Reliability as a Diagnose tab.

The records behind Windows' Reliability Monitor, classified by what happened
(not by keywords), with the stability index for the hour of the selected event,
a summary of the biggest drops and what was stamped in them, a sparkline
of the whole loaded index history next to Refresh (`stability_chart.py`),
30-day stability trend analysis, failure bursts/clustering, and MTBF health metrics.
"""
from __future__ import annotations

import logging
import subprocess

from PyQt6.QtWidgets import QPushButton

from core.log_reader_module import LogReaderModule
from ui.glance import GlanceTile

from modules.reliability import reliability_analysis as ra
from modules.reliability.reliability_reader import read_reliability
from modules.reliability.reliability_search_provider import ReliabilitySearchProvider
from modules.reliability.stability_chart import StabilityChart

logger = logging.getLogger(__name__)


class ReliabilityModule(LogReaderModule):
    name = "Reliability"
    icon = "📊"
    description = "Windows reliability records"
    requires_admin = False
    provider_class = ReliabilitySearchProvider

    def __init__(self) -> None:
        super().__init__()
        self._metrics: list = []
        self._problems: list = []
        self._stability_tile: Optional[GlanceTile] = None
        self._trend_tile: Optional[GlanceTile] = None
        self._mtbf_tile: Optional[GlanceTile] = None
        self._cluster_tile: Optional[GlanceTile] = None
        self._stability_chart: Optional[StabilityChart] = None

    def pane_options(self) -> dict:
        self._stability_tile = GlanceTile(
            "Stability index",
            tooltip="Windows' measured stability index from 0 to 10. The chart shows the loaded hourly history on that fixed scale.",
        )
        self._trend_tile = GlanceTile(
            "30-Day Trend",
            tooltip="System Stability Index trajectory over the past 30 days (Improving / Declining / Stable).",
        )
        self._mtbf_tile = GlanceTile(
            "MTBF",
            tooltip="Mean Time Between Failures across system crashes, OS faults, and hardware errors.",
        )
        self._cluster_tile = GlanceTile(
            "Failure Clusters",
            tooltip="Bursts of rapid failures and stability drops detected in the timeline.",
        )
        self._stability_tile.set_unreadable("Not read yet")
        self._trend_tile.set_unreadable("Not read yet")
        self._mtbf_tile.set_unreadable("Not read yet")
        self._cluster_tile.set_unreadable("Not read yet")

        self._stability_chart = StabilityChart()
        self._stability_tile.layout().addWidget(self._stability_chart)
        self._stability_tile.clicked.connect(self._show_stability_details)

        return {
            "detail_enricher": lambda entry: ra.detail_html(entry, self._metrics),
            "summarizer": self._summary,
            "glance_tiles": [
                self._stability_tile,
                self._trend_tile,
                self._mtbf_tile,
                self._cluster_tile,
            ],
        }

    def build_controls(self, toolbar, extra) -> None:
        extra["stability_chart"] = self._stability_chart
        rel_btn = QPushButton("Open Reliability Monitor")
        rel_btn.setToolTip("Launch native Windows Reliability Monitor (perfmon /rel)")
        rel_btn.clicked.connect(lambda: subprocess.Popen(["perfmon.exe", "/rel"], shell=True))
        toolbar.addWidget(rel_btn)
        extra["rel_btn"] = rel_btn

    def _show_stability_details(self) -> None:
        if self._pane is not None:
            self._pane._note_btn.setChecked(True)

    def _control(self, key: str):
        if self._pane is None:
            return None
        return self._pane.extra.get(key)

    def _summary(self, entries) -> str:
        if self._stability_tile is None or self._trend_tile is None or self._mtbf_tile is None or self._cluster_tile is None:
            return ra.summary_text(entries)
        chart = self._control("stability_chart")
        if chart is not None:
            chart.set_metrics(self._metrics)
        latest = ra.latest_index(self._metrics)

        # 1. Stability Tile
        if latest is None:
            self._stability_tile.set_unreadable(" ".join(self._problems) or "No stability index history was returned")
            self._trend_tile.set_unreadable("No stability history")
        else:
            self._stability_tile.set(
                f"{latest.index:.1f} / 10",
                f"Measured {latest.start:%d %b %H:%M}",
                tooltip=ra.sparkline_tooltip(self._metrics),
                severity="ok" if latest.index >= 7 else "warning" if latest.index >= 4 else "danger",
            )

            # 2. 30-Day Trend Tile
            trend = ra.calculate_stability_trend(self._metrics, days=30)
            if trend.history:
                self._stability_tile.set_history(trend.history)
            trend_sev = (
                "ok"
                if trend.direction == "improving"
                else (
                    "danger"
                    if trend.direction == "declining" and (trend.delta or 0) <= -1.0
                    else "warning"
                    if trend.direction == "declining"
                    else "ok"
                )
            )
            self._trend_tile.set(
                trend.direction.title(),
                sub=trend.description,
                severity=trend_sev,
            )

        # 3. MTBF Tile
        mtbf = ra.calculate_mtbf(entries, days=30)
        has_failures = any(ra.is_failure_event(e) for e in entries)
        if mtbf is not None:
            mtbf_sev = "ok" if mtbf >= 48.0 else ("warning" if mtbf >= 12.0 else "danger")
            self._mtbf_tile.set(
                ra.format_mtbf(mtbf),
                sub="Mean time between failures (30d)",
                severity=mtbf_sev,
            )
        else:
            self._mtbf_tile.set(
                "No Failures" if not has_failures else "—",
                sub="0 or 1 failure in window" if has_failures else "100% operational in period",
                severity="ok" if not has_failures else "muted",
            )

        # 4. Failure Clusters Tile
        clusters = ra.detect_failure_clusters(entries)
        dips = ra.find_dips(self._metrics, entries)
        c_sev = "ok" if len(clusters) == 0 else ("warning" if len(clusters) <= 2 else "danger")
        self._cluster_tile.set(
            f"{len(clusters)} burst{'s' if len(clusters) != 1 else ''}",
            sub=f"{len(dips)} stability drop{'s' if len(dips) != 1 else ''}",
            severity=c_sev,
        )

        return ra.summary_text(self._metrics, entries, self._problems)

    def load_entries(self, worker):
        entries, metrics, problems = read_reliability(
            max_records=1000,
            progress_callback=lambda p: worker.signals.progress.emit(p),
        )
        self._metrics = metrics or []
        self._problems = problems
        from core.types import LogReadResult
        return LogReadResult(entries, problems)
