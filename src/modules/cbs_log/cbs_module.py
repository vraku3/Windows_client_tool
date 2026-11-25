"""CBS Log as a Diagnose tab.

Everything specific to CBS Log is its parser and its search provider; the UI is
`LogPane` by way of `LogReaderModule`.

Windows 11 usually ships no plain CBS.log, so the CbsPersist cab fallback
in load_entries is the path that actually runs.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Optional

from PyQt6.QtWidgets import QPushButton

from core.log_reader_module import LogReaderModule
from core.types import LogReadResult
from core.windows_utils import program_files, system_root
from modules.cbs_log.cbs_parser import CBSParser
from modules.cbs_log.cbs_search_provider import CBSSearchProvider
from ui.glance import GlanceTile

logger = logging.getLogger(__name__)

CBS_LOG_PATH = os.path.join(system_root(), "Logs", "CBS", "CBS.log")


class CBSLogModule(LogReaderModule):
    name = "CBS Log"
    icon = "📝"
    description = "Component-Based Servicing log parser"
    requires_admin = False
    provider_class = CBSSearchProvider

    def __init__(self) -> None:
        super().__init__()
        self._store_health_tile: Optional[GlanceTile] = None
        self._pkg_stages_tile: Optional[GlanceTile] = None

    def pane_options(self) -> dict:
        from core import servicing_summary as ss

        self._store_health_tile = GlanceTile(
            "Store Health",
            tooltip="Component store health and corruption indicators.",
        )
        self._store_health_tile.set("Healthy", "No corruption", severity="ok")

        self._pkg_stages_tile = GlanceTile(
            "Package Stages",
            tooltip="Package servicing stage transitions (Installed, Staged, Superseded, Failed).",
        )
        self._pkg_stages_tile.set("0 Transitions", "Routine logs", severity="ok")

        def summarise(entries):
            if not entries:
                if self._store_health_tile:
                    self._store_health_tile.set("—", "No records", severity="ok")
                if self._pkg_stages_tile:
                    self._pkg_stages_tile.set("—", "No records", severity="ok")
                return ss.summarize(entries, "cbs")

            failures = ss.servicing_failures_summary(entries)
            stages = ss.package_stage_summary(entries)
            markers = ss.corruption_markers(entries)

            if self._store_health_tile:
                if failures["total_failures"] or markers:
                    total_issues = failures["total_failures"] + sum(markers.values())
                    self._store_health_tile.set("Issues Found", f"{total_issues} issue(s)", severity="danger")
                else:
                    self._store_health_tile.set("Healthy", "No corruption", severity="ok")

            if self._pkg_stages_tile:
                if stages["total_transitions"]:
                    failed = stages["counts"].get("Failed", 0)
                    inst = stages["counts"].get("Installed", 0)
                    staged = stages["counts"].get("Staged", 0)
                    self._pkg_stages_tile.set(
                        f"{stages['total_transitions']} Transitions",
                        f"{inst} Inst, {staged} Stage, {failed} Fail",
                        severity="danger" if failed else "info",
                    )
                else:
                    self._pkg_stages_tile.set("0 Transitions", "Routine logs", severity="ok")

            return ss.summarize(entries, "cbs")

        return {
            "detail_enricher": ss.detail_html,
            "summarizer": summarise,
            "glance_tiles": [self._store_health_tile, self._pkg_stages_tile],
        }

    def build_controls(self, toolbar, extra) -> None:
        btn = QPushButton("Open CBS Folder")
        btn.setToolTip("Open CBS servicing directory containing live and archived records.")
        btn.clicked.connect(lambda: subprocess.Popen(["explorer.exe", os.path.dirname(CBS_LOG_PATH)]))
        toolbar.addWidget(btn)

        cmtrace_btn = QPushButton("📄 Open in CMTrace")
        cmtrace_btn.setToolTip("Open CBS logs in integrated Log Viewer with live tailing and CMTrace syntax.")
        cmtrace_btn.clicked.connect(self._open_in_log_viewer)
        toolbar.addWidget(cmtrace_btn)

    def _open_in_log_viewer(self) -> None:
        if self.app and hasattr(self.app, "main_window") and self.app.main_window:
            self.app.main_window.navigate_to("Log Viewer")
            target = CBS_LOG_PATH if os.path.exists(CBS_LOG_PATH) else os.path.dirname(CBS_LOG_PATH)
            diag = next((m for m in getattr(self.app.module_registry, "_modules", []) if m.name == "Diagnose"), None)
            if diag and len(diag.children) > 7:
                lv = diag.children[7]
                w = getattr(lv, "_widget", None)
                if w is not None:
                    if os.path.isdir(target):
                        w.open_folder(target)
                    else:
                        w.open(target)

    def load_entries(self, worker):
        # Windows 11 stores CBS in .cab files under C:\Windows\Logs\CBS\
        if not os.path.exists(CBS_LOG_PATH):
            logger.info("CBS.log not found — trying to extract from cab archive")
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
                return LogReadResult(problems=[f"CBS.log is unavailable and no CBS CAB archives were readable in {cab_dir}"])

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
                entries = parser.parse_result(
                    progress_callback=lambda p: worker.signals.progress.emit(50 + p // 2)
                )
                try:
                    os.unlink(log_path)
                    os.rmdir(tmp_dir)
                except Exception as e:
                    logger.debug("Could not clean up temp files: %s", e)
                return entries
            else:
                logger.warning("Could not extract CBS log from cab: %s", latest_cab)
                return LogReadResult(problems=[f"Could not extract CBS records from {latest_cab}; check 7-Zip availability and archive access"])

        parser = CBSParser(CBS_LOG_PATH)
        return parser.parse_result(
            progress_callback=lambda p: worker.signals.progress.emit(p)
        )
