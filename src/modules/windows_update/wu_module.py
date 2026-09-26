"""Windows Update as a Diagnose tab.

Everything specific to Windows Update is its parser, its analysis and its search
provider; the UI is `LogPane` by way of `LogReaderModule`. The strip above the
table names each failing update with its error code translated.
"""
import logging
import os

from core.log_reader_module import LogReaderModule
from core.windows_utils import system_root

from modules.windows_update import wu_analysis
from modules.windows_update.wu_parser import WUParser
from modules.windows_update.wu_search_provider import WUSearchProvider

logger = logging.getLogger(__name__)

WU_LOG_PATH = os.path.join(system_root(), "SoftwareDistribution", "ReportingEvents.log")


class WindowsUpdateModule(LogReaderModule):
    name = "Windows Update"
    icon = "🪟"
    description = "Windows Update reporting events"
    requires_admin = False
    provider_class = WUSearchProvider

    def pane_options(self) -> dict:
        return {
            "detail_enricher": wu_analysis.detail_html,
            "summarizer": wu_analysis.summary_text,
        }

    def load_entries(self, worker):
        parser = WUParser(WU_LOG_PATH)
        if not parser.file_exists():
            # A log that is not there is a different answer from an empty one.
            raise FileNotFoundError(f"{WU_LOG_PATH} does not exist on this machine")
        return parser.parse(
            progress_callback=lambda p: worker.signals.progress.emit(p)
        )
