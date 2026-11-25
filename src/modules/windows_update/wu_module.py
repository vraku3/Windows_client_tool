"""Windows Update as a Diagnose tab.

Everything specific to Windows Update is its parser, its analysis, its reader,
and its search provider; the UI is `LogPane` by way of `LogReaderModule`.
The strip above the table names each failing update with its error code translated,
and Glance tiles summarize installed updates, pending/failing items, and average install time.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import List

from PyQt6.QtWidgets import QPushButton

from core.log_reader_module import LogReaderModule
from core.windows_utils import system_root
from ui.glance import GlanceTile

from modules.windows_update import wu_analysis, wu_reader
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

    def __init__(self) -> None:
        super().__init__()
        self._installed_tile: Optional[GlanceTile] = None
        self._pending_tile: Optional[GlanceTile] = None
        self._avg_duration_tile: Optional[GlanceTile] = None

    def pane_options(self) -> dict:
        self._installed_tile = GlanceTile("Installed KBs", tooltip="Total distinct updates successfully installed")
        self._pending_tile = GlanceTile("Pending / Failed", tooltip="Updates requiring attention, pending restart, or failing installation")
        self._avg_duration_tile = GlanceTile("Avg Install Time", tooltip="Mean duration measured between installation start and completion")
        self._installed_tile.set_unreadable("Not read yet")
        self._pending_tile.set_unreadable("Not read yet")
        self._avg_duration_tile.set_unreadable("Not read yet")
        return {
            "detail_enricher": wu_analysis.detail_html,
            "summarizer": self._summary,
            "glance_tiles": [self._installed_tile, self._pending_tile, self._avg_duration_tile],
        }

    def _summary(self, entries: list) -> str:
        if self._installed_tile is None or self._pending_tile is None or self._avg_duration_tile is None:
            return wu_analysis.summary_text(entries)
        if not entries:
            self._installed_tile.set_unreadable("No reporting events")
            self._pending_tile.set_unreadable("No reporting events")
            self._avg_duration_tile.set_unreadable("No reporting events")
            return wu_analysis.summary_text(entries)

        groups = wu_reader.group_updates_by_kb(entries)
        installed = [g for g in groups.values() if g.status == "Installed"]
        failed = [g for g in groups.values() if g.status == "Failed"]
        pending = [g for g in groups.values() if g.status == "Pending"]

        all_durations = [d for g in groups.values() for d in g.durations]
        avg_dur = (sum(all_durations) / len(all_durations)) if all_durations else None

        self._installed_tile.set(
            f"{len(installed)}",
            sub="Installed successfully",
            severity="ok",
        )

        total_problem = len(failed) + len(pending)
        sev = "danger" if len(failed) > 0 else ("warning" if len(pending) > 0 else "ok")
        self._pending_tile.set(
            f"{total_problem}",
            sub=f"{len(failed)} failed, {len(pending)} pending",
            severity=sev,
        )

        if avg_dur is not None:
            self._avg_duration_tile.set(
                f"{avg_dur:.0f}s",
                sub=f"Across {len(all_durations)} installs",
                severity="info",
            )
        else:
            self._avg_duration_tile.set("—", sub="No duration data", severity="muted")

        return wu_analysis.summary_text(entries)

    def build_controls(self, toolbar, extra) -> None:
        settings_btn = QPushButton("WU Settings")
        settings_btn.setToolTip("Open Windows Update page in Settings (ms-settings:windowsupdate).")
        settings_btn.clicked.connect(lambda: subprocess.Popen(["cmd.exe", "/c", "start", "ms-settings:windowsupdate"], shell=True))
        toolbar.addWidget(settings_btn)

        folder_btn = QPushButton("Open Folder")
        folder_btn.setToolTip("Open SoftwareDistribution folder in Explorer.")
        folder_btn.clicked.connect(lambda: subprocess.Popen(["explorer.exe", os.path.dirname(WU_LOG_PATH)]))
        toolbar.addWidget(folder_btn)

    def load_entries(self, worker):
        parser = WUParser(WU_LOG_PATH)
        if not parser.file_exists():
            # A log that is not there is a different answer from an empty one.
            raise FileNotFoundError(f"{WU_LOG_PATH} does not exist on this machine")
        return parser.parse_result(
            progress_callback=lambda p: worker.signals.progress.emit(p)
        )
