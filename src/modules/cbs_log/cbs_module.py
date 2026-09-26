"""CBS Log as a Diagnose tab.

Everything specific to CBS Log is its parser and its search provider; the UI is
`LogPane` by way of `LogReaderModule`.

Windows 11 usually ships no plain CBS.log, so the CbsPersist cab fallback
in load_entries is the path that actually runs.
"""
import logging

import os

from core.log_reader_module import LogReaderModule
from core.windows_utils import program_files, system_root

from modules.cbs_log.cbs_parser import CBSParser
from modules.cbs_log.cbs_search_provider import CBSSearchProvider

logger = logging.getLogger(__name__)

CBS_LOG_PATH = os.path.join(system_root(), "Logs", "CBS", "CBS.log")


class CBSLogModule(LogReaderModule):
    name = "CBS Log"
    icon = "📝"
    description = "Component-Based Servicing log parser"
    requires_admin = False
    provider_class = CBSSearchProvider

    def pane_options(self) -> dict:
        from modules.diagnose import servicing_summary as ss
        return {
            "detail_enricher": ss.detail_html,
            "summarizer": lambda entries: ss.summarize(entries, "cbs"),
        }

    def load_entries(self, worker):
        import os
        import subprocess
        import tempfile

        # Windows 11 stores CBS in .cab files under C:\Windows\Logs\CBS\
        if not os.path.exists(CBS_LOG_PATH):
            logger.info("CBS.log not found — trying to extract from cab archive")
            # Find the most recent CBS cab file
            cab_dir = os.path.dirname(CBS_LOG_PATH)
            cab_files = []
            try:
                for f in os.listdir(cab_dir):
                    if f.startswith("CbsPersist_") and f.endswith(".cab"):
                        cab_files.append(os.path.join(cab_dir, f))
            except OSError:
                logger.debug("Ignored OSError", exc_info=True)

            if not cab_files:
                logger.warning("No CBS cab files found in %s", cab_dir)
                return []

            cab_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            latest_cab = cab_files[0]

            # Try 7z if available
            seven_zip = os.path.join(program_files(), "7-Zip", "7z.exe")
            if not os.path.exists(seven_zip):
                seven_zip = os.path.join(program_files(x86=True), "7-Zip", "7z.exe")

            tmp_dir = tempfile.mkdtemp(prefix="cbs_")
            log_path = os.path.join(tmp_dir, "CBS_extracted.log")

            if os.path.exists(seven_zip):
                try:
                    subprocess.run(
                        [seven_zip, "e", latest_cab, f"-o{tmp_dir}", "-y"],
                        capture_output=True, timeout=30,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception as ex:
                    logger.warning("7z extraction failed: %s", ex)

            if os.path.exists(log_path):
                parser = CBSParser(log_path)
                worker.signals.progress.emit(50)
                entries = parser.parse(
                    progress_callback=lambda p: worker.signals.progress.emit(50 + p // 2)
                )
                # Clean up temp file
                try:
                    os.unlink(log_path)
                    os.rmdir(tmp_dir)
                except Exception as e:
                    logger.debug("Could not clean up temp files: %s", e)
                return entries
            else:
                logger.warning("Could not extract CBS log from cab: %s", latest_cab)
                return []

        parser = CBSParser(CBS_LOG_PATH)
        return parser.parse(
            progress_callback=lambda p: worker.signals.progress.emit(p)
        )