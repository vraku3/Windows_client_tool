"""Restore Manager UI — create and manage system restore points."""
import subprocess
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QVBoxLayout, QWidget, QInputDialog,
)

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.system_restore import parse_restore_point_time, sequence_numbers_to_prune
from core.table_ui import centered_item, fit_table
from core.worker import Worker
from ui.error_banner import ErrorBanner
import logging

logger = logging.getLogger(__name__)


class RestoreManagerModule(BaseModule):
    # Deliberately NOT "Restore Manager" — that name is used by the Tools ▸ Restore
    # Manager... dialog (ui/restore_manager.py), which undoes THIS app's own tweak
    # changes and is a completely different feature from Windows System Restore.
    # Two same-named "Restore Manager"s caused real user confusion (2026-08-14): a
    # user looking here for "undo the tweak I just applied" found only OS restore
    # points, with no path to the actual per-tweak undo.
    name = "System Restore"
    icon = "♻️"
    description = "Create and manage Windows System Restore points (OS-level, not this app's tweak undo — see Tools ▸ Restore Manager for that)"
    group = ModuleGroup.OPTIMIZE
    requires_admin = True

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._restore_points: List[dict] = []
        self._worker: Optional[Worker] = None
        self._delete_running = False

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        # Toolbar
        toolbar = QHBoxLayout()
        create_btn = QPushButton("➕ Create Restore Point")
        create_btn.setObjectName("accentButton")
        create_btn.clicked.connect(self._create_restore_point)
        toolbar.addWidget(create_btn)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._load_restore_points)
        toolbar.addWidget(refresh_btn)

        self._delete_btn = QPushButton("🗑 Delete Selected")
        self._delete_btn.setToolTip(
            "Permanently remove the restore point(s) selected in the table"
        )
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)
        toolbar.addWidget(self._delete_btn)

        self._prune_btn = QPushButton("🧹 Keep Only Latest")
        self._prune_btn.setToolTip(
            "Delete every restore point except the most recent one"
        )
        self._prune_btn.setEnabled(False)
        self._prune_btn.clicked.connect(self._delete_all_but_latest)
        toolbar.addWidget(self._prune_btn)

        open_sysprops_btn = QPushButton("🔧 System Properties")
        open_sysprops_btn.clicked.connect(self._open_system_properties)
        toolbar.addWidget(open_sysprops_btn)

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(200)
        self._progress.setVisible(False)
        toolbar.addWidget(self._progress)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Status info
        self._status_label = QLabel("System Restore is enabled")
        self._status_label.setStyleSheet("color: #888; font-size: 12px; padding: 4px;")
        layout.addWidget(self._status_label)

        # Restore points table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Name", "Date", "Type", "Size"])
        fit_table(self._table, stretch=[0], content=[1, 2, 3])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._update_delete_buttons)
        layout.addWidget(self._table)

        # Info
        info = QLabel(
            "💡 System Restore monitors system files and registry for changes. "
            "Restore points let you revert Windows to a working state if problems occur."
        )
        info.setWordWrap(True)
        info.setObjectName("infoNote")
        layout.addWidget(info)

        return self._widget

    def on_start(self, app) -> None:
        self.app = app

    def get_refresh_interval(self) -> Optional[int]:
        return 120_000

    def refresh_data(self) -> None:
        self._load_restore_points()

    def on_activate(self) -> None:
        self._load_restore_points()

    def get_status_info(self) -> str:
        return f"Restore Manager — {len(self._restore_points)} points"

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    # ── implementation ──────────────────────────────────────────────────────

    def _load_restore_points(self):
        self._progress.setVisible(True)

        def do_load(worker):
            try:
                result = subprocess.run(
                    [
                        "powershell", "-Command",
                        "Get-ComputerRestorePoint | "
                        "Select-Object SequenceNumber, Description, "
                        "RestorePointType, CreationTime | "
                        "ConvertTo-Json -Compress",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                import json

                data = json.loads(result.stdout) if result.stdout.strip() else []
                if isinstance(data, dict):
                    data = [data]
                return data
            except Exception as e:
                logger.warning("Failed to load restore points: %s", e)
                return []

        def _on_load_error(err: str) -> None:
            self._progress.setVisible(False)
            logger.error("Load restore points error: %s", err)

        self._worker = Worker(do_load)
        self._worker.signals.result.connect(self._on_points_loaded)
        self._worker.signals.error.connect(_on_load_error)
        self._workers.append(self._worker)
        self.app.thread_pool.start(self._worker)

    def _on_points_loaded(self, points):
        self._progress.setVisible(False)
        self._restore_points = points
        self._table.setRowCount(0)

        for pt in points:
            row = self._table.rowCount()
            self._table.insertRow(row)
            name = pt.get("Description", "Unnamed restore point")
            ctime = pt.get("CreationTime", "")
            dt = parse_restore_point_time(ctime)
            if dt is not None:
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            else:
                date_str = str(ctime)[:14] if ctime else "Unknown"

            rptype = pt.get("RestorePointType", "Unknown")
            name_item = centered_item(name)
            try:
                name_item.setData(Qt.ItemDataRole.UserRole, int(pt.get("SequenceNumber")))
            except (TypeError, ValueError):
                # Without a sequence number there is no safe way to address it
                # for deletion, so the row stays visible but unselectable-for-delete.
                logger.warning("Restore point %r has no usable SequenceNumber", name)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, centered_item(date_str))
            self._table.setItem(row, 2, centered_item(rptype))
            self._table.setItem(row, 3, centered_item("~"))

        self._update_delete_buttons()

    def _create_restore_point(self):
        reply = QMessageBox.question(
            self._widget,
            "Create Restore Point",
            "This will create a new System Restore point.\n\n"
            "Enter a description for this restore point:",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        desc, ok = QInputDialog.getText(
            self._widget,
            "Restore Point Description",
            "Description:",
            QInputDialog.InputMode.TextInput,
        )
        if not ok or not desc.strip():
            desc = "Windows Client Tool Restore Point"

        self._progress.setVisible(True)
        self._status_label.setText("Creating restore point…")

        def do_create(worker):
            from core.system_restore import create_restore_point
            return create_restore_point(desc, timeout=60)

        def _on_create_error(err: str) -> None:
            self._progress.setVisible(False)
            logger.error("Create restore point error: %s", err)

        self._worker = Worker(do_create)
        self._worker.signals.result.connect(self._on_created)
        self._worker.signals.error.connect(_on_create_error)
        self._workers.append(self._worker)
        self.app.thread_pool.start(self._worker)

    def _on_created(self, result):
        self._progress.setVisible(False)
        success, output = result
        if success:
            QMessageBox.information(
                self._widget,
                "Restore Point Created",
                "System Restore point was created successfully.",
            )
            self._load_restore_points()
        else:
            self._error_banner.set_error(
                f"Could not create restore point. {output} "
                "Note: some Windows editions restrict restore point creation via scripts.")
            self._status_label.setText("Restore point creation may be restricted by policy")

    # ── deletion ──────────────────────────────────────────────

    def _selected_rows(self) -> List[int]:
        model = self._table.selectionModel()
        if model is None:
            return []
        return sorted({idx.row() for idx in model.selectedRows()})

    def _sequence_number_at(self, row: int) -> Optional[int]:
        item = self._table.item(row, 0)
        if item is None:
            return None
        seq = item.data(Qt.ItemDataRole.UserRole)
        return None if seq is None else int(seq)

    def _update_delete_buttons(self) -> None:
        has_selection = any(
            self._sequence_number_at(row) is not None for row in self._selected_rows()
        )
        self._delete_btn.setEnabled(has_selection and not self._delete_running)
        self._prune_btn.setEnabled(
            bool(sequence_numbers_to_prune(self._restore_points))
            and not self._delete_running
        )

    def _delete_selected(self):
        pairs = [(row, self._sequence_number_at(row)) for row in self._selected_rows()]
        pairs = [(row, seq) for row, seq in pairs if seq is not None]
        if not pairs:
            return

        names = []
        for row, _seq in pairs:
            item = self._table.item(row, 0)
            names.append(item.text() if item is not None else "Unnamed restore point")
        detail = "\n".join("\u2022 " + n for n in names[:10])
        if len(names) > 10:
            detail += "\n\u2026 and %d more" % (len(names) - 10)

        noun = "restore point" if len(pairs) == 1 else "restore points"
        if not confirm_destructive(
            self._widget,
            "Delete Restore Point",
            "Delete %d %s?" % (len(pairs), noun),
            detail=detail,
        ):
            return
        self._start_delete([seq for _row, seq in pairs])

    def _delete_all_but_latest(self):
        seqs = sequence_numbers_to_prune(self._restore_points)
        if not seqs:
            QMessageBox.information(
                self._widget,
                "Nothing to Delete",
                "There is only one restore point, so there is nothing older to remove.",
            )
            return

        noun = "restore point" if len(seqs) == 1 else "restore points"
        if not confirm_destructive(
            self._widget,
            "Keep Only Latest Restore Point",
            "Delete %d older %s, keeping only the most recent one?" % (len(seqs), noun),
        ):
            return
        self._start_delete(seqs)

    def _start_delete(self, sequence_numbers: List[int]):
        self._delete_running = True
        self._update_delete_buttons()
        self._progress.setVisible(True)
        self._status_label.setText(
            "Deleting %d restore point(s)\u2026" % len(sequence_numbers)
        )

        def do_delete(worker):
            from core.system_restore import delete_restore_points
            return delete_restore_points(sequence_numbers)

        def _on_delete_error(err: str) -> None:
            self._delete_running = False
            self._progress.setVisible(False)
            self._status_label.setText("Delete failed")
            self._update_delete_buttons()
            logger.error("Delete restore points error: %s", err)
            self._error_banner.set_error("Could not delete restore points: %s" % err)

        self._worker = Worker(do_delete)
        self._worker.signals.result.connect(self._on_deleted)
        self._worker.signals.error.connect(_on_delete_error)
        self._workers.append(self._worker)
        self.app.thread_pool.start(self._worker)

    def _on_deleted(self, result):
        self._delete_running = False
        self._progress.setVisible(False)
        deleted, failures = result

        if failures:
            reasons = "\n".join(
                "\u2022 Restore point %d: %s" % (seq, msg) for seq, msg in failures[:10]
            )
            self._error_banner.set_error(
                "Deleted %d restore point(s); %d could not be deleted. %s"
                % (deleted, len(failures), reasons))
            self._status_label.setText("Deleted %d, failed %d" % (deleted, len(failures)))
        else:
            self._status_label.setText("Deleted %d restore point(s)" % deleted)

        # Re-read from Windows rather than trusting our own bookkeeping.
        self._load_restore_points()

    def _open_system_properties(self):
        try:
            subprocess.Popen(["control.exe", "sysdm.cpl,,0"],
                              creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            self._error_banner.set_error(f"Could not open System Properties: {e}")
