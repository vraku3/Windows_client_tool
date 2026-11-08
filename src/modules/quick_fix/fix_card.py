import logging
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QFrame, QDialog, QLineEdit, QApplication,
)
from PyQt6.QtCore import QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QTextCharFormat, QColor, QTextCursor

from core.worker import Worker
from core.long_op_pool import get_long_op_pool
from modules.quick_fix.fix_actions import FixAction

logger = logging.getLogger(__name__)


class FixCard(QFrame):
    _line = pyqtSignal(str)
    history_changed = pyqtSignal()

    def __init__(self, action: FixAction, last_entry: dict = None,
                 recurring_count: int = 0, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._action = action
        self._running = False
        self._worker = None
        self._last_entry = last_entry
        self._recurring_count = recurring_count
        self._thread_pool = QThreadPool.globalInstance()
        self._start_time: float = 0.0
        self._output_lines: int = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._card_state = "idle"
        self._setup_ui()
        self._apply_state_border()

    _RISK_COLORS = {"low": "#4CAF50", "medium": "#FF9800", "high": "#F44336"}
    _SCOPE_LABELS = {"local": "Local", "service": "Service", "system": "System"}
    _MAX_OUTPUT_LINES = 500
    _STATE_COLORS = {"idle": "#555", "running": "#2196F3", "success": "#4CAF50", "error": "#F44336"}

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_lbl = QLabel(self._action.title)
        font = title_lbl.font()
        font.setBold(True)
        title_lbl.setFont(font)
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        self._add_badges(title_row)
        layout.addLayout(title_row)

        desc = QLabel(self._action.description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        if self._action.duration_hint:
            dur = QLabel(f"⏱ {self._action.duration_hint}")
            dur.setStyleSheet("color: #888; font-size: 11px;")
            dur.setToolTip("Estimated duration")
            meta_row.addWidget(dur)
        if self._action.interruptions:
            intr = QLabel(f"⚡ {self._action.interruptions}")
            intr.setStyleSheet("color: #CC8800; font-size: 11px;")
            intr.setToolTip("What you will notice while this runs")
            meta_row.addWidget(intr)
        meta_row.addStretch()
        layout.addLayout(meta_row)

        self._run_btn = QPushButton("Run")
        from .help_text import ACTIONS
        prompt = (" Asks for confirmation before running." if self._action.confirm_text
                  else " Starts immediately without another confirmation.")
        self._run_btn.setToolTip(ACTIONS.get(self._action.key, self._action.description + " There is no app undo.") + prompt + " Requires administrator rights.")
        self._run_btn.setFixedWidth(80)
        layout.addWidget(self._run_btn)

        self._status = QLabel("")
        self._status.setToolTip(
            "Recorded outcome or current precondition for this repair. "
            "Click to open this action's history.")
        self._status.setStyleSheet("color: gray; font-size: 11px; cursor: pointer;")
        self._status.mousePressEvent = lambda _evt: self._show_action_history()
        layout.addWidget(self._status)
        self._refresh_status()

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setMaximumHeight(150)
        mono_font = QFont("Consolas", 8)
        self._output.setFont(mono_font)
        self._output.hide()
        layout.addWidget(self._output)

        output_bar = QHBoxLayout()
        output_bar.setSpacing(4)
        self._copy_btn = QPushButton("Copy Output")
        self._copy_btn.setFixedWidth(90)
        self._copy_btn.setToolTip("Copy the output text to the clipboard")
        self._copy_btn.hide()
        self._copy_btn.clicked.connect(self._copy_output)
        output_bar.addWidget(self._copy_btn)
        self._line_count_lbl = QLabel("")
        self._line_count_lbl.setStyleSheet("color: #888; font-size: 10px;")
        self._line_count_lbl.hide()
        output_bar.addWidget(self._line_count_lbl)
        output_bar.addStretch()
        layout.addLayout(output_bar)

        self._run_btn.clicked.connect(self._run)
        self._line.connect(self._append_output)

    def _add_badges(self, row: QHBoxLayout) -> None:
        action = self._action
        risk_color = self._RISK_COLORS.get(action.risk, "#888")
        scope_label = self._SCOPE_LABELS.get(action.scope, action.scope)
        badge_style = (
            "font-size: 10px; padding: 1px 5px; border-radius: 3px; "
            "color: white; background: {bg};"
        )
        risk_badge = QLabel(action.risk.capitalize())
        risk_badge.setStyleSheet(badge_style.format(bg=risk_color))
        risk_badge.setToolTip(f"Risk level: {action.risk}")
        row.addWidget(risk_badge)
        scope_badge = QLabel(scope_label)
        scope_badge.setStyleSheet(badge_style.format(bg="#5C6BC0"))
        scope_badge.setToolTip(f"Scope: {scope_label} — how far this action reaches")
        row.addWidget(scope_badge)
        if action.reboot_required:
            reboot_badge = QLabel("Reboot")
            reboot_badge.setStyleSheet(badge_style.format(bg="#E53935"))
            reboot_badge.setToolTip("A reboot is required to complete this action")
            row.addWidget(reboot_badge)

    def _apply_state_border(self) -> None:
        color = self._STATE_COLORS.get(self._card_state, "#555")
        self.setStyleSheet(f"FixCard {{ border-left: 3px solid {color}; }}")

    def _set_card_state(self, state: str) -> None:
        self._card_state = state
        self._apply_state_border()

    def _append_output(self, line: str, *, force: bool = False) -> None:
        if not force and self._output_lines >= self._MAX_OUTPUT_LINES:
            if self._output_lines == self._MAX_OUTPUT_LINES:
                self._output.appendPlainText(
                    f"— output capped at {self._MAX_OUTPUT_LINES} lines —")
                self._output_lines += 1
            return
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self._output_lines > 0:
            cursor.insertText("\n")
        fmt = QTextCharFormat()
        upper = line.upper()
        if upper.startswith("ERROR") or "FAILED" in upper:
            fmt.setForeground(QColor("#F44336"))
        elif upper.startswith("WARNING") or "WARN" in upper[:12]:
            fmt.setForeground(QColor("#FF9800"))
        cursor.insertText(line, fmt)
        self._output_lines += 1
        self._line_count_lbl.setText(f"{self._output_lines} lines")
        sb = self._output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _copy_output(self) -> None:
        text = self._output.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self._copy_btn.setText("Copied!")
            QTimer.singleShot(1500, self._reset_copy_btn)

    def _reset_copy_btn(self) -> None:
        try:
            from PyQt6 import sip
            if sip.isdeleted(self._copy_btn):
                return
        except ImportError:
            pass
        self._copy_btn.setText("Copy Output")

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        return f"{m}m {s:02d}s"

    def _tick_elapsed(self) -> None:
        elapsed = time.monotonic() - self._start_time
        self._status.setText(f"Running… {self._fmt_elapsed(elapsed)}")

    def _stop_elapsed(self) -> None:
        self._elapsed_timer.stop()

    def _refresh_status(self) -> None:
        parts = []
        if self._recurring_count:
            parts.append(
                f"⚠ Run {self._recurring_count} times in the last 14 days — "
                f"if this keeps happening, the underlying cause needs a look.")
        if self._action.precondition is not None:
            msg = self._action.precondition()
            if msg is not None:
                parts.append(msg)
                self._status.setText(" ".join(parts))
                return
        if self._last_entry is not None:
            parts.append(
                f"Last run: {self._last_entry['at']} — {self._last_entry['outcome']}")
        self._status.setText(" ".join(parts))

    def _run(self):
        if self._running:
            return
        action = self._action

        if action.precondition is not None:
            msg = action.precondition()
            if msg is not None:
                self._status.setText(msg)
                return

        if action.confirm_text is not None:
            from PyQt6.QtWidgets import QMessageBox
            mb = QMessageBox(self)
            mb.setWindowTitle(action.title)
            mb.setIcon(QMessageBox.Icon.Warning)
            mb.setText(action.confirm_text)
            mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if mb.exec() != QMessageBox.StandardButton.Ok:
                return

        self._running = True
        self._run_btn.setEnabled(False)
        self._set_card_state("running")
        self._output.clear()
        self._output_lines = 0
        self._output.show()
        self._copy_btn.hide()
        self._line_count_lbl.setText("")
        self._line_count_lbl.show()
        self._start_time = time.monotonic()
        self._status.setText("Running… 0s")
        self._elapsed_timer.start()

        def append(line: str):
            self._line.emit(line)

        def do_work(_w):
            from modules.quick_fix import fix_actions
            self._worker = _w
            fix_actions.begin_tracking()
            try:
                action.fn(append)
            finally:
                failed = fix_actions.end_tracking()
            if failed:
                raise RuntimeError(f"{len(failed)} step(s) failed: " + "; ".join(failed[:3]))

        self._worker = Worker(do_work)
        w = self._worker
        w.signals.result.connect(lambda _r, w=w: self._on_done(w))
        w.signals.error.connect(lambda err, w=w: self._on_error(err, w))
        pool = get_long_op_pool() if action.long_running else self._thread_pool
        pool.start(w)

    def _on_done(self, worker) -> None:
        if self._worker is not worker:
            return
        self._stop_elapsed()
        elapsed = self._fmt_elapsed(time.monotonic() - self._start_time)
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._set_card_state("success")
        if self._output_lines:
            self._copy_btn.show()
        from modules.quick_fix import quick_fix_history
        self._last_entry = quick_fix_history.record(self._action.title, "ok")
        self.history_changed.emit()
        self._recurring_count = quick_fix_history.recurring_actions().get(
            self._action.title, 0)
        self._refresh_status()
        self._status.setText(self._status.text() + f" ✓ Completed in {elapsed}")
        if self._output_lines:
            QTimer.singleShot(2000, self._collapse_output)

    def _collapse_output(self) -> None:
        if self._card_state == 'success' and self._output.isVisible():
            self._output.hide()

    def _on_error(self, error_str: str, worker) -> None:
        if self._worker is not worker:
            return
        self._stop_elapsed()
        elapsed = self._fmt_elapsed(time.monotonic() - self._start_time)
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._set_card_state("error")
        self._append_output(f"ERROR: {error_str}", force=True)
        self._copy_btn.show()
        from modules.quick_fix import quick_fix_history
        self._last_entry = quick_fix_history.record(self._action.title, "error")
        self.history_changed.emit()
        self._recurring_count = quick_fix_history.recurring_actions().get(
            self._action.title, 0)
        self._refresh_status()
        self._status.setText(self._status.text() + f" ✗ Failed after {elapsed}")

    def _show_action_history(self) -> None:
        QuickFixHistoryDialog(self, action_filter=self._action.title).exec()

    def cancel(self) -> None:
        if self._worker is not None and self._running:
            self._stop_elapsed()
            from core.worker import retire
            retire([self._worker])
            self._running = False
            self._worker = None
            self._run_btn.setEnabled(True)
            self._set_card_state("idle")
            self._append_output("Cancelled.")
            if self._output_lines:
                self._copy_btn.show()
            from modules.quick_fix import quick_fix_history
            self._last_entry = quick_fix_history.record(self._action.title, "cancelled")
            self._recurring_count = quick_fix_history.recurring_actions().get(
                self._action.title, 0)
            self._refresh_status()


class QuickFixHistoryDialog(QDialog):

    _OUTCOME_COLORS = {"ok": "#4CAF50", "error": "#F44336", "cancelled": "#FF9800"}

    def __init__(self, parent=None, action_filter: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Quick Fix History")
        self.resize(640, 480)
        root = QVBoxLayout(self)
        from ui.module_intro import module_intro
        root.addWidget(module_intro(
            "Read the recent repair outcomes recorded by this app. "
            "Nothing here changes Windows or undoes completed repairs; "
            "administrator rights are not needed to read local history."))

        from PyQt6.QtWidgets import QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView

        filter_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by action name…")
        self._search.setToolTip("Filter history entries by action name")
        if action_filter:
            self._search.setText(action_filter)
        self._search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._search)

        self._outcome_filter = QComboBox()
        self._outcome_filter.addItems(["All outcomes", "ok", "error", "cancelled"])
        self._outcome_filter.setToolTip("Filter by outcome")
        self._outcome_filter.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self._outcome_filter)
        root.addLayout(filter_row)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Time", "Action", "Outcome"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)

        from modules.quick_fix import quick_fix_history
        self._entries = quick_fix_history.recent(limit=200)
        self._populate()
        root.addWidget(self._table)

        self._summary = QLabel("")
        self._summary.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(self._summary)
        self._update_summary()

        btn_row = QHBoxLayout()
        export_btn = QPushButton("Export CSV")
        export_btn.setToolTip("Export the filtered history to a CSV file")
        export_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(export_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setToolTip(
            "Close this history window; no repair runs and the recorded history is kept.")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _populate(self) -> None:
        from PyQt6.QtWidgets import QTableWidgetItem
        query = self._search.text().strip().lower()
        outcome_val = self._outcome_filter.currentText()
        filtered = []
        for entry in self._entries:
            action = entry.get("action", "")
            outcome = entry.get("outcome", "")
            if query and query not in action.lower():
                continue
            if outcome_val != "All outcomes" and outcome != outcome_val:
                continue
            filtered.append(entry)
        self._table.setRowCount(len(filtered))
        for row, entry in enumerate(filtered):
            time_item = QTableWidgetItem(entry.get("at", ""))
            action_item = QTableWidgetItem(entry.get("action", ""))
            outcome = entry.get("outcome", "")
            outcome_item = QTableWidgetItem(outcome)
            color = self._OUTCOME_COLORS.get(outcome, "#888")
            outcome_item.setForeground(QColor(color))
            self._table.setItem(row, 0, time_item)
            self._table.setItem(row, 1, action_item)
            self._table.setItem(row, 2, outcome_item)

    def _apply_filter(self) -> None:
        self._populate()
        self._update_summary()

    def _export_csv(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        import csv
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Quick Fix History", "quick_fix_history.csv",
            "CSV Files (*.csv)")
        if not path:
            return
        rows = []
        for row in range(self._table.rowCount()):
            rows.append([
                self._table.item(row, c).text() if self._table.item(row, c) else ""
                for c in range(3)
            ])
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["Time", "Action", "Outcome"])
                w.writerows(rows)
            self._summary.setText(f"Exported {len(rows)} entries to {path}")
        except OSError as e:
            self._summary.setText(f"Export failed: {e}")

    def _update_summary(self) -> None:
        total = len(self._entries)
        shown = self._table.rowCount()
        ok = sum(1 for e in self._entries if e.get("outcome") == "ok")
        err = sum(1 for e in self._entries if e.get("outcome") == "error")
        self._summary.setText(
            f"Showing {shown} of {total} entries — "
            f"{ok} succeeded, {err} failed")
