"""_RunAllTab — runs the checked maintenance stages (Windows Update / winget /
Microsoft Store / Cleanup-safe / DISM) in sequence, in one background worker,
then writes run history and an HTML maintenance report.

Cancellation is checked between AND within stages — stage_runners.py's per-
item loops (e.g. installing updates one at a time) take the same
`worker.is_cancelled` check, so Stop takes effect before the next package,
not only between stages. A package already installing is allowed to finish:
killing an installer mid-write can leave an application half-installed.
"""
import logging
import os
from typing import Optional

from PyQt6.QtCore import QElapsedTimer, QThreadPool
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from core.worker import COMWorker

logger = logging.getLogger(__name__)


class _RunAllTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self._workers: list = []
        self._last_data: dict = {}
        self._op_timer: Optional[QElapsedTimer] = None
        self._setup_ui()

    def set_app(self, app) -> None:
        self.app = app

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        desc = QLabel(
            "Runs the checked stages in sequence. Windows Update installs and cleanup "
            "results are logged to run history; a maintenance report is generated at the end."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        checks_row = QHBoxLayout()
        self._chk_wu = QCheckBox("Windows Update")
        self._chk_wu.setChecked(True)
        self._chk_winget = QCheckBox("winget")
        self._chk_winget.setChecked(True)
        self._chk_store = QCheckBox("Microsoft Store")
        self._chk_store.setChecked(True)
        self._chk_cleanup = QCheckBox("Cleanup (safe)")
        self._chk_cleanup.setChecked(True)
        self._chk_dism = QCheckBox("DISM WinSxS cleanup")
        self._chk_dism.setChecked(False)
        for c in (self._chk_wu, self._chk_winget, self._chk_store, self._chk_cleanup, self._chk_dism):
            checks_row.addWidget(c)
        checks_row.addStretch()
        layout.addLayout(checks_row)

        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Run All")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._report_btn = QPushButton("Generate Maintenance Report")
        self._report_btn.setEnabled(False)
        self._status_lbl = QLabel("")
        btn_row.addWidget(self._run_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addWidget(self._report_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._status_lbl)
        layout.addLayout(btn_row)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        layout.addWidget(self._log, 1)

        self._run_btn.clicked.connect(self._do_run)
        self._stop_btn.clicked.connect(self._do_stop)
        self._report_btn.clicked.connect(self._do_report)

    def _selected_stages(self):
        stages = []
        if self._chk_wu.isChecked():
            stages.append("wu")
        if self._chk_winget.isChecked():
            stages.append("winget")
        if self._chk_store.isChecked():
            stages.append("store")
        if self._chk_cleanup.isChecked():
            stages.append("cleanup")
        if self._chk_dism.isChecked():
            stages.append("dism")
        return stages

    def _do_run(self):
        if self.app is None:
            return
        stages = self._selected_stages()
        if not stages:
            self._status_lbl.setText("Select at least one stage.")
            return

        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._report_btn.setEnabled(False)
        self._log.clear()
        self._status_lbl.setText("Running…")
        self._op_timer = QElapsedTimer()
        self._op_timer.start()

        app = self.app

        # A cancelled Worker emits `cancelled`, never `result`: the stages
        # already run live here so Stop still records and reports them.
        data = {}

        def _run(worker):
            from modules.updates.stage_runners import STAGE_LABELS, STAGE_RUNNERS
            for stage in stages:
                if worker.is_cancelled:
                    worker.signals.log_line.emit("Cancelled.")
                    break
                worker.signals.log_line.emit(f"--- stage: {STAGE_LABELS.get(stage, stage)} ---")
                fn = STAGE_RUNNERS[stage]
                try:
                    data[stage] = fn(
                        app,
                        lambda line: worker.signals.log_line.emit(line),
                        lambda: worker.is_cancelled,
                    )
                except Exception as e:
                    logger.exception("Stage %s failed", stage)
                    worker.signals.log_line.emit(f"Stage {stage} failed: {e}")
                    data[stage] = {}
            return dict(data)

        w = COMWorker(_run)
        w.signals.log_line.connect(self._log.appendPlainText)
        w.signals.result.connect(self._on_run_done)
        w.signals.error.connect(self._on_run_error)
        def _cancelled():
            from core.widget_life import widget_is_valid
            if widget_is_valid(self):       # a closure: Qt cannot auto-disconnect it
                self._on_run_done(dict(data), cancelled=True)
        w.signals.cancelled.connect(_cancelled)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _do_stop(self):
        for w in self._workers:
            w.cancel()
        self._stop_btn.setEnabled(False)

    def _on_run_done(self, data: dict, cancelled: bool = False) -> None:
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._report_btn.setEnabled(True)
        self._last_data = self._normalize_data(data)
        held = len(self._last_data.get("wu_held") or [])
        held_note = f" {held} AMD display driver update(s) held on purpose." if held else ""
        self._status_lbl.setText(("Stopped -- the stages already run are reported."
                                  if cancelled else "Run complete.") + held_note)

        from modules.updates.history_writer import append_run
        append_run(
            self.app.app_data_dir,
            freed_bytes=self._last_data.get("cleanup_freed", 0),
            updates_installed=self._last_data.get("wu_installed", 0),
            winget_count=len(self._last_data.get("winget_results", [])),
            pending_restart=self._last_data.get("wu_pending_restart", 0),
        )

        self._do_report()
        self._maybe_toast()

    def _on_run_error(self, err: str) -> None:
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status_lbl.setText(f"Error: {err}")
        self._log.appendPlainText(f"Error: {err}")

    def _normalize_data(self, data: dict) -> dict:
        from modules.updates.stage_runners import normalize_stage_data
        return normalize_stage_data(data)

    def _do_report(self):
        if self.app is None:
            return
        from modules.updates.history_writer import load_history
        from modules.updates.report_generator import render_update_report_html, write_report

        history = load_history(self.app.app_data_dir)
        html_text = render_update_report_html(self._last_data, history)
        path = write_report(self.app.app_data_dir, html_text)
        self._report_btn.setEnabled(True)
        if path is None:
            self._log.appendPlainText("Report not written — low disk space (see log for details).")
            return
        self._log.appendPlainText(f"Report written: {path}")
        try:
            os.startfile(path)  # noqa: S606 — user-initiated, opening our own report
        except Exception:
            logger.warning("Could not auto-open report %s", path, exc_info=True)

    def _maybe_toast(self):
        if self._op_timer is None or self.app is None:
            return
        if self._op_timer.elapsed() <= 20_000:
            return
        if not self.app.config.get("app.toast_on_long_ops", True):
            return
        from core.events import NOTIFY_BALLOON, BalloonNotifyData
        self.app.event_bus.publish(
            NOTIFY_BALLOON,
            BalloonNotifyData(
                title="Maintenance run complete",
                message="Run All finished — see the Updates module for the report.",
            ),
        )

    def _cancel_all(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()
