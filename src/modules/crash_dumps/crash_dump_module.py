"""Crash Dumps as a Diagnose tab.

Lists kernel dumps, live kernel reports and BugCheck events, each with its
bugcheck code named and its usual cause explained. Everything specific to
Crash Dumps is in `crash_dump_reader` and `dump_parser`; the UI is `LogPane`
by way of `LogReaderModule`.
"""
import logging

from core.log_reader_module import LogReaderModule

from modules.crash_dumps.crash_dump_reader import detail_html, read_crash_evidence, summary_text
from modules.crash_dumps.crash_dump_search_provider import CrashDumpSearchProvider

logger = logging.getLogger(__name__)


class CrashDumpModule(LogReaderModule):
    name = "Crash Dumps"
    icon = "💥"
    description = "Application and system crash dumps"
    requires_admin = True
    provider_class = CrashDumpSearchProvider

    def __init__(self) -> None:
        super().__init__()
        self._notes: list = []

    def pane_options(self) -> dict:
        return {
            "detail_enricher": detail_html,
            "summarizer": lambda entries: summary_text(entries, self._notes),
            "empty_text": "No crash dumps or bugcheck events found",
        }

    def load_entries(self, worker):
        entries, notes = read_crash_evidence(
            progress_callback=lambda p: worker.signals.progress.emit(p)
        )
        self._notes = notes
        return entries
