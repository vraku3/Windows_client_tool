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
import time
from typing import Optional

from PyQt6.QtCore import QElapsedTimer, QThreadPool, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from core.worker import COMWorker
from core.semantic_colors import semantic
from modules.updates import run_checkpoint
from modules.updates.operation_guard import maintenance_worker
from .activity_widget import ActivityWidget

logger = logging.getLogger(__name__)

#: No log line for this long during a run is reported as a stall.
STALL_AFTER_S = 180


def format_running_status(phase: str, elapsed_s: float, idle_s: float = 0.0) -> str:
    text = f"{phase or 'Working'}… {int(elapsed_s) // 60}m {int(elapsed_s) % 60:02d}s"
    if idle_s >= STALL_AFTER_S:
        text += f" — no output for {int(idle_s) // 60}m"
    return text


class _RunAllTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self._workers: list = []
        self._last_data: dict = {}
        self._op_timer: Optional[QElapsedTimer] = None
        self._busy_owner = ""
        self._running = False
        self._phase = ""
        self._last_activity = 0.0
        self._setup_ui()

    def set_app(self, app) -> None:
        self.app = app
        self._activity.set_app(app)

    def set_maintenance_busy(self, owner) -> None:
        self._busy_owner = owner or ""
        self._sync_run_button()

    def _sync_run_button(self) -> None:
        self._run_btn.setEnabled(not self._busy_owner and not self._running)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        desc = QLabel(
            "Runs the checked stages in sequence. Windows Update installs and cleanup "
            "results are logged to run history; a maintenance report is generated at the end."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)
        self._checkpoint_lbl = QLabel()
        self._checkpoint_lbl.setWordWrap(True)
        self._checkpoint_lbl.setVisible(False)
        layout.addWidget(self._checkpoint_lbl)

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
        self._activity = ActivityWidget()
        layout.addWidget(self._activity, 1)

        self._run_btn.clicked.connect(self._do_run)
        self._stop_btn.clicked.connect(self._do_stop)
        self._report_btn.clicked.connect(self._do_report)
        self._watch = QTimer(self)
        self._watch.setInterval(5000)
        self._watch.timeout.connect(self._tick_watch)

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

    def refresh_checkpoint_note(self) -> None:
        """Say so when the last run never finished, without blocking anything."""
        run = run_checkpoint.unfinished(getattr(self.app, "app_data_dir", None))
        if run is None:
            self._checkpoint_lbl.setVisible(False)
            return
        rows = run_checkpoint.item_summary(run)[-5:]
        self._checkpoint_lbl.setText(
            f"A previous maintenance run did not finish (context: {run.get('context') or 'unknown'}, "
            f"started {run.get('started') or 'unknown'})."
            + (" Last recorded: " + "; ".join(rows) if rows else ""))
        self._checkpoint_lbl.setStyleSheet(f"color: {semantic('warning')};")
        self._checkpoint_lbl.setVisible(True)

    def _confirm_preflight(self) -> bool:
        """Show restart and previous-run evidence before anything installs."""
        from modules.updates.reboot_preflight import assess
        folder = getattr(self.app, "app_data_dir", None)
        try:
            preflight = assess(folder)
        except Exception:
            logger.warning("Could not build the run preflight", exc_info=True)
            return True
        if not preflight.should_ask:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Restart evidence")
        box.setText("A restart is pending." if preflight.reasons
                    else "A previous maintenance run did not finish.")
        box.setInformativeText("\n".join(preflight.evidence_lines()))
        run_anyway = box.addButton("Run anyway", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(run_anyway)
        box.exec()
        return box.clickedButton() is run_anyway

    def _do_run(self):
        if self.app is None:
            return
        stages = self._selected_stages()
        if not stages:
            self._status_lbl.setText("Select at least one stage.")
            return
        if not self._confirm_preflight():
            return

        self._running = True
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._report_btn.setEnabled(False)
        self._log.clear()
        self._status_lbl.setStyleSheet("")
        self._status_lbl.setText("Running…")
        self._phase = ""
        self._last_activity = time.monotonic()
        self._op_timer = QElapsedTimer()
        self._op_timer.start()
        self._watch.start()

        app = self.app

        # A cancelled Worker emits `cancelled`, never `result`: the stages
        # already run live here so Stop still records and reports them.
        data = {}
        lines = []

        @maintenance_worker("Updates install")
        def _run(worker):
            from modules.updates.stage_runners import STAGE_LABELS, STAGE_RUNNERS, finalize_run, write_session_log
            def log(line):
                lines.append(line)
                worker.signals.log_line.emit(line)
            run_checkpoint.begin(app.app_data_dir, stages, "run_all")
            for stage in stages:
                if worker.is_cancelled:
                    log("Cancelled.")
                    break
                log(f"--- stage: {STAGE_LABELS.get(stage, stage)} ---")
                fn = STAGE_RUNNERS[stage]
                try:
                    data[stage] = fn(
                        app,
                        log,
                        lambda: worker.is_cancelled,
                    )
                except Exception as e:
                    logger.exception("Stage %s failed", stage)
                    log(f"Stage {stage} failed: {e}")
                    data[stage] = {"error": str(e)}
                if any(r.get("state") == "STILL RUNNING" for r in data[stage].get("results", [])):
                    log("Remaining stages stopped: installer completion unknown.")
                    break
            finalize_run(data)
            try:
                data["session_log"] = write_session_log(app.app_data_dir, data, lines)
            except OSError:
                logger.warning("Could not write maintenance session log", exc_info=True)
            run_checkpoint.finish(app.app_data_dir,
                                  "cancelled" if worker.is_cancelled else "completed")
            return dict(data)

        w = COMWorker(_run)
        w.signals.log_line.connect(self._on_log)
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

    def _on_log(self, line: str) -> None:
        """Show log lines; turn "Phase: x" markers into the running status."""
        self._last_activity = time.monotonic()
        if line.startswith("Phase: "):
            self._phase = line[len("Phase: "):].strip().capitalize()
            self._update_running_status()
            return
        self._log.appendPlainText(line)

    def _update_running_status(self) -> None:
        if not self._running or self._op_timer is None:
            return
        elapsed = self._op_timer.elapsed() / 1000
        idle = time.monotonic() - self._last_activity
        self._status_lbl.setText(format_running_status(self._phase, elapsed, idle))
        self._status_lbl.setStyleSheet(
            f"color: {semantic('warning')};" if idle >= STALL_AFTER_S else "")

    def _tick_watch(self) -> None:
        self._update_running_status()

    def _on_run_done(self, data: dict, cancelled: bool = False) -> None:
        self._activity.refresh()
        self._running = False
        self._watch.stop()
        self._run_btn.setEnabled(not self._busy_owner)
        self._stop_btn.setEnabled(False)
        self._report_btn.setEnabled(True)
        self._status_lbl.setStyleSheet("")
        self._last_data = self._normalize_data(data)
        from modules.updates.stage_runners import outstanding_text
        self._log.setPlainText(outstanding_text(data.get("outstanding") or {}) + "\n\n" + self._log.toPlainText())
        held = len(self._last_data.get("wu_held") or [])
        held_note = f" {held} AMD display driver update(s) held on purpose." if held else ""
        failures = self._last_data.get("failures", [])
        self._status_lbl.setText(("Stopped -- the stages already run are reported."
                                  if cancelled else f"Run incomplete: {len(failures)} problem(s)." if failures
                                  else "Run complete.") + held_note)

        from modules.updates.history_writer import append_run
        append_run(
            self.app.app_data_dir,
            freed_bytes=self._last_data.get("cleanup_freed", 0),
            updates_installed=self._last_data.get("wu_installed", 0),
            winget_count=self._last_data.get("apps_confirmed"),
            apps_need_you=self._last_data.get("apps_need_you"),
            machine_restart_pending=self._last_data.get("machine_restart_pending"),
            pending_restart=self._last_data.get("wu_pending_restart", 0),
        )

        self._do_report()
        self._maybe_toast()

    def _on_run_error(self, err: str) -> None:
        self._activity.refresh()
        self._running = False
        self._watch.stop()
        self._run_btn.setEnabled(not self._busy_owner)
        self._stop_btn.setEnabled(False)
        self._status_lbl.setStyleSheet("")
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
        from modules.updates.unattended_runner import completion_message
        self.app.event_bus.publish(
            NOTIFY_BALLOON,
            BalloonNotifyData(
                title="Maintenance run complete",
                message=completion_message(self._last_data, "Updates module",
                                           self._last_data.get("machine_restart_pending"),
                                           self._last_data.get("failures")),
            ),
        )

    def _cancel_all(self) -> None:
        self._watch.stop()
        for w in self._workers:
            w.cancel()
        self._workers.clear()
