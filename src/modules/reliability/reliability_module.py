"""Reliability as a Diagnose tab.

The records behind Windows' Reliability Monitor, classified by what happened
(not by keywords), with the stability index for the hour of the selected event,
a summary of the biggest drops and what was stamped in them, and a sparkline
of the whole loaded index history next to Refresh (`stability_chart.py`) --
the text summary states the single biggest one-hour drop, which understates a
slow multi-day decline (measured real: 10.0 down to 4.4 over three weeks on
this machine). Everything specific to Reliability is
`reliability_reader`/`reliability_analysis`; the UI is `LogPane` by way of
`LogReaderModule`.
"""
import logging

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

    def pane_options(self) -> dict:
        self._stability_tile = GlanceTile("Stability index", tooltip="Windows' measured stability index from 0 to 10. The chart shows the loaded hourly history on that fixed scale.")
        self._stability_tile.set_unreadable("Not read yet")
        self._stability_chart = StabilityChart()
        self._stability_tile.layout().addWidget(self._stability_chart)
        self._stability_tile.clicked.connect(self._show_stability_details)
        return {
            "detail_enricher": lambda entry: ra.detail_html(entry, self._metrics),
            "summarizer": self._summary,
            "glance_tiles": [self._stability_tile],
        }

    def build_controls(self, toolbar, extra) -> None:
        extra["stability_chart"] = self._stability_chart

    def _show_stability_details(self) -> None:
        if self._pane is not None:
            self._pane._note_btn.setChecked(True)

    def _control(self, key: str):
        if self._pane is None:
            return None
        return self._pane.extra.get(key)

    def _summary(self, entries) -> str:
        chart = self._control("stability_chart")
        if chart is not None:
            chart.set_metrics(self._metrics)
        latest = ra.latest_index(self._metrics)
        if latest is None:
            self._stability_tile.set_unreadable(" ".join(self._problems) or "No stability index history was returned")
        else:
            self._stability_tile.set(f"{latest.index:.1f} / 10", f"Measured {latest.start:%d %b %H:%M}",
                                     tooltip=ra.sparkline_tooltip(self._metrics),
                                     severity="ok" if latest.index >= 7 else "warning" if latest.index >= 4 else "danger")
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
