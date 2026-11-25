from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Optional

from PyQt6.QtCore import QTime, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTimeEdit, QVBoxLayout,
)
from core.table_ui import set_role
from modules.scripts_hub import library

logger = logging.getLogger(__name__)


class ScheduleScriptDialog(QDialog):
    """Dialog to register a script as a Windows Scheduled Task."""

    def __init__(self, script: library.Script, parent=None):
        super().__init__(parent)
        self._script = script
        self.setWindowTitle(f"Schedule Script - {script.title}")
        self.setMinimumWidth(480)
        self.setModal(True)

        layout = QVBoxLayout(self)

        title_lbl = QLabel(f"<b>Schedule Script:</b> {script.title}")
        layout.addWidget(title_lbl)

        form = QFormLayout()

        # Sanitize task name
        safe_name = re.sub(r"[^\w.-]+", "_", script.id).strip("_")
        self._task_name = QLineEdit(f"WinClientTool_{safe_name}")
        form.addRow("Task Name:", self._task_name)

        # Trigger frequency
        self._frequency = QComboBox()
        self._frequency.addItems(["Daily", "Weekly", "At Logon", "On Idle"])
        form.addRow("Schedule Frequency:", self._frequency)

        # Start Time
        self._time_edit = QTimeEdit()
        self._time_edit.setTime(QTime(3, 0)) # Default 03:00 AM
        form.addRow("Trigger Time:", self._time_edit)

        # Elevation checkbox
        self._elevated_chk = QCheckBox("Run with highest privileges (Administrator)")
        self._elevated_chk.setChecked(True)
        form.addRow("", self._elevated_chk)

        # Custom arguments
        self._args_edit = QLineEdit()
        self._args_edit.setPlaceholderText("e.g. -OpenReport or -CleanSafe -Force")
        form.addRow("Script Arguments:", self._args_edit)

        layout.addLayout(form)

        # Status feedback
        self._status = QLabel("")
        set_role(self._status, "muted")
        layout.addWidget(self._status)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("Create Scheduled Task")
        buttons.accepted.connect(self._create_task)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_task(self) -> None:
        task_name = self._task_name.text().strip()
        if not task_name:
            self._status.setText("Please enter a valid task name.")
            set_role(self._status, "statusError")
            return

        # Prepare target script path
        script_path = self._script.path
        if not script_path:
            # If it's a built-in script, export a copy to the user scripts directory
            try:
                script_path, _ = library.export_for_editing(self._script)
            except Exception as e:
                logger.warning("Could not export script for scheduling: %s", e)
                self._status.setText(f"Export failed: {e}")
                set_role(self._status, "statusError")
                return

        # Build schtasks command
        freq_map = {
            "Daily": "DAILY",
            "Weekly": "WEEKLY",
            "At Logon": "ONLOGON",
            "On Idle": "ONIDLE",
        }
        schedule_type = freq_map.get(self._frequency.currentText(), "DAILY")
        time_str = self._time_edit.time().toString("HH:mm")

        # Command to execute
        ps_exe = "powershell.exe"
        user_args = self._args_edit.text().strip()
        run_cmd = f'{ps_exe} -NoProfile -ExecutionPolicy Bypass -File "{script_path}" {user_args}'.strip()

        cmd = [
            "schtasks.exe", "/Create",
            "/TN", task_name,
            "/TR", run_cmd,
            "/SC", schedule_type,
            "/F",
        ]

        if schedule_type in ("DAILY", "WEEKLY"):
            cmd.extend(["/ST", time_str])

        if self._elevated_chk.isChecked():
            cmd.extend(["/RL", "HIGHEST"])

        self._status.setText("Registering task in Windows Task Scheduler…")
        set_role(self._status, "statusInfo")

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=0x08000000, # CREATE_NO_WINDOW
            )
            if res.returncode == 0:
                self._status.setText(f"Task '{task_name}' registered successfully!")
                set_role(self._status, "statusSuccess")
                QMessageBox.information(
                    self,
                    "Task Scheduled",
                    f"Task '{task_name}' has been successfully registered in Windows Task Scheduler.\n\n"
                    f"Command: {run_cmd}\nFrequency: {self._frequency.currentText()} at {time_str}"
                )
                self.accept()
            else:
                err_msg = res.stderr.strip() or res.stdout.strip() or "Unknown error"
                logger.warning("schtasks failed (%s): %s", res.returncode, err_msg)
                self._status.setText(f"Registration failed: {err_msg}")
                set_role(self._status, "statusError")
        except Exception as e:
            logger.exception("Error scheduling script: %s", e)
            self._status.setText(f"Execution error: {e}")
            set_role(self._status, "statusError")
