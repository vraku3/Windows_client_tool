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
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.semantic_colors import semantic
from core.widget_life import widget_is_valid

logger = logging.getLogger(__name__)


from modules.diagnose.help_ui import explain
from modules.system_health.help_text import INTRO, CONTROLS, HEADERS, TABS

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
        self._confirm_field.setToolTip("Type RESETBASE exactly to enable OK. Accepting proceeds to the restore-point gate and permanent component cleanup; this app cannot restore uninstall options for currently installed updates.")
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
        explain(self._dialog, "Reset Base permanently removes the ability to uninstall currently installed Windows updates. It needs administrator rights, a clean ScanHealth in this session, typed confirmation and successful restore-point creation; this app cannot undo it.")

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
        self._storage_boot_tab = None

    def on_start(self, app) -> None:
        self.app = app

    def create_widget(self) -> QWidget:
        from PyQt6.QtWidgets import QTabWidget
        from modules.system_health.storage_boot_tab import StorageBootTab
        outer = QWidget()
        layout = QVBoxLayout(outer)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._storage_boot_tab = StorageBootTab()
        self._tabs.addTab(self._build_findings_tab(), "Findings")
        self._tabs.addTab(self._build_servicing_tab(), "Servicing")
        self._tabs.addTab(self._build_latency_tab(), "Latency & Timers")
        self._tabs.addTab(self._storage_boot_tab, "Storage & Boot Health")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        explain(outer, INTRO, CONTROLS, HEADERS, TABS)
        return outer

    # ── Findings tab ──────────────────────────────────────────────────

    def _build_findings_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(6)

        toolbar = QHBoxLayout()
        self._refresh_findings_btn = QPushButton("🔄  Refresh Findings")
        self._refresh_findings_btn.clicked.connect(self._refresh_findings)
        self._findings_status_lbl = QLabel("Click Refresh Findings to check this machine")
        self._findings_status_lbl.setToolTip("Results of the latest checks, including refused or unavailable evidence where reported. No issues found means these checks found none; it is not a complete certification of PC health.")
        self._findings_status_lbl.setObjectName("muted")
        toolbar.addWidget(self._refresh_findings_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._findings_status_lbl)
        lay.addLayout(toolbar)

        self._copy_finding_btn = QPushButton("Copy finding")
        self._jump_btn = QPushButton("Open related tab")
        self._export_report_btn = QPushButton("Export Audit Report...")
        self._copy_finding_btn.setEnabled(False)
        self._jump_btn.setEnabled(False)
        self._copy_finding_btn.clicked.connect(self._copy_finding)
        self._jump_btn.clicked.connect(self._jump_to_finding)
        self._export_report_btn.setToolTip("Export a standalone HTML / Markdown system health audit report")
        self._export_report_btn.clicked.connect(self._export_audit_report)
        toolbar.insertWidget(1, self._copy_finding_btn)
        toolbar.insertWidget(2, self._jump_btn)
        toolbar.insertWidget(3, self._export_report_btn)

        # Filter bar: Search + Severity filter pills
        filter_bar = QHBoxLayout()
        self._findings_search = QLineEdit()
        self._findings_search.setPlaceholderText("Filter findings by title, detail, or evidence…")
        self._findings_search.setToolTip("Filter findings by keyword in title, details, or evidence.")
        self._findings_search.setClearButtonEnabled(True)
        self._findings_search.textChanged.connect(self._apply_findings_filter)
        filter_bar.addWidget(self._findings_search, 1)

        self._severity_filter = "all"
        self._filter_all_btn = QPushButton("All (0)")
        self._filter_all_btn.setToolTip("Show all system health findings.")
        self._filter_errors_btn = QPushButton("Errors (0)")
        self._filter_errors_btn.setToolTip("Filter to show critical errors only.")
        self._filter_warnings_btn = QPushButton("Warnings (0)")
        self._filter_warnings_btn.setToolTip("Filter to show warnings only.")
        self._filter_info_btn = QPushButton("Info (0)")
        self._filter_info_btn.setToolTip("Filter to show informational findings only.")
        self._filter_info_btn.setToolTip("Filter to show informational findings.")

        for btn in (self._filter_all_btn, self._filter_errors_btn, self._filter_warnings_btn, self._filter_info_btn):
            btn.setCheckable(True)

        self._filter_all_btn.setChecked(True)
        self._filter_all_btn.clicked.connect(lambda: self._set_severity_filter("all"))
        self._filter_errors_btn.clicked.connect(lambda: self._set_severity_filter("error"))
        self._filter_warnings_btn.clicked.connect(lambda: self._set_severity_filter("warning"))
        self._filter_info_btn.clicked.connect(lambda: self._set_severity_filter("info"))

        filter_bar.addWidget(self._filter_all_btn)
        filter_bar.addWidget(self._filter_errors_btn)
        filter_bar.addWidget(self._filter_warnings_btn)
        filter_bar.addWidget(self._filter_info_btn)
        lay.addLayout(filter_bar)

        self._findings_data = []
        self._filtered_findings = []
        self._findings_list = QListWidget()
        self._findings_list.setWordWrap(True)
        self._findings_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._findings_list.currentRowChanged.connect(self._on_finding_selected)
        self._findings_list.itemDoubleClicked.connect(lambda _item: self._jump_to_finding())

        self._finding_detail = QPlainTextEdit()
        self._finding_detail.setReadOnly(True)
        self._finding_detail.setMaximumHeight(200)
        self._finding_detail.setPlaceholderText("Select a finding to see the evidence behind it.")
        lay.addWidget(self._findings_list, 1)
        lay.addWidget(self._finding_detail)
        return tab

    # ── Latency & Timers tab ──────────────────────────────────────────

    def _build_latency_tab(self) -> QWidget:
        from PyQt6.QtWidgets import QFrame, QProgressBar, QGridLayout
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(10)

        # Toolbar
        toolbar = QHBoxLayout()
        self._refresh_latency_btn = QPushButton("🔄  Refresh Stats")
        self._refresh_latency_btn.clicked.connect(self._refresh_latency_stats)
        self._sample_latency_btn = QPushButton("⚡  Run Latency Sample")
        self._sample_latency_btn.clicked.connect(self._run_latency_sample)
        self._latency_progress = QProgressBar()
        self._latency_progress.setVisible(False)
        self._latency_progress.setRange(0, 100)
        self._latency_progress.setMaximumWidth(150)
        self._latency_status_lbl = QLabel("Ready to inspect kernel latency and timers.")
        self._latency_status_lbl.setObjectName("muted")

        toolbar.addWidget(self._refresh_latency_btn)
        toolbar.addWidget(self._sample_latency_btn)
        toolbar.addWidget(self._latency_progress)
        toolbar.addStretch()
        toolbar.addWidget(self._latency_status_lbl)
        lay.addLayout(toolbar)

        # Stats Cards
        grid = QGridLayout()
        grid.setSpacing(8)

        # Timer Card
        card_timer = QFrame()
        card_timer.setObjectName("card")
        lay_timer = QVBoxLayout(card_timer)
        lbl_t_title = QLabel("<b>Platform Timer Resolution</b>")
        self._lbl_timer_val = QLabel("Not checked")
        self._lbl_timer_desc = QLabel("Querying NtQueryTimerResolution...")
        self._lbl_timer_desc.setWordWrap(True)
        self._lbl_timer_desc.setObjectName("muted")
        lay_timer.addWidget(lbl_t_title)
        lay_timer.addWidget(self._lbl_timer_val)
        lay_timer.addWidget(self._lbl_timer_desc)
        grid.addWidget(card_timer, 0, 0)

        # Clock Card
        card_clock = QFrame()
        card_clock.setObjectName("card")
        lay_clock = QVBoxLayout(card_clock)
        lbl_c_title = QLabel("<b>Platform Clock Source (QPC)</b>")
        self._lbl_clock_val = QLabel("Not checked")
        self._lbl_clock_desc = QLabel("Querying QueryPerformanceFrequency...")
        self._lbl_clock_desc.setWordWrap(True)
        self._lbl_clock_desc.setObjectName("muted")
        lay_clock.addWidget(lbl_c_title)
        lay_clock.addWidget(self._lbl_clock_val)
        lay_clock.addWidget(self._lbl_clock_desc)
        grid.addWidget(card_clock, 0, 1)

        # Interrupt Card
        card_dpc = QFrame()
        card_dpc.setObjectName("card")
        lay_dpc = QVBoxLayout(card_dpc)
        lbl_d_title = QLabel("<b>Interrupt & DPC Distribution</b>")
        self._lbl_dpc_val = QLabel("Not checked")
        self._lbl_dpc_desc = QLabel("Querying SystemInterruptInformation...")
        self._lbl_dpc_desc.setWordWrap(True)
        self._lbl_dpc_desc.setObjectName("muted")
        lay_dpc.addWidget(lbl_d_title)
        lay_dpc.addWidget(self._lbl_dpc_val)
        lay_dpc.addWidget(self._lbl_dpc_desc)
        grid.addWidget(card_dpc, 0, 2)

        lay.addLayout(grid)

        # Latency Sample Results area
        lbl_sample_hdr = QLabel("<b>Kernel Scheduling Jitter & Micro-Stutter Analysis:</b>")
        lay.addWidget(lbl_sample_hdr)

        self._latency_out = QPlainTextEdit()
        self._latency_out.setReadOnly(True)
        self._latency_out.setPlaceholderText(
            "Click 'Run Latency Sample' to execute a calibrated microsecond kernel jitter measurement.\n\n"
            "This measures real-time kernel scheduling interruptions. Spikes over 1000 µs (1.0 ms) indicate "
            "DPC latency delays caused by device drivers (often GPU, audio, or network drivers) or power-state "
            "transitions, which cause game frame drops and audio stutter."
        )
        lay.addWidget(self._latency_out, 1)

        return tab

    def _refresh_latency_stats(self) -> None:
        from modules.system_health.latency_engine import diagnose_latency_health
        report = diagnose_latency_health()
        if hasattr(self, "_lbl_timer_val") and widget_is_valid(self._lbl_timer_val):
            self._lbl_timer_val.setText(f"<b>{report.timer.current_resolution_ms:.3f} ms</b>")
            self._lbl_timer_desc.setText(report.timer.verdict)
            self._lbl_clock_val.setText(f"<b>{report.clock.frequency_mhz:.3f} MHz</b>")
            self._lbl_clock_desc.setText(report.clock.verdict)
            self._lbl_dpc_val.setText(f"<b>{report.interrupts.total_dpc_count:,} DPCs</b> ({report.interrupts.core_count} Cores)")
            self._lbl_dpc_desc.setText(report.interrupts.distribution_note)
            self._latency_status_lbl.setText("Updated stats")

    def _run_latency_sample(self) -> None:
        self._sample_latency_btn.setEnabled(False)
        self._latency_progress.setVisible(True)
        self._latency_progress.setValue(0)
        self._latency_status_lbl.setText("Sampling kernel execution latency for 3 seconds...")

        def _run(worker):
            from modules.system_health.latency_engine import sample_kernel_latency
            return sample_kernel_latency(
                duration_seconds=3.0,
                callback=lambda p: worker.signals.progress.emit(p),
            )

        def _done(res):
            if not widget_is_valid(self._sample_latency_btn):
                return
            self._sample_latency_btn.setEnabled(True)
            self._latency_progress.setVisible(False)
            self._latency_status_lbl.setText(f"Sample finished: Max Latency {res.max_latency_us:.1f} µs")

            lines = [
                "=== KERNEL SCHEDULING JITTER & DPC LATENCY REPORT ===",
                f"Duration: {res.duration_seconds}s | Total Samples: {res.samples_count:,}",
                f"Maximum Latency Spike: {res.max_latency_us:.1f} µs",
                f"Average Latency Jitter: {res.avg_latency_us:.1f} µs",
                f"Stutter Spikes (>1000 µs): {res.stutter_spikes_1000us}",
                f"Severe Spikes (>2000 µs): {res.severe_spikes_2000us}",
                f"\nVerdict: {res.verdict}",
            ]
            if res.stutter_spikes_1000us > 0:
                lines.extend([
                    "\n--- Admin Troubleshooting Advice ---",
                    "• High DPC latency is almost always caused by third-party device drivers holding execution at DISPATCH_LEVEL.",
                    "• Common culprits: Wi-Fi/NIC drivers (NDIS), GPU display drivers, or motherboard USB controller drivers.",
                    "• Check Driver Manager for recent driver updates or rollbacks.",
                    "• Ensure platform clock is not forced to HPET (useplatformclock should be disabled in BCD).",
                ])
            else:
                lines.extend([
                    "\n--- System Status ---",
                    "• System responsiveness is optimal. No kernel scheduling delays exceeding 1.0 ms were detected.",
                ])
            self._latency_out.setPlainText("\n".join(lines))

        def _err(e: str):
            if not widget_is_valid(self._sample_latency_btn):
                return
            self._sample_latency_btn.setEnabled(True)
            self._latency_progress.setVisible(False)
            self._latency_status_lbl.setText("Sample failed")
            self._latency_out.setPlainText(f"Error sampling latency: {e}")

        w = Worker(_run)
        w.signals.progress.connect(self._latency_progress.setValue)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
        if self.app and hasattr(self.app, "thread_pool"):
            self.app.thread_pool.start(w)
        else:
            from PyQt6.QtCore import QThreadPool
            QThreadPool.globalInstance().start(w)

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

            errors = sum(1 for f in results if f.severity in ("error", "critical"))
            warnings = sum(1 for f in results if f.severity == "warning")
            infos = sum(1 for f in results if f.severity == "info")

            if hasattr(self, "_filter_all_btn"):
                self._filter_all_btn.setText(f"All ({len(results)})")
                self._filter_errors_btn.setText(f"Errors ({errors})")
                self._filter_warnings_btn.setText(f"Warnings ({warnings})")
                self._filter_info_btn.setText(f"Info ({infos})")

            summary_parts = []
            if errors:
                summary_parts.append(f"{errors} error(s)")
            if warnings:
                summary_parts.append(f"{warnings} warning(s)")
            if infos:
                summary_parts.append(f"{infos} info(s)")
            status_text = f"{len(results)} finding(s): {', '.join(summary_parts)}" if results else "No issues found"
            self._findings_status_lbl.setText(status_text)

            self._apply_findings_filter()

        def _err(e: str):
            if widget_is_valid(self._findings_list):
                self._refresh_findings_btn.setEnabled(True)
                self._findings_status_lbl.setText(f"Error: {e}")

        self._findings_worker = Worker(_run)
        self._findings_worker.signals.result.connect(_done)
        self._findings_worker.signals.error.connect(_err)
        self._workers.append(self._findings_worker)
        self.app.thread_pool.start(self._findings_worker)

    def _set_severity_filter(self, sev: str) -> None:
        self._severity_filter = sev
        if hasattr(self, "_filter_all_btn"):
            self._filter_all_btn.setChecked(sev == "all")
            self._filter_errors_btn.setChecked(sev == "error")
            self._filter_warnings_btn.setChecked(sev == "warning")
            self._filter_info_btn.setChecked(sev == "info")
        self._apply_findings_filter()

    def _apply_findings_filter(self) -> None:
        if not widget_is_valid(self._findings_list):
            return
        query = self._findings_search.text().strip().lower() if hasattr(self, "_findings_search") else ""
        sev_filter = getattr(self, "_severity_filter", "all")

        self._filtered_findings = []
        self._findings_list.clear()

        for finding in self._findings_data:
            if sev_filter == "error" and finding.severity not in ("error", "critical"):
                continue
            if sev_filter == "warning" and finding.severity != "warning":
                continue
            if sev_filter == "info" and finding.severity != "info":
                continue

            if query:
                haystack = f"{finding.title} {finding.detail} {finding.evidence} {finding.jump}".lower()
                if query not in haystack:
                    continue

            self._filtered_findings.append(finding)
            if finding.severity in ("error", "critical"):
                icon = "⛔"
                color = semantic("error")
            elif finding.severity == "warning":
                icon = "⚠️"
                color = semantic("warning")
            else:
                icon = "ℹ️"
                color = semantic("info")

            jump_suffix = f"   [→ {finding.jump}]" if finding.jump else ""
            item = QListWidgetItem(f"{icon}  {finding.title}{jump_suffix}\n{finding.detail}")
            item.setToolTip(finding.copy_text())
            item.setForeground(QColor(color))
            self._findings_list.addItem(item)

        if self._filtered_findings:
            self._findings_list.setCurrentRow(0)
        else:
            self._on_finding_selected(-1)

    def _current_finding(self):
        row = self._findings_list.currentRow()
        data = getattr(self, "_filtered_findings", self._findings_data)
        return data[row] if 0 <= row < len(data) else None

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

    def _export_audit_report(self) -> None:
        if not self._findings_data:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self._widget, "Export Report", "Run Refresh Findings first before exporting.")
            return

        from modules.system_health.report_generator import (
            render_health_report_html,
            render_health_report_markdown,
            write_health_report,
        )
        import os

        app_data = getattr(self.app, "app_data_dir", None)
        html_text = render_health_report_html(self._findings_data)
        md_text = render_health_report_markdown(self._findings_data)

        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.clipboard().setText(md_text)

        path = write_health_report(app_data, html_text)
        if path:
            try:
                os.startfile(path)
            except Exception:
                logger.warning("Could not open health report: %s", path, exc_info=True)
            self._findings_status_lbl.setText(f"Exported to {os.path.basename(path)} (Markdown copied)")
        else:
            self._findings_status_lbl.setText("Markdown summary copied to clipboard")

    # ── Servicing tab ────────────────────────────────────────────────

    def _build_servicing_tab(self) -> QWidget:
        from core.long_op_pool import get_long_op_pool
        tab = QWidget()
        lay = QVBoxLayout(tab)

        self._servicing_out = QLabel("")
        self._servicing_out.setToolTip("Output and result from the most recent servicing command. Read the text as well as the exit code: DISM can finish successfully while reporting repairable corruption.")
        self._servicing_out.setWordWrap(True)
        self._servicing_out.setObjectName("muted")

        self._scan_health_btn = QPushButton("🔍  Check for Corruption (ScanHealth)")
        self._scan_health_btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /ScanHealth\n"
            "Checks the component store for corruption. Read-only, "
            "does not repair anything. Can take several minutes. Needs administrator rights; "
            "starts without confirmation and may record the detected health state."
        )
        self._scan_health_btn.clicked.connect(self._run_scan_health)
        lay.addWidget(self._scan_health_btn)

        self._component_cleanup_btn = QPushButton("🗑️  Component Cleanup")
        self._component_cleanup_btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /StartComponentCleanup\n"
            "Removes superseded Windows components from WinSxS. "
            "Can reclaim 2–10 GB. Takes several minutes. Asks first and needs administrator rights; "
            "the amount varies and this app cannot restore removed component files."
        )
        self._component_cleanup_btn.clicked.connect(self._run_component_cleanup)
        lay.addWidget(self._component_cleanup_btn)

        self._reset_base_btn = QPushButton("⚠️  Reset Base (permanent)")
        self._reset_base_btn.setEnabled(False)
        self._reset_base_btn.setToolTip(
            "Run Check for Corruption first, with a clean result, before "
            "Reset Base becomes available. Needs administrator rights, typed RESETBASE confirmation "
            "and successful restore-point creation. Permanently removes uninstall options for "
            "currently installed Windows updates; this app cannot undo it."
        )
        self._reset_base_btn.clicked.connect(self._on_reset_base_clicked)
        lay.addWidget(self._reset_base_btn)

        self._sfc_btn = QPushButton("🩹  SFC Scan")
        self._sfc_btn.setToolTip(
            "Runs: sfc /scannow\n"
            "Scans and repairs protected Windows system files. "
            "Can take 10-15 minutes. Asks first and needs administrator rights; repaired files "
            "are changed on disk and this app has no undo for the repair."
        )
        self._sfc_btn.clicked.connect(self._run_sfc_scan)
        lay.addWidget(self._sfc_btn)

        self._restore_health_btn = QPushButton("🩹  DISM RestoreHealth")
        self._restore_health_btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /RestoreHealth\n"
            "Repairs component-store corruption ScanHealth detects. "
            "Can take 10-30 minutes and may download repair content. Asks first and needs "
            "administrator rights; this app has no undo for component-store repairs."
        )
        self._restore_health_btn.clicked.connect(self._run_restore_health)
        lay.addWidget(self._restore_health_btn)

        self._chkdsk_btn = QPushButton("🩹  Schedule CHKDSK")
        self._chkdsk_btn.setToolTip(
            "Runs: chkdsk C: /f /r /x\n"
            "Schedules a full disk check and repair for the next reboot. Asks first and needs "
            "administrator rights; the restart-time scan can take hours and its repairs have no undo here."
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
            from modules.system_health import servicing
            scheduled = servicing.chkdsk_scheduled(servicing.system_drive())
            self._servicing_out.setText(
                (f"CHKDSK scheduled for {servicing.system_drive()} -- confirmed in BootExecute. "
                 "Reboot to run it." if scheduled else
                 f"CHKDSK was NOT confirmed as scheduled (exit {result.returncode}): "
                 + ("the boot-time check list (BootExecute) could not be read."
                    if scheduled is None else
                    "the boot-time check list (BootExecute) shows no check queued.")
                 + " Run 'chkdsk /f /r' yourself in an elevated prompt, or "
                 "'chkntfs " + servicing.system_drive() + "' to see what Windows reports.")
                + f"\n{result.output[:500]}")
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

    def _on_tab_changed(self, index: int) -> None:
        if self._storage_boot_tab is not None and self._tabs.widget(index) is self._storage_boot_tab:
            if not self._storage_boot_tab.loaded:
                self._storage_boot_tab.load()

    def on_activate(self) -> None:
        self._refresh_findings()

    def on_stop(self) -> None:
        self.cancel_all_workers()
        if hasattr(self, "_storage_boot_tab") and self._storage_boot_tab is not None:
            self._storage_boot_tab.cancel()

    def get_status_info(self) -> str:
        return "System Health"
