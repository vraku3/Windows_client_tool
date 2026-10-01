"""DISM Log as a Diagnose tab.

Everything specific to DISM Log is its parser and its search provider; the UI is
`LogPane` by way of `LogReaderModule`.

Falls back to Get-HotFix when there is no DISM.log to read.

Also offers a "Check Component Store Health" button -- DISM /CheckHealth,
read-only and fast (measured ~120ms elevated, ~30ms refused unelevated on
this machine; see `checkhealth.py`), distinct from System Health's slower
ScanHealth/RestoreHealth. It belongs here rather than there because it
answers the exact question this tab's own log is about, and it found real
damage on this dev machine that nothing else in the app currently surfaces.
"""
import logging

import os

from PyQt6.QtWidgets import QMessageBox, QPushButton

from core.log_reader_module import LogReaderModule
from core.widget_life import widget_is_valid
from core.windows_utils import system_root
from core.worker import Worker

from modules.dism_log import checkhealth
from modules.dism_log.dism_parser import DISMParser
from modules.dism_log.dism_search_provider import DISMSearchProvider

logger = logging.getLogger(__name__)

DISM_LOG_PATH = os.path.join(system_root(), "Logs", "DISM", "dism.log")

_ICON_FOR_VERDICT = {
    "healthy": QMessageBox.Icon.Information,
    "repairable": QMessageBox.Icon.Warning,
    "unrepairable": QMessageBox.Icon.Critical,
    "refused": QMessageBox.Icon.Warning,
    "unknown": QMessageBox.Icon.Warning,
    "timeout": QMessageBox.Icon.Warning,
}

_TITLE_FOR_VERDICT = {
    "healthy": "Component store: healthy",
    "repairable": "Component store: damage found (repairable)",
    "unrepairable": "Component store: damage found (NOT repairable by DISM)",
    "refused": "Component store health: could not check",
    "unknown": "Component store health: unrecognised answer",
    "timeout": "Component store health: no answer",
}


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

    def on_deactivate(self) -> None:
        # A reader's tab page is permanent (CompositeModule never destroys
        # it), so the widget outlives a tab switch -- but the check-health
        # worker should not keep running against a tab the user has left.
        # A cancelled Worker emits `cancelled`, never `result` or `error`
        # (documented CLAUDE.md Cleanup-module trap), so nothing would
        # otherwise re-enable the button -- do that here, not in a signal
        # handler that will never fire.
        super().on_deactivate()
        self.cancel_all_workers()
        btn = self._control("check_health_btn")
        if widget_is_valid(btn):
            btn.setEnabled(True)

    def build_controls(self, toolbar, extra) -> None:
        btn = QPushButton("Check Component Store Health")
        btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /CheckHealth\n"
            "Read-only -- reads a flag a previous scan already set, does "
            "not scan anything itself. Needs elevation."
        )
        btn.clicked.connect(self._on_check_health)
        toolbar.addWidget(btn)
        extra["check_health_btn"] = btn

    def _on_check_health(self) -> None:
        btn = self._control("check_health_btn")
        if btn is not None:
            btn.setEnabled(False)

        def _run(_worker):
            return checkhealth.run_check_health()

        def _done(result: checkhealth.ComponentStoreHealth) -> None:
            if widget_is_valid(btn):
                btn.setEnabled(True)
            self._show_check_health_result(result)

        def _err(message: str) -> None:
            logger.error("CheckHealth worker failed: %s", message)
            if widget_is_valid(btn):
                btn.setEnabled(True)
                QMessageBox.warning(
                    self._pane, "Component store health: could not check",
                    f"Running DISM failed: {message}")

        worker = Worker(_run)
        worker.signals.result.connect(_done)
        worker.signals.error.connect(_err)
        self._workers.append(worker)
        self.app.thread_pool.start(worker)

    def _show_check_health_result(self, result: "checkhealth.ComponentStoreHealth") -> None:
        if not widget_is_valid(self._pane):
            return
        mb = QMessageBox(self._pane)
        mb.setWindowTitle(_TITLE_FOR_VERDICT.get(result.verdict, "Component store health"))
        mb.setIcon(_ICON_FOR_VERDICT.get(result.verdict, QMessageBox.Icon.Warning))
        text = result.detail
        if result.raw.strip():
            text += "\n\n" + result.raw.strip()[:1000]
        mb.setText(text)
        mb.exec()

    def _control(self, key: str):
        if self._pane is None:
            return None
        return self._pane.extra.get(key)

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