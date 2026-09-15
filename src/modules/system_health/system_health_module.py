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


class _ResetBaseConfirmDialog:
    """Typed-confirmation gate for Reset Base -- a checkbox is too easy
    to click through without reading; typing the literal word forces at
    least a moment's real attention."""

    def __init__(self, parent=None):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QDialogButtonBox
        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle("Reset Base — Confirm")
        lay = QVBoxLayout(self._dialog)
        lay.addWidget(QLabel(
            "This runs:\n\n"
            "  dism /Online /Cleanup-Image /StartComponentCleanup /ResetBase\n\n"
            "This PERMANENTLY removes the ability to uninstall currently "
            "installed Windows updates. A restore point will be created "
            "automatically first. This cannot be undone by this app.\n\n"
            "Type RESETBASE to continue:"
        ))
        self._confirm_field = QLineEdit()
        lay.addWidget(self._confirm_field)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setEnabled(False)
        self._confirm_field.textChanged.connect(
            lambda text: self._ok_button.setEnabled(text == "RESETBASE"))
        self._buttons.accepted.connect(self._dialog.accept)
        self._buttons.rejected.connect(self._dialog.reject)
        lay.addWidget(self._buttons)

    def exec(self) -> bool:
        from PyQt6.QtWidgets import QDialog
        return self._dialog.exec() == QDialog.DialogCode.Accepted


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

    # ── Servicing tab ────────────────────────────────────────────────

    def _build_servicing_tab(self) -> QWidget:
        from core.long_op_pool import get_long_op_pool
        tab = QWidget()
        lay = QVBoxLayout(tab)

        self._servicing_out = QLabel("")
        self._servicing_out.setWordWrap(True)
        self._servicing_out.setObjectName("muted")

        self._scan_health_btn = QPushButton("🔍  Check for Corruption (ScanHealth)")
        self._scan_health_btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /ScanHealth\n"
            "Checks the component store for corruption. Read-only, "
            "does not repair anything. Can take several minutes."
        )
        self._scan_health_btn.clicked.connect(self._run_scan_health)
        lay.addWidget(self._scan_health_btn)

        self._component_cleanup_btn = QPushButton("🗑️  Component Cleanup")
        self._component_cleanup_btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /StartComponentCleanup\n"
            "Removes superseded Windows components from WinSxS. "
            "Can reclaim 2–10 GB. Takes several minutes."
        )
        self._component_cleanup_btn.clicked.connect(self._run_component_cleanup)
        lay.addWidget(self._component_cleanup_btn)

        self._reset_base_btn = QPushButton("⚠️  Reset Base (permanent)")
        self._reset_base_btn.setEnabled(False)
        self._reset_base_btn.setToolTip(
            "Run Check for Corruption first, with a clean result, before "
            "Reset Base becomes available."
        )
        self._reset_base_btn.clicked.connect(self._on_reset_base_clicked)
        lay.addWidget(self._reset_base_btn)

        lay.addWidget(self._servicing_out)
        lay.addStretch()

        self._servicing_pool = get_long_op_pool()
        return tab

    def _run_scan_health(self) -> None:
        self._scan_health_btn.setEnabled(False)
        self._servicing_out.setText("Checking for corruption (can take several minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_scan_health()

        def _done(result):
            self._scan_health_btn.setEnabled(True)
            self._last_scan_health_clean = (result.returncode == 0)
            self._reset_base_btn.setEnabled(self._last_scan_health_clean)
            self._servicing_out.setText(
                f"ScanHealth: {'no corruption found' if self._last_scan_health_clean else 'issue detected'} "
                f"(exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="scan_health",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            self._scan_health_btn.setEnabled(True)
            self._last_scan_health_clean = False
            self._reset_base_btn.setEnabled(False)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._servicing_pool.start(w)

    def _run_component_cleanup(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        mb = QMessageBox(self._tabs)
        mb.setWindowTitle("Component Cleanup")
        mb.setIcon(QMessageBox.Icon.Information)
        mb.setText(
            "Runs: dism /Online /Cleanup-Image /StartComponentCleanup\n\n"
            "Removes superseded Windows components from WinSxS. Can "
            "reclaim 2–10 GB. Takes several minutes. Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return

        self._component_cleanup_btn.setEnabled(False)
        self._servicing_out.setText("Running Component Cleanup (this can take several minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_component_cleanup()

        def _done(result):
            self._component_cleanup_btn.setEnabled(True)
            self._servicing_out.setText(
                f"Component Cleanup finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="component_cleanup",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            self._component_cleanup_btn.setEnabled(True)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._servicing_pool.start(w)

    def _on_reset_base_clicked(self) -> None:
        if not self._last_scan_health_clean:
            return
        dlg = _ResetBaseConfirmDialog(self._tabs)
        if not dlg.exec():
            return
        self._do_reset_base_confirmed()

    def _do_reset_base_confirmed(self) -> None:
        self._reset_base_btn.setEnabled(False)
        self._servicing_out.setText("Creating a restore point before Reset Base...")

        def _run(_worker):
            from core.system_restore import create_restore_point
            from modules.system_health import servicing
            ok, msg = create_restore_point("Before System Health Reset Base")
            if not ok:
                raise RuntimeError(f"Could not create a restore point: {msg}")
            return servicing.run_reset_base()

        def _done(result):
            self._reset_base_btn.setEnabled(False)  # stays gated -- needs a fresh ScanHealth to re-enable
            self._last_scan_health_clean = False
            self._servicing_out.setText(
                f"Reset Base finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="reset_base",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            self._servicing_out.setText(f"Reset Base did not run: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._servicing_pool.start(w)

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._refresh_findings()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_status_info(self) -> str:
        return "System Health"
