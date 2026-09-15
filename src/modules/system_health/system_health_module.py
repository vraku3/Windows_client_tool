"""System Health — read-only findings plus gated DISM servicing actions.

requires_admin=True, read_only_unelevated=True: matches Monitor
Control's pattern (display/DDC reads need no elevation, only audio
writes do) rather than Driver Manager's (requires_admin=False) --
System Health is fundamentally about privileged servicing operations
that happen to have a genuinely useful unelevated read-only subset,
which is the closer fit.
"""
import logging

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.semantic_colors import semantic

logger = logging.getLogger(__name__)


class SystemHealthModule(BaseModule):
    name = "System Health"
    icon = "🩺"
    description = "Servicing findings, WinSxS cleanup, and component-store health checks"
    requires_admin = True
    read_only_unelevated = True
    group = ModuleGroup.SYSTEM

    def __init__(self):
        super().__init__()
        self._last_scan_health_clean = False
        self._findings_worker = None

    def on_start(self, app) -> None:
        self.app = app

    def create_widget(self) -> QWidget:
        from PyQt6.QtWidgets import QTabWidget
        outer = QWidget()
        layout = QVBoxLayout(outer)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_findings_tab(), "Findings")
        self._tabs.addTab(self._build_servicing_tab(), "Servicing")

        return outer

    # ── Findings tab ──────────────────────────────────────────────────

    def _build_findings_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        self._refresh_findings_btn = QPushButton("🔄  Refresh Findings")
        self._refresh_findings_btn.clicked.connect(self._refresh_findings)
        self._findings_status_lbl = QLabel("Click Refresh Findings to check this machine")
        self._findings_status_lbl.setObjectName("muted")
        toolbar.addWidget(self._refresh_findings_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._findings_status_lbl)
        lay.addLayout(toolbar)

        self._findings_list = QListWidget()
        lay.addWidget(self._findings_list, 1)

        return tab

    def _refresh_findings(self) -> None:
        self._refresh_findings_btn.setEnabled(False)
        self._findings_status_lbl.setText("Checking...")

        def _run(_worker):
            from modules.system_health import findings
            return findings.all_findings()

        def _done(results):
            self._refresh_findings_btn.setEnabled(True)
            self._findings_list.clear()
            for finding in results:
                icon = "⚠️" if finding.severity == "warning" else "ℹ️"
                item = QListWidgetItem(f"{icon}  {finding.title}\n{finding.detail}")
                color = semantic("warning") if finding.severity == "warning" else semantic("info")
                item.setForeground(QColor(color))
                self._findings_list.addItem(item)
            self._findings_status_lbl.setText(
                f"{len(results)} finding(s)" if results else "No issues found")

        def _err(e: str):
            self._refresh_findings_btn.setEnabled(True)
            self._findings_status_lbl.setText(f"Error: {e}")

        self._findings_worker = Worker(_run)
        self._findings_worker.signals.result.connect(_done)
        self._findings_worker.signals.error.connect(_err)
        self.app.thread_pool.start(self._findings_worker)

    # ── Servicing tab (Task 5 fills this in) ────────────────────────────

    def _build_servicing_tab(self) -> QWidget:
        return QWidget()

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._refresh_findings()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_status_info(self) -> str:
        return "System Health"
