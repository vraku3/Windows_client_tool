"""Session controls, profiles, protection, audit and recovery for Game Mode."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QMessageBox, QPlainTextEdit,
                             QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)
from PyQt6.QtCore import Qt

from core.table_ui import set_role
from core.worker import Worker
from ui.error_banner import ErrorBanner
from ui.table_style import apply_table_style

from .engine import game_config, game_library, health_audit, session

logger = logging.getLogger(__name__)

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_schedule_text(text: str):
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            days_part, rest = line.split(None, 1)
            times, _sep, profile = rest.partition(" ")
            start, _dash, end = times.partition("-")
            days = []
            for token in days_part.split(","):
                token = token.strip()[:3].title()
                if token not in DAYS:
                    raise ValueError(token)
                days.append(DAYS.index(token))
        except ValueError:
            logger.warning("Game Mode: ignoring schedule line %r", line)
            continue
        row = game_config.normalize_schedule(
            {"days": days, "start": start.strip(), "end": end.strip(),
             "profile": profile.strip()})
        if row:
            rows.append(row)
    return rows


def format_schedule_text(schedules) -> str:
    lines = []
    for row in schedules or []:
        days = ",".join(DAYS[day] for day in row.get("days", []))
        lines.append(f"{days} {row['start']}-{row['end']} {row.get('profile', '')}".strip())
    return "\n".join(lines)


class SessionTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._games = []
        layout = QVBoxLayout(self)
        self._error = ErrorBanner("", self)
        self._error.hide()
        layout.addWidget(self._error)
        self._status = QLabel("Game Mode is OFF.", self)
        set_role(self._status, "statusInfo")
        layout.addWidget(self._status)

        controls = QGroupBox("Session controls", self)
        form = QFormLayout(controls)
        self._pause_btn = QPushButton("Pause enforcement 30 min", controls)
        self._pause_btn.clicked.connect(lambda: self.host._pause_enforcement(30))
        self._resume_btn = QPushButton("Resume enforcement", controls)
        self._resume_btn.clicked.connect(lambda: self.host._pause_enforcement(0))
        pause_row = QHBoxLayout()
        pause_row.addWidget(self._pause_btn)
        pause_row.addWidget(self._resume_btn)
        pause_row.addStretch()
        form.addRow("Enforcement", pause_row)
        self._auto_spin = QSpinBox(controls)
        self._auto_spin.setRange(0, 24 * 60)
        self._auto_spin.setSuffix(" min (0 = never)")
        self._auto_spin.valueChanged.connect(self._auto_changed)
        form.addRow("Auto-OFF after", self._auto_spin)
        self._restore_cb = QCheckBox("Create a restore point before ON", controls)
        self._restore_cb.toggled.connect(self._setting_changed)
        form.addRow("Safety", self._restore_cb)
        self._notify_cb = QCheckBox("Show tray notifications", controls)
        self._notify_cb.toggled.connect(self._setting_changed)
        form.addRow("", self._notify_cb)
        self._force_cb = QCheckBox("Force-close programs that ignore the polite close", controls)
        self._force_cb.toggled.connect(self._setting_changed)
        form.addRow("", self._force_cb)
        self._grace_spin = QSpinBox(controls)
        self._grace_spin.setRange(0, 120)
        self._grace_spin.setSuffix(" s grace")
        self._grace_spin.valueChanged.connect(self._setting_changed)
        form.addRow("Close grace", self._grace_spin)
        self._priority = QComboBox(controls)
        for label, value in (("Off", "off"), ("Above Normal", "above"), ("High", "high")):
            self._priority.addItem(label, value)
        self._priority.currentIndexChanged.connect(self._setting_changed)
        form.addRow("Bound game priority", self._priority)
        self._ac_cb = QCheckBox("Warn before Maximum on battery", controls)
        self._ac_cb.toggled.connect(self._setting_changed)
        form.addRow("Power guard", self._ac_cb)
        self._retention_spin = QSpinBox(controls)
        self._retention_spin.setRange(1, 1000)
        self._retention_spin.setSuffix(" sessions kept")
        self._retention_spin.valueChanged.connect(self._setting_changed)
        form.addRow("History", self._retention_spin)
        self._bound = QComboBox(controls)
        self._bound.setEditable(True)
        self._bound.currentTextChanged.connect(self._bound_changed)
        self._scan_btn = QPushButton("Rescan games", controls)
        self._scan_btn.clicked.connect(self._scan_games)
        bound_row = QHBoxLayout()
        bound_row.addWidget(self._bound, 1)
        bound_row.addWidget(self._scan_btn)
        form.addRow("Bound game", bound_row)
        self._armed_cb = QCheckBox("Enter Game Mode when the bound game starts", controls)
        self._armed_cb.toggled.connect(self._setting_changed)
        form.addRow("", self._armed_cb)
        layout.addWidget(controls)

        profiles = QGroupBox("Profiles", self)
        prow = QHBoxLayout(profiles)
        self._profile_combo = QComboBox(profiles)
        self._profile_combo.setEditable(True)
        self._profile_save = QPushButton("Save current", profiles)
        self._profile_save.clicked.connect(self._save_profile)
        self._profile_load = QPushButton("Apply", profiles)
        self._profile_load.clicked.connect(self._load_profile)
        self._profile_delete = QPushButton("Delete", profiles)
        self._profile_delete.clicked.connect(self._delete_profile)
        self._resume_btn = QPushButton("Resume last", profiles)
        self._resume_btn.setToolTip(
            "Load the last session's choices and run the normal ON preview.")
        self._resume_btn.clicked.connect(self._resume_last)
        for widget in (self._profile_combo, self._profile_save, self._profile_load,
                       self._profile_delete, self._resume_btn):
            prow.addWidget(widget)
        layout.addWidget(profiles)

        protected = QGroupBox("User protection", self)
        pform = QFormLayout(protected)
        self._protected = QPlainTextEdit(protected)
        self._protected.setMaximumHeight(90)
        self._protected.setPlaceholderText("One pattern per line: exe name or title text")
        self._publishers = QPlainTextEdit(protected)
        self._publishers.setMaximumHeight(60)
        self._publishers.setPlaceholderText("One publisher/company name per line")
        self._protected_save = QPushButton("Save protection", protected)
        self._protected_save.clicked.connect(self._save_protection)
        pform.addRow("Never close", self._protected)
        pform.addRow("Never close publisher", self._publishers)
        pform.addRow("", self._protected_save)
        layout.addWidget(protected)

        schedules = QGroupBox("Scheduled windows", self)
        sform = QFormLayout(schedules)
        self._schedule_text = QPlainTextEdit(schedules)
        self._schedule_text.setMaximumHeight(80)
        self._schedule_text.setPlaceholderText("Mon,Fri 20:00-23:00 Profile")
        self._schedule_save = QPushButton("Save windows", schedules)
        self._schedule_save.clicked.connect(self._save_schedules)
        sform.addRow("Weekly windows", self._schedule_text)
        sform.addRow("", self._schedule_save)
        layout.addWidget(schedules)

        health = QGroupBox("Health audit", self)
        hlay = QVBoxLayout(health)
        self._health_table = QTableWidget(0, 4, health)
        self._health_table.setHorizontalHeaderLabels(
            ["Change", "Expected", "Actual", "Status"])
        self._health_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self._health_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        apply_table_style(self._health_table)
        hrow = QHBoxLayout()
        self._audit_btn = QPushButton("Check state", health)
        self._audit_btn.clicked.connect(self._audit)
        self._repair_btn = QPushButton("Repair drift", health)
        self._repair_btn.clicked.connect(self._repair)
        hrow.addWidget(self._audit_btn)
        hrow.addWidget(self._repair_btn)
        hrow.addStretch()
        hlay.addWidget(self._health_table)
        hlay.addLayout(hrow)
        layout.addWidget(health)

        journal = QGroupBox("Active changes", self)
        jlay = QVBoxLayout(journal)
        self._journal_table = QTableWidget(0, 3, journal)
        self._journal_table.setHorizontalHeaderLabels(["Change", "Status", "Label"])
        self._journal_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        apply_table_style(self._journal_table)
        jrow = QHBoxLayout()
        self._undo_btn = QPushButton("Undo selected", journal)
        self._undo_btn.clicked.connect(self._undo)
        refresh = QPushButton("Refresh", journal)
        refresh.clicked.connect(self.refresh)
        jrow.addWidget(self._undo_btn)
        jrow.addWidget(refresh)
        jrow.addStretch()
        jlay.addWidget(self._journal_table)
        jlay.addLayout(jrow)
        layout.addWidget(journal)

        lower = QHBoxLayout()
        self._export_btn = QPushButton("Export settings", self)
        self._export_btn.clicked.connect(self._export_settings)
        self._import_btn = QPushButton("Import settings", self)
        self._import_btn.clicked.connect(self._import_settings)
        self._recover_btn = QPushButton("Recovery details", self)
        self._recover_btn.clicked.connect(self._show_recovery)
        lower.addWidget(self._export_btn)
        lower.addWidget(self._import_btn)
        lower.addWidget(self._recover_btn)
        lower.addStretch()
        layout.addLayout(lower)
        layout.addStretch()

    # ---- refresh ------------------------------------------------------------------------------
    def refresh(self) -> None:
        s = self.host._settings
        self._auto_spin.blockSignals(True)
        self._grace_spin.blockSignals(True)
        self._auto_spin.setValue(int(s.auto_off_minutes))
        self._grace_spin.setValue(int(s.close_grace_s))
        self._auto_spin.blockSignals(False)
        self._grace_spin.blockSignals(False)
        self._restore_cb.setChecked(bool(s.restore_point))
        self._notify_cb.setChecked(bool(s.notifications))
        self._force_cb.setChecked(bool(s.force_close))
        self._ac_cb.setChecked(bool(s.ac_guard))
        self._retention_spin.blockSignals(True)
        self._retention_spin.setValue(int(s.retention))
        self._retention_spin.blockSignals(False)
        self._armed_cb.setChecked(bool(s.auto_on_armed))
        index = self._priority.findData(s.priority_boost)
        self._priority.setCurrentIndex(max(0, index))
        self._bound.blockSignals(True)
        self._bound.setCurrentText(s.bound_game)
        self._bound.blockSignals(False)
        self._protected.setPlainText("\n".join(game_config.load_protected(self.host._config())))
        publishers = self.host._config().get("modules.game_mode.protected_publishers", []) \
            if self.host._config() is not None else []
        self._publishers.setPlainText("\n".join(str(p) for p in publishers or []))
        self._schedule_text.setPlainText(
            format_schedule_text(game_config.load_schedules(self.host._config())))
        self._profile_combo.clear()
        self._profile_combo.addItems(sorted(game_config.load_profiles(self.host._config())))
        active = self.host._journal.active
        elapsed = session.age_text(self.host._journal) if active else "OFF"
        paused = self.host._pause.paused()
        self._status.setText(
            f"{elapsed}    preset {self.host._choices.preset}"
            + ("    enforcement PAUSED" if paused else "")
            + ("    bound: " + os.path.basename(s.bound_game) if s.bound_game else ""))
        self._pause_btn.setEnabled(active)
        self._resume_btn.setEnabled(active and paused)
        self._undo_btn.setEnabled(active)
        self._repair_btn.setEnabled(active)
        self._render_journal()
        self._audit()

    def _setting_changed(self, *_args) -> None:
        s = self.host._settings
        s.auto_off_minutes = self._auto_spin.value()
        s.restore_point = self._restore_cb.isChecked()
        s.notifications = self._notify_cb.isChecked()
        s.force_close = self._force_cb.isChecked()
        s.close_grace_s = float(self._grace_spin.value())
        s.ac_guard = self._ac_cb.isChecked()
        s.retention = self._retention_spin.value()
        s.auto_on_armed = self._armed_cb.isChecked()
        s.priority_boost = self._priority.currentData() or "off"
        self.host._save_settings()
        self.host._show_state()

    def _auto_changed(self, _value) -> None:
        self._setting_changed()

    def _bound_changed(self, text: str) -> None:
        self.host._settings.bound_game = text.strip()
        self.host._save_settings()

    def _scan_games(self) -> None:
        roots = []
        for variable, relative in (
            ("ProgramFiles(x86)", ("Steam",)),
            ("ProgramFiles", ("Steam",)),
        ):
            base = os.environ.get(variable)
            if base:
                roots.append(str(Path(base).joinpath(*relative)))
        epic = os.environ.get("ProgramData")
        manifests = str(Path(epic) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests") \
            if epic else ""
        self._games = game_library.scan_games(
            next((r for r in roots if Path(r).exists()), None),
            manifests if manifests and Path(manifests).exists() else None)
        self._bound.blockSignals(True)
        current = self._bound.currentText()
        self._bound.clear()
        self._bound.addItem("")
        for game in self._games:
            self._bound.addItem(f"{game.name}  -  {game.source}", game.exe)
        if current:
            self._bound.setCurrentText(current)
        self._bound.blockSignals(False)
        if not self._games:
            self._error.set_error("No Steam/Epic games found; type an exe path above.")

    def _save_profile(self) -> None:
        name = self._profile_combo.currentText().strip()
        if not name:
            return
        try:
            game_config.save_profile(self.host._config(), name, self.host._choices.to_config())
        except ValueError as exc:
            QMessageBox.warning(self, "Profile", str(exc))
            return
        self.host._config().save()
        self.refresh()

    def _load_profile(self) -> None:
        name = self._profile_combo.currentText().strip()
        saved = game_config.load_profiles(self.host._config()).get(name)
        if not saved:
            return
        self.host._choices = session.choices_from_config(saved)
        self.host._apply_settings_to_choices()
        self.host._save_choices()
        self.host._refill_ticks()
        self.host._sync_flag_boxes()
        self.refresh()

    def _delete_profile(self) -> None:
        name = self._profile_combo.currentText().strip()
        if not name:
            return
        game_config.delete_profile(self.host._config(), name)
        self.host._config().save()
        self.refresh()

    def _resume_last(self) -> None:
        config = self.host._config()
        saved = config.get("modules.game_mode.last_choices", None) if config else None
        if not isinstance(saved, dict):
            self._error.set_error("No previous session choices were saved.")
            return
        self.host._choices = session.choices_from_config(saved)
        self.host._apply_settings_to_choices()
        self.host._save_choices()
        self.host._refill_ticks()
        self.host._sync_flag_boxes()
        self.host._turn_on()

    def _save_protection(self) -> None:
        patterns = [line.strip() for line in self._protected.toPlainText().splitlines()
                    if line.strip()]
        game_config.save_protected(self.host._config(), patterns)
        publishers = [line.strip() for line in self._publishers.toPlainText().splitlines()
                      if line.strip()]
        self.host._config().set("modules.game_mode.protected_publishers", publishers)
        self.host._config().save()
        self.host._apply_settings_to_choices()
        self.host._save_choices()

    def _save_schedules(self) -> None:
        schedules = parse_schedule_text(self._schedule_text.toPlainText())
        game_config.save_schedules(self.host._config(), schedules)
        self.host._config().save()
        self._schedule_text.setPlainText(format_schedule_text(schedules))

    def _render_journal(self) -> None:
        actions = self.host._journal.actions if self.host._journal.active else []
        self._journal_table.setRowCount(len(actions))
        for row, entry in enumerate(actions):
            status = "undone" if entry.get("undone") else ("done" if entry.get("done") else "pending")
            values = [entry.get("kind", ""), status,
                      entry.get("label") or entry.get("name") or entry.get("exe", "")]
            for column, value in enumerate(values):
                self._journal_table.setItem(row, column, QTableWidgetItem(str(value or "")))

    def _audit(self) -> None:
        try:
            rows = health_audit.audit(self.host._system, self.host._journal.actions)
        except Exception as exc:
            logger.warning("game mode: audit failed: %s", exc)
            rows = []
        self._health_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = [item.get("label", ""), item.get("expected", ""),
                      item.get("actual", ""), item.get("status", "unknown")]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 3 and value != "ok":
                    cell.setForeground(Qt.GlobalColor.yellow if value == "drift"
                                       else Qt.GlobalColor.gray)
                self._health_table.setItem(row, column, cell)

    def _repair(self) -> None:
        try:
            rows = health_audit.audit(self.host._system, self.host._journal.actions)
            health_audit.repair(self.host._system, rows)
        except Exception as exc:
            QMessageBox.warning(self, "Repair drift", str(exc))
        self._audit()

    def _undo(self) -> None:
        row = self._journal_table.currentRow()
        journal, system = self.host._journal, self.host._system

        def work(w):
            return session.undo_action(
                system, journal, row,
                lambda level, text: w.signals.log_line.emit(f"{level}\t{text}"))

        w = Worker(work)
        w.signals.log_line.connect(self.host._append)
        w.signals.result.connect(lambda _r: self.refresh())
        w.signals.error.connect(lambda err: self._error.set_error(str(err)))
        self.host._start(w)

    def _export_settings(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export Game Mode settings", "game-mode-settings.json",
            "JSON files (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(game_config.export_settings(self.host._config()),
                                  encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _import_settings(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import Game Mode settings", "", "JSON files (*.json)")
        if not path:
            return
        try:
            game_config.import_settings(
                self.host._config(), Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self.host._config().save()
        self.host._settings = game_config.load_settings(self.host._config())
        self.host._apply_settings_to_choices()
        self.host._save_choices()
        self.refresh()

    def _show_recovery(self) -> None:
        if self.host._journal.active:
            self.host._turn_off()
            return
        last = Path(self.host._journal.folder) / "last_restore.json"
        try:
            data = json.loads(last.read_text(encoding="utf-8"))
            report = data.get("report", [])
        except (OSError, ValueError):
            QMessageBox.information(self, "Recovery", "No restore report was found.")
            return
        lines = [f"[{r.get('level')}] {r.get('text')}" for r in report]
        QMessageBox.information(self, "Last restore report", "\n".join(lines[-30:]) or "Empty")
