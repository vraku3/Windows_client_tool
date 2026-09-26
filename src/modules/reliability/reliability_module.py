"""Reliability as a Diagnose tab.

The records behind Windows' Reliability Monitor, classified by what happened
(not by keywords), with the stability index for the hour of the selected event
and a summary of the biggest drops and what was stamped in them. Everything
specific to Reliability is `reliability_reader`/`reliability_analysis`; the UI
is `LogPane` by way of `LogReaderModule`.
"""
import logging

from core.log_reader_module import LogReaderModule

from modules.reliability import reliability_analysis as ra
from modules.reliability.reliability_reader import read_reliability
from modules.reliability.reliability_search_provider import ReliabilitySearchProvider

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
        return {
            "detail_enricher": lambda entry: ra.detail_html(entry, self._metrics),
            "summarizer": lambda entries: ra.summary_text(self._metrics, entries, self._problems),
        }

    def load_entries(self, worker):
        entries, metrics, problems = read_reliability(
            max_records=1000,
            progress_callback=lambda p: worker.signals.progress.emit(p),
        )
        self._metrics = metrics or []
        self._problems = problems
        return entries
