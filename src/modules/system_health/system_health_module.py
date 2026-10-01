"""System Health — read-only findings plus gated DISM servicing actions.

requires_admin=True, read_only_unelevated=True: matches Monitor
Control's pattern (display/DDC reads need no elevation, only audio
writes do) rather than Driver Manager's (requires_admin=False) --
System Health is fundamentally about privileged servicing operations
that happen to have a genuinely useful unelevated read-only subset,
which is the closer fit.
"""
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.semantic_colors import semantic
from core.widget_life import widget_is_valid

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

        self._copy_finding_btn = QPushButton("Copy finding")
        self._jump_btn = QPushButton("Open related tab")
        self._copy_finding_btn.setEnabled(False)
        self._jump_btn.setEnabled(False)
        self._copy_finding_btn.clicked.connect(self._copy_finding)
        self._jump_btn.clicked.connect(self._jump_to_finding)
        toolbar.insertWidget(1, self._copy_finding_btn)
        toolbar.insertWidget(2, self._jump_btn)

        self._findings_data = []
        self._findings_list = QListWidget()
        self._findings_list.setWordWrap(True)
        self._findings_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._findings_list.currentRowChanged.connect(self._on_finding_selected)
        self._finding_detail = QPlainTextEdit()
        self._finding_detail.setReadOnly(True)
        self._finding_detail.setMaximumHeight(200)
        self._finding_detail.setPlaceholderText("Select a finding to see the evidence behind it.")
        lay.addWidget(self._findings_list, 1)
        lay.addWidget(self._finding_detail)
        return tab

    def _refresh_findings(self) -> None:
        self._refresh_findings_btn.setEnabled(False)
        self._findings_status_lbl.setText("Checking (this reads disks, services, the System log)...")

        def _run(worker):
            from modules.system_health import findings
            return findings.full_findings(is_cancelled=lambda: worker.is_cancelled)

        def _done(results):
            if not widget_is_valid(self._findings_list):
                return
            self._refresh_findings_btn.setEnabled(True)
            self._findings_data = list(results)
            self._findings_list.clear()
            for finding in results:
                icon = "⚠️" if finding.severity == "warning" else "ℹ️"
                item = QListWidgetItem(f"{icon}  {finding.title}\n{finding.detail}")
                color = semantic("warning") if finding.severity == "warning" else semantic("info")
                item.setForeground(QColor(color))
                self._findings_list.addItem(item)
            warnings = sum(1 for f in results if f.severity == "warning")
            self._findings_status_lbl.setText(
                f"{len(results)} finding(s), {warnings} warning(s)" if results else "No issues found")

        def _err(e: str):
            if widget_is_valid(self._findings_list):
                self._refresh_findings_btn.setEnabled(True)
                self._findings_status_lbl.setText(f"Error: {e}")

        self._findings_worker = Worker(_run)
        self._findings_worker.signals.result.connect(_done)
        self._findings_worker.signals.error.connect(_err)
        self.app.thread_pool.start(self._findings_worker)

    def _current_finding(self):
        row = self._findings_list.currentRow()
        return self._findings_data[row] if 0 <= row < len(self._findings_data) else None

    def _on_finding_selected(self, _row: int) -> None:
        finding = self._current_finding()
        self._copy_finding_btn.setEnabled(finding is not None)
        self._jump_btn.setEnabled(bool(finding and finding.jump))
        self._finding_detail.setPlainText(finding.copy_text() if finding else "")

    def _copy_finding(self) -> None:
        from PyQt6.QtWidgets import QApplication
        finding = self._current_finding()
        if finding:
            QApplication.clipboard().setText(finding.copy_text())
            self._findings_status_lbl.setText("Copied to the clipboard")

    def _jump_to_finding(self) -> None:
        from core.events import NAV_REQUEST_MODULE, NavRequestData
        finding = self._current_finding()
        if finding and finding.jump:
            self.app.event_bus.publish(NAV_REQUEST_MODULE, NavRequestData(module_name=finding.jump))

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

        self._sfc_btn = QPushButton("🩹  SFC Scan")
        self._sfc_btn.setToolTip(
            "Runs: sfc /scannow\n"
            "Scans and repairs protected Windows system files. "
            "Can take 10-15 minutes."
        )
        self._sfc_btn.clicked.connect(self._run_sfc_scan)
        lay.addWidget(self._sfc_btn)

        self._restore_health_btn = QPushButton("🩹  DISM RestoreHealth")
        self._restore_health_btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /RestoreHealth\n"
            "Repairs component-store corruption ScanHealth detects. "
            "Can take 10-30 minutes."
        )
        self._restore_health_btn.clicked.connect(self._run_restore_health)
        lay.addWidget(self._restore_health_btn)

        self._chkdsk_btn = QPushButton("🩹  Schedule CHKDSK")
        self._chkdsk_btn.setToolTip(
            "Runs: chkdsk C: /f /r /x\n"
            "Schedules a full disk check and repair for the next reboot."
        )
        self._chkdsk_btn.clicked.connect(self._run_chkdsk_schedule)
        lay.addWidget(self._chkdsk_btn)

        lay.addWidget(self._servicing_out)
        lay.addStretch()

        self._servicing_pool = get_long_op_pool()
        return tab

    def _confirm(self, title: str, text: str,
                 icon: QMessageBox.Icon = QMessageBox.Icon.Information) -> bool:
        """Shared Ok/Cancel confirm dialog (default Cancel) -- the same
        boilerplate was repeated verbatim across every servicing action
        that shows one."""
        mb = QMessageBox(self._tabs)
        mb.setWindowTitle(title)
        mb.setIcon(icon)
        mb.setText(text)
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return mb.exec() == QMessageBox.StandardButton.Ok

    def _run_scan_health(self) -> None:
        self._scan_health_btn.setEnabled(False)
        self._servicing_out.setText("Checking for corruption (can take several minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_scan_health()

        def _done(result):
            if not widget_is_valid(self._scan_health_btn):
                return
            self._scan_health_btn.setEnabled(True)
            # DISM /ScanHealth exits 0 even when it found and recorded real
            # corruption -- measured on this machine (2026-10-01):
            # exit=0, stdout "The component store is repairable.\nThe
            # operation completed successfully." That is the SAME wording
            # family /CheckHealth already classifies text instead of
            # trusting the exit code for (checkhealth.classify, see its
            # own commit 14f5404 for the matching /CheckHealth measurement
            # on this same real, damaged machine). Reuse that classifier
            # rather than a second one for the same output family -- only
            # "healthy" may enable ResetBase; "repairable" is exactly the
            # damaged-store case this gate exists to block.
            from modules.dism_log import checkhealth
            verdict = checkhealth.classify(result.returncode, result.output)
            self._last_scan_health_clean = (verdict.verdict == "healthy")
            self._reset_base_btn.setEnabled(self._last_scan_health_clean)
            self._servicing_out.setText(
                f"ScanHealth: {verdict.verdict} (exit {result.returncode})\n"
                f"{verdict.detail}\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="scan_health",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            if not widget_is_valid(self._scan_health_btn):
                return
            self._scan_health_btn.setEnabled(True)
            self._last_scan_health_clean = False
            self._reset_base_btn.setEnabled(False)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
        self._servicing_pool.start(w)

    def _run_component_cleanup(self) -> None:
        if not self._confirm(
            "Component Cleanup",
            "Runs: dism /Online /Cleanup-Image /StartComponentCleanup\n\n"
            "Removes superseded Windows components from WinSxS. Can "
            "reclaim 2–10 GB. Takes several minutes. Continue?",
        ):
            return

        self._component_cleanup_btn.setEnabled(False)
        self._servicing_out.setText("Running Component Cleanup (this can take several minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_component_cleanup()

        def _done(result):
            if not widget_is_valid(self._component_cleanup_btn):
                return
            self._component_cleanup_btn.setEnabled(True)
            self._servicing_out.setText(
                f"Component Cleanup finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="component_cleanup",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            if not widget_is_valid(self._component_cleanup_btn):
                return
            self._component_cleanup_btn.setEnabled(True)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
        self._servicing_pool.start(w)

    def _run_sfc_scan(self) -> None:
        if not self._confirm(
            "SFC Scan",
            "Runs: sfc /scannow\n\nScans and repairs protected Windows "
            "system files. Can take 10-15 minutes. Continue?",
        ):
            return

        self._sfc_btn.setEnabled(False)
        self._servicing_out.setText("Running SFC scan (can take 10-15 minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_sfc_scan()

        def _done(result):
            if not widget_is_valid(self._sfc_btn):
                return
            self._sfc_btn.setEnabled(True)
            self._servicing_out.setText(
                f"SFC scan finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="sfc_scan",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            if not widget_is_valid(self._sfc_btn):
                return
            self._sfc_btn.setEnabled(True)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
        self._servicing_pool.start(w)

    def _run_restore_health(self) -> None:
        if not self._confirm(
            "DISM RestoreHealth",
            "Runs: dism /Online /Cleanup-Image /RestoreHealth\n\n"
            "Repairs component-store corruption. Can take 10-30 minutes. Continue?",
        ):
            return

        self._restore_health_btn.setEnabled(False)
        self._servicing_out.setText("Running DISM RestoreHealth (can take 10-30 minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_restore_health()

        def _done(result):
            if not widget_is_valid(self._restore_health_btn):
                return
            self._restore_health_btn.setEnabled(True)
            self._servicing_out.setText(
                f"RestoreHealth finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="restore_health",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            if not widget_is_valid(self._restore_health_btn):
                return
            self._restore_health_btn.setEnabled(True)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
        self._servicing_pool.start(w)

    def _run_chkdsk_schedule(self) -> None:
        if not self._confirm(
            "Schedule CHKDSK",
            "Runs: chkdsk C: /f /r /x\n\n"
            "Schedules a full disk check and repair for the NEXT REBOOT. "
            "Continue?",
            icon=QMessageBox.Icon.Warning,
        ):
            return

        self._chkdsk_btn.setEnabled(False)
        self._servicing_out.setText("Scheduling CHKDSK for next reboot...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_chkdsk_schedule()

        def _done(result):
            if not widget_is_valid(self._chkdsk_btn):
                return
            self._chkdsk_btn.setEnabled(True)
            self._servicing_out.setText(
                f"CHKDSK scheduled (exit {result.returncode}). Reboot to run.\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="chkdsk_schedule",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            if not widget_is_valid(self._chkdsk_btn):
                return
            self._chkdsk_btn.setEnabled(True)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
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
            if not widget_is_valid(self._servicing_out):
                return
            self._reset_base_btn.setEnabled(False)  # stays gated -- needs a fresh ScanHealth to re-enable
            self._last_scan_health_clean = False
            self._servicing_out.setText(
                f"Reset Base finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="reset_base",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            if not widget_is_valid(self._servicing_out):
                return
            self._servicing_out.setText(f"Reset Base did not run: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
        self._servicing_pool.start(w)

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._refresh_findings()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_status_info(self) -> str:
        return "System Health"
