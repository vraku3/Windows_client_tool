"""DISM Log as a Diagnose tab.

Everything specific to DISM Log is its parser and its search provider; the UI is
`LogPane` by way of `LogReaderModule`.

Falls back to Get-HotFix when there is no DISM.log to read.
"""
import logging

import os

from core.log_reader_module import LogReaderModule
from core.windows_utils import system_root

from modules.dism_log.dism_parser import DISMParser
from modules.dism_log.dism_search_provider import DISMSearchProvider

logger = logging.getLogger(__name__)

DISM_LOG_PATH = os.path.join(system_root(), "Logs", "DISM", "dism.log")


class DISMLogModule(LogReaderModule):
    name = "DISM Log"
    icon = "🔧"
    description = "DISM servicing log parser"
    requires_admin = False
    provider_class = DISMSearchProvider

    def pane_options(self) -> dict:
        from core import servicing_summary as ss

        def summarise(entries):
            if entries and all(e.source == "DISM/HotFix" for e in entries):
                return ("dism.log does not exist on this machine, so this shows installed "
                        "hotfixes (Get-HotFix) instead; there is no servicing log to summarise.")
            return ss.summarize(entries, "dism")

        return {"detail_enricher": ss.detail_html, "summarizer": summarise}

    def load_entries(self, worker):
        import os
        import subprocess

        if not os.path.exists(DISM_LOG_PATH):
            # DISM text log not found — try DISM API via PowerShell (non-admin: get hotfixes)
            logger.info("DISM.log not found — using Get-HotFix as fallback")
            ps_script = (
                "Get-HotFix | Sort-Object InstalledOn -Descending | "
                "Select-Object HotFixID,Description,InstalledOn,Caption | "
                "ConvertTo-Json -Compress -Depth 2"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            raw = result.stdout.strip()
            if not raw:
                return []
            import json
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    data = [data]
                # Convert to LogEntry format
                entries = []
                from core.types import LogEntry
                from datetime import datetime
                for entry in data:
                    installed_on = entry.get("InstalledOn", {})
                    ts_str = ""
                    if isinstance(installed_on, dict):
                        ts_str = installed_on.get("DateTime", "")
                    elif isinstance(installed_on, str):
                        ts_str = installed_on
                    ts = datetime.now()
                    for fmt in ("%A, %B %d, %Y %H:%M:%S", "%m/%d/%Y", "%Y-%m-%d"):
                        try:
                            ts = datetime.strptime(ts_str.split()[0], fmt)
                            break
                        except Exception as e:
                            logger.debug("Could not parse timestamp '%s': %s", ts_str, e)
                    desc = str(entry.get("Description", ""))
                    kb = str(entry.get("HotFixID", ""))
                    entries.append(LogEntry(
                        timestamp=ts,
                        source="DISM/HotFix",
                        level="Info",
                        message=f"{kb} — {desc}",
                        raw=entry,
                    ))
                return entries
            except Exception as ex:
                logger.warning("Failed to parse Get-HotFix output: %s", ex)
                return []

        parser = DISMParser(DISM_LOG_PATH)
        return parser.parse(
            progress_callback=lambda p: worker.signals.progress.emit(p)
        )