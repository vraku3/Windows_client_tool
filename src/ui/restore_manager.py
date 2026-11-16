# src/ui/restore_manager.py
import json
import logging
from datetime import datetime
from typing import List

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QMessageBox, QProgressBar, QPushButton, QSplitter,
    QTableWidget, QVBoxLayout,
)

from core.backup_service import BackupService, RestorePointInfo
from core.table_ui import centered_item, center_header
from core.worker import Worker

logger = logging.getLogger(__name__)

_STATUS_ICON = {
    "active":   "🟢 Active",
    "restored": "↩ Restored",
    "partial":  "⚠️ Partial",
    "deleted":  "🗑️ Deleted",
}


class RestoreManagerDialog(QDialog):
    """Shows all change snapshots with options to restore or delete them."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app
        self._backup: BackupService = app.backup
        self._workers: list = []
        self._rp_list: List[RestorePointInfo] = []
        self.setWindowTitle("Restore Manager — Change Snapshots")
        self.resize(960, 620)
        self._setup_ui()
        self._load()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Info banner
        info = QLabel(
            "Every tweak session is recorded here. "
            "Select a snapshot and click <b>Restore</b> to undo those changes. "
            "Use <b>Create Snapshot</b> to mark your current state before making manual changes."
        )
        info.setWordWrap(True)
        root.addWidget(info)

        # Top toolbar
        tb = QHBoxLayout()
        self._create_btn = QPushButton("📸  Create Snapshot")
        self._create_btn.setToolTip("Save a named checkpoint before making manual changes")
        self._create_btn.clicked.connect(self._create_manual)
        tb.addWidget(self._create_btn)
        tb.addStretch()
        refresh_btn = QPushButton("⟳  Refresh")
        refresh_btn.clicked.connect(self._load)
        tb.addWidget(refresh_btn)
        root.addLayout(tb)

        # Splitter: snapshot table (top) + step details (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Snapshot list
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Date / Time", "Label", "Module", "Changes", "Status"]
        )
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(False)
        for col in range(5):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 140)
        self._table.setColumnWidth(1, 240)
        self._table.setColumnWidth(2, 120)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 100)
        center_header(self._table)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        splitter.addWidget(self._table)

        # Step details — one row per recorded change, individually revertible
        self._detail = QTableWidget(0, 4)
        self._detail.setHorizontalHeaderLabels(["Step", "Target", "Before → After", "Status"])
        dhdr = self._detail.horizontalHeader()
        dhdr.setStretchLastSection(False)
        for col in range(4):
            dhdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self._detail.setColumnWidth(0, 100)
        self._detail.setColumnWidth(1, 200)
        self._detail.setColumnWidth(2, 240)
        self._detail.setColumnWidth(3, 100)
        center_header(self._detail)
        self._detail.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._detail.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._detail.verticalHeader().setVisible(False)
        self._detail.selectionModel().currentRowChanged.connect(self._on_step_selection_changed)
        splitter.addWidget(self._detail)
        splitter.setSizes([380, 180])
        root.addWidget(splitter, stretch=1)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        root.addWidget(self._progress)

        # Status label
        self._status_lbl = QLabel("")
        root.addWidget(self._status_lbl)

        # Bottom button row
        btns = QHBoxLayout()
        self._restore_btn = QPushButton("↩  Restore Selected")
        self._restore_btn.setToolTip("Undo all changes recorded in the selected snapshot")
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._restore_selected)
        btns.addWidget(self._restore_btn)

        self._delete_btn = QPushButton("🗑️  Delete Selected")
        self._delete_btn.setToolTip("Remove this snapshot record (changes already applied are NOT undone)")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)
        btns.addWidget(self._delete_btn)

        self._revert_step_btn = QPushButton("↩  Revert This Step Only")
        self._revert_step_btn.setToolTip(
            "Undo just the selected change below, leaving the rest of this snapshot applied")
        self._revert_step_btn.setEnabled(False)
        self._revert_step_btn.clicked.connect(self._revert_selected_step)
        btns.addWidget(self._revert_step_btn)

        btns.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self._rp_list = self._backup.list_restore_points()
        self._table.setRowCount(0)
        for rp in self._rp_list:
            row = self._table.rowCount()
            self._table.insertRow(row)
            try:
                dt = datetime.fromisoformat(rp.created_at)
                date_str = dt.strftime("%Y-%m-%d  %H:%M")
            except ValueError:
                date_str = rp.created_at[:16]

            status_text = _STATUS_ICON.get(rp.status, rp.status)
            values = [date_str, rp.label, rp.module, str(rp.step_count), status_text]
            for col, val in enumerate(values):
                item = centered_item(val)
                item.setData(Qt.ItemDataRole.UserRole, rp.id)
                if rp.status == "restored":
                    item.setForeground(self._table.palette().color(
                        self._table.palette().ColorRole.Mid))
                self._table.setItem(row, col, item)

        self._restore_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._detail.clear()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_row_changed(self, current, _previous) -> None:
        has = current.isValid()
        if not has:
            self._restore_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            self._detail.clear()
            return

        rp_id = self._table.item(current.row(), 0).data(Qt.ItemDataRole.UserRole)
        status = self._table.item(current.row(), 4).text() if self._table.item(current.row(), 4) else ""
        already_restored = "Restored" in status

        self._restore_btn.setEnabled(not already_restored)
        self._delete_btn.setEnabled(True)
        self._load_detail(rp_id)

    def _load_detail(self, rp_id: str) -> None:
        self._detail.setRowCount(0)
        self._revert_step_btn.setEnabled(False)
        try:
            rows = self._backup._conn.execute(
                """SELECT id, step_type, target, before_value, after_value,
                          reverted_at
                   FROM tweak_steps WHERE restore_point_id=? ORDER BY rowid""",
                (rp_id,),
            ).fetchall()
        except Exception as e:
            logger.error("Could not load step details for %s: %s", rp_id, e)
            self._status_lbl.setText(f"Could not load step details: {e}")
            return

        for r in rows:
            row = self._detail.rowCount()
            self._detail.insertRow(row)

            def _fmt(raw):
                if not raw or raw == "null":
                    return "—"
                try:
                    return str(json.loads(raw))
                except Exception:
                    logger.warning("Ignored Exception", exc_info=True)
                    return str(raw)

            before_after = f"{_fmt(r['before_value'])} → {_fmt(r['after_value'])}"
            reverted = bool(r["reverted_at"])
            status = f"✓ reverted {r['reverted_at'][:16]}" if reverted else "applied"

            item0 = centered_item(r["step_type"].upper())
            item0.setData(Qt.ItemDataRole.UserRole, r["id"])
            item0.setData(Qt.ItemDataRole.UserRole + 1, reverted)
            self._detail.setItem(row, 0, item0)
            self._detail.setItem(row, 1, centered_item(r["target"]))
            self._detail.setItem(row, 2, centered_item(before_after))
            self._detail.setItem(row, 3, centered_item(status))

        if not rows:
            self._detail.insertRow(0)
            placeholder = centered_item(
                "No individual steps recorded (manual snapshots have none).")
            self._detail.setItem(0, 0, placeholder)
            self._detail.setSpan(0, 0, 1, 4)

    def _on_step_selection_changed(self, current, _previous) -> None:
        if not current.isValid():
            self._revert_step_btn.setEnabled(False)
            return
        item = self._detail.item(current.row(), 0)
        already_reverted = bool(item and item.data(Qt.ItemDataRole.UserRole + 1))
        step_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._revert_step_btn.setEnabled(bool(step_id) and not already_reverted)

    def _revert_selected_step(self) -> None:
        row = self._detail.currentRow()
        if row < 0:
            return
        item = self._detail.item(row, 0)
        step_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not step_id:
            return
        target = self._detail.item(row, 1).text() if self._detail.item(row, 1) else "this change"

        reply = QMessageBox.question(
            self, "Revert Step",
            f"Undo just this one change?\n\n  {target}\n\n"
            "The rest of this snapshot's changes stay applied.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        rp_id, _ = self._get_selected_rp()
        self._set_busy(True)
        self._status_lbl.setText("Reverting step…")

        def work(_worker):
            return self._backup.revert_step(step_id)

        def on_result(ok):
            self._set_busy(False)
            self._status_lbl.setText(
                "✅ Step reverted." if ok else "❌ Could not revert this step — see logs.")
            if rp_id:
                self._load_detail(rp_id)
                self._load()  # snapshot's step_count/status in the top table may change

        def on_error(err):
            self._set_busy(False)
            self._status_lbl.setText(f"❌ Error: {err}")

        w = Worker(work)
        w.signals.result.connect(on_result)
        w.signals.error.connect(on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _get_selected_rp(self):
        """Return (rp_id, label) for the currently selected row, or (None, None)."""
        row = self._table.currentRow()
        if row < 0:
            return None, None
        item0 = self._table.item(row, 0)
        item1 = self._table.item(row, 1)
        if item0 is None:
            return None, None
        return item0.data(Qt.ItemDataRole.UserRole), (item1.text() if item1 else "this snapshot")

    def _restore_selected(self) -> None:
        rp_id, label = self._get_selected_rp()
        if not rp_id:
            return

        reply = QMessageBox.question(
            self, "Restore Snapshot",
            f"Restore all changes from:\n\n  \"{label}\"\n\n"
            "This will revert the tweaks recorded in that session.\n"
            "A reboot may be required for some changes to take effect.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self._status_lbl.setText("Restoring snapshot…")

        def work(_worker):
            return self._backup.restore_point(rp_id)

        def on_result(result):
            self._set_busy(False)
            if result.success:
                self._status_lbl.setText(
                    "✅ Restore successful. Reboot may be required for some changes."
                )
            elif result.partial:
                self._status_lbl.setText(
                    f"⚠️ Partially restored — {len(result.failed_steps)} step(s) failed."
                )
                QMessageBox.warning(
                    self, "Partial Restore",
                    "Some steps could not be reverted:\n\n" + "\n".join(result.errors[:5]),
                )
            else:
                self._status_lbl.setText("❌ Restore failed.")
                QMessageBox.critical(
                    self, "Restore Failed",
                    "\n".join(result.errors[:5]) or "Unknown error.",
                )
            self._load()

        def on_error(err):
            self._set_busy(False)
            self._status_lbl.setText(f"❌ Error: {err}")

        w = Worker(work)
        w.signals.result.connect(on_result)
        w.signals.error.connect(on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _delete_selected(self) -> None:
        rp_id, label = self._get_selected_rp()
        if not rp_id:
            return

        reply = QMessageBox.question(
            self, "Delete Snapshot",
            f"Delete snapshot \"{label}\"?\n\n"
            "The changes already applied to the system will NOT be undone.\n"
            "You just lose the ability to restore from this snapshot.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._backup._conn.execute(
                "DELETE FROM tweak_steps WHERE restore_point_id=?", (rp_id,)
            )
            self._backup._conn.execute(
                "DELETE FROM restore_points WHERE id=?", (rp_id,)
            )
            self._backup._conn.commit()
        except Exception as e:
            QMessageBox.critical(self, "Delete Failed", str(e))
            return

        self._status_lbl.setText(f"Snapshot \"{label}\" deleted.")
        self._load()

    def _create_manual(self) -> None:
        label, ok = QInputDialog.getText(
            self, "Create Manual Snapshot",
            "Enter a name for this checkpoint\n(e.g. 'Before gaming tweaks'):",
        )
        if not ok or not label.strip():
            return
        try:
            rp_id = self._backup.create_restore_point(label.strip(), "Manual")
            self._status_lbl.setText(
                f"📸 Snapshot '{label.strip()}' created (ID: {rp_id[:8]}…)"
            )
            self._load()
        except Exception as e:
            QMessageBox.critical(self, "Create Failed", str(e))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._restore_btn.setEnabled(not busy)
        self._delete_btn.setEnabled(not busy)
        self._create_btn.setEnabled(not busy)
        self._revert_step_btn.setEnabled(False if busy else self._revert_step_btn.isEnabled())
        if busy:
            self._progress.setRange(0, 0)
            self._progress.show()
        else:
            self._progress.hide()
