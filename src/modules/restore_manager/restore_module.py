"""System Restore pane: restore points with age and gaps, System Protection and
shadow-storage state per drive, computed findings, and guarded deletion."""
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QProgressBar,
    QPushButton, QTableWidget, QVBoxLayout, QWidget,
)

from core.admin_utils import is_admin
from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.system_restore import sequence_numbers_to_prune
from core.table_ui import centered_item, fit_table, set_role
from core.worker import Worker
from modules.restore_manager import restore_analysis as ra

#: The number -> words mapping (never a blank cell; unknown numbers read "Type N").
#: Kept under its original name: the table-numbers regression test and older
#: callers import it from here.
restore_point_type_name = ra._point_kind
from ui.error_banner import ErrorBanner
from ui.table_items import fit_table_height, numeric_item, paint_severity

logger = logging.getLogger(__name__)

POINT_COLUMNS = ["Name", "Date", "Type", "Age", "Gap"]
DRIVE_COLUMNS = ["Drive", "System Protection", "Used", "Allocated", "Maximum", "Used of max"]
_ROLE_FOR = {"error": "statusError", "warning": "statusWarning", "info": "statusInfo"}
NEEDS_ADMIN = "Creating or deleting restore points needs administrator rights."


def _alive(widget) -> bool:
    return widget is not None and not sip.isdeleted(widget)


@dataclass
class _Snapshot:
    """Everything one refresh read.  A None field means "could not read"."""
    points: Optional[List[dict]] = None
    points_error: str = ""
    protection: Optional[ra.Protection] = None
    protection_error: str = ""
    storage: Optional[List[ra.ShadowStorage]] = None
    storage_error: str = ""
    policy_disabled: Optional[bool] = None
    frequency: int = ra.DEFAULT_FREQUENCY_MINUTES
    mounts: List[str] = field(default_factory=list)


def _fixed_mounts() -> List[str]:
    import psutil
    mounts = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        if "removable" in part.opts:
            continue
        mounts.append(part.mountpoint.rstrip("\\").upper())
    return mounts


def read_snapshot() -> _Snapshot:
    """Runs on a worker.  Each read fails independently."""
    snap = _Snapshot()
    try:
        snap.points = ra.read_restore_points()
    except ra.RestoreReadError as e:
        logger.warning("Restore point list failed: %s", e)
        snap.points_error = str(e)
    snap.protection, snap.protection_error = ra.read_protection()
    snap.storage, snap.storage_error = ra.read_shadow_storage()
    snap.policy_disabled = ra.read_policy_disabled()
    snap.frequency = ra.read_frequency_minutes()
    try:
        snap.mounts = _fixed_mounts()
    except OSError as e:
        logger.warning("Could not list fixed drives: %s", e)
    return snap


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
    # Listing points, protection state and shadow storage work unelevated;
    # only creating and deleting need elevation and say so.
    read_only_unelevated = True

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._restore_points: List[dict] = []
        self._infos: List[ra.PointInfo] = []
        self._snapshot = _Snapshot()
        self._worker: Optional[Worker] = None
        self._delete_running = False
        self._pending_verify: List[int] = []
        self._create_started: Optional[datetime] = None
        self._create_description = ""
        self._verify_created = False

    # ── UI ──────────────────────────────────────────────────────────────────

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)
        # Errors show in the pane (project rule), never as a modal that a
        # background result can pop over whatever the person is doing.
        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)
        layout.addLayout(self._build_toolbar())

        self._status_label = QLabel("Reading restore points...")
        set_role(self._status_label, "muted")
        layout.addWidget(self._status_label)

        self._findings_box = QVBoxLayout()
        layout.addLayout(self._findings_box)

        head = QLabel("Drives")
        set_role(head, "heading")
        layout.addWidget(head)
        self._drive_table = QTableWidget(0, len(DRIVE_COLUMNS))
        self._drive_table.setHorizontalHeaderLabels(DRIVE_COLUMNS)
        fit_table(self._drive_table, stretch=[0], content=[1, 2, 3, 4, 5])
        self._drive_table.verticalHeader().setVisible(False)
        self._drive_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._drive_table)

        head2 = QLabel("Restore points")
        set_role(head2, "heading")
        layout.addWidget(head2)
        self._table = QTableWidget()
        self._table.setColumnCount(len(POINT_COLUMNS))
        self._table.setHorizontalHeaderLabels(POINT_COLUMNS)
        fit_table(self._table, stretch=[0], content=[1, 2, 3, 4])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._update_delete_buttons)
        layout.addWidget(self._table, 1)

        info = QLabel(
            "System Restore monitors system files and registry for changes. Restore points let "
            "you revert Windows to a working state. Times are shown in local time.")
        info.setWordWrap(True)
        info.setObjectName("infoNote")
        layout.addWidget(info)
        return self._widget

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        self._create_btn = QPushButton("Create Restore Point")
        self._create_btn.setObjectName("accentButton")
        self._create_btn.clicked.connect(self._create_restore_point)
        toolbar.addWidget(self._create_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load_restore_points)
        toolbar.addWidget(refresh_btn)

        self._delete_btn = QPushButton("Delete Selected")
        self._delete_btn.setToolTip("Permanently remove the restore point(s) selected in the table")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)
        toolbar.addWidget(self._delete_btn)

        self._older_btn = QPushButton("Delete Older Than...")
        self._older_btn.setToolTip("Delete restore points older than a number of days; the newest is always kept")
        self._older_btn.setEnabled(False)
        self._older_btn.clicked.connect(self._delete_older_than)
        toolbar.addWidget(self._older_btn)

        self._prune_btn = QPushButton("Keep Only Latest")
        self._prune_btn.setToolTip("Delete every restore point except the most recent one")
        self._prune_btn.setEnabled(False)
        self._prune_btn.clicked.connect(self._delete_all_but_latest)
        toolbar.addWidget(self._prune_btn)

        self._copy_btn = QPushButton("Copy as Markdown")
        self._copy_btn.clicked.connect(self._copy_markdown)
        toolbar.addWidget(self._copy_btn)

        open_sysprops_btn = QPushButton("System Properties")
        open_sysprops_btn.setToolTip("Turn protection on or off and set the shadow-storage limit")
        open_sysprops_btn.clicked.connect(self._open_system_properties)
        toolbar.addWidget(open_sysprops_btn)

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(200)
        self._progress.setVisible(False)
        toolbar.addWidget(self._progress)
        toolbar.addStretch()
        return toolbar

    # ── lifecycle ───────────────────────────────────────────────────────────

    def on_start(self, app) -> None:
        self.app = app

    def get_refresh_interval(self) -> Optional[int]:
        return 120_000

    def refresh_data(self) -> None:
        if self._widget is not None and _alive(self._widget):
            self._load_restore_points()

    def on_activate(self) -> None:
        self._load_restore_points()

    def get_status_info(self) -> str:
        return f"System Restore — {len(self._restore_points)} points"

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    # ── loading ─────────────────────────────────────────────────────────────

    def _load_restore_points(self):
        self._progress.setVisible(True)

        def do_load(worker):
            return read_snapshot()

        def _on_load_error(err: str) -> None:
            if not _alive(self._widget):
                return
            self._progress.setVisible(False)
            self._show_status(f"Could not read System Restore: {err}", "statusError")
            logger.error("Load restore points error: %s", err)

        self._worker = Worker(do_load)
        self._worker.signals.result.connect(lambda snap: self._on_snapshot(snap))
        self._worker.signals.error.connect(lambda err: _on_load_error(err))
        self._workers.append(self._worker)
        self.app.thread_pool.start(self._worker)

    def _show_status(self, text: str, role: str = "muted") -> None:
        self._status_label.setText(text)
        set_role(self._status_label, role)

    def _on_snapshot(self, snap: _Snapshot) -> None:
        if not _alive(self._widget):
            return
        self._snapshot = snap
        self._progress.setVisible(False)
        if snap.points is None:
            # A failed read is not "no restore points": keep what is shown.
            self._show_status(f"Could not list restore points: {snap.points_error}", "statusError")
            self._render_context()
            return
        self._on_points_loaded(snap.points)

    def _on_points_loaded(self, points):
        self._progress.setVisible(False)
        self._restore_points = points
        self._infos = ra.analyze_points(points)
        self._fill_points()
        self._render_context()
        self._update_delete_buttons()
        self._verify_pending()

    def _fill_points(self) -> None:
        raw_by_seq = {}
        for pt in self._restore_points:
            try:
                raw_by_seq[int(pt.get("SequenceNumber"))] = pt
            except (TypeError, ValueError):
                logger.debug("point without sequence number kept unaddressable")
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._infos))
        for row, p in enumerate(self._infos):
            name_item = centered_item(p.description)
            if p.sequence is not None:
                name_item.setData(Qt.ItemDataRole.UserRole, p.sequence)
            else:
                logger.warning("Restore point %r has no usable SequenceNumber", p.description)
            raw_time = ""
            if p.created is None:
                raw_time = self._raw_time_for(p, raw_by_seq)
            date_text = p.created.strftime("%Y-%m-%d %H:%M") if p.created else (raw_time or "Unknown")
            age = None if p.age_days is None else round(p.age_days, 1)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, centered_item(date_text))
            self._table.setItem(row, 2, centered_item(p.kind))
            self._table.setItem(row, 3, numeric_item("n/a" if age is None else f"{age:g} d", age))
            gap = None if p.gap_days is None else round(p.gap_days, 1)
            self._table.setItem(row, 4, numeric_item("-" if gap is None else f"{gap:g} d", gap))
        self._table.setSortingEnabled(True)

    def _raw_time_for(self, info: ra.PointInfo, raw_by_seq: dict) -> str:
        """The unparsed CreationTime, so an odd value still shows something."""
        pt = raw_by_seq.get(info.sequence) if info.sequence is not None else None
        if pt is None:
            for cand in self._restore_points:
                if str(cand.get("Description") or "Unnamed restore point") == info.description:
                    pt = cand
                    break
        return str((pt or {}).get("CreationTime") or "")[:14]

    def _render_context(self) -> None:
        snap = self._snapshot
        findings = ra.restore_findings(
            self._infos, snap.protection, snap.storage, snap.storage_error,
            snap.policy_disabled, snap.frequency)
        self._render_findings(findings)
        self._fill_drives()
        counts = ra.summary_counts(findings)
        if snap.points is not None and not self._pending_verify:
            newest = self._infos[-1].created if self._infos and self._infos[-1].created else None
            self._show_status(
                f"{len(self._infos)} restore point(s)"
                + (f", newest {newest:%Y-%m-%d %H:%M}" if newest else "")
                + f" -- {counts['error']} error(s), {counts['warning']} warning(s)")

    def _render_findings(self, findings: List[ra.Finding]) -> None:
        while self._findings_box.count():
            item = self._findings_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for f in findings:
            lbl = QLabel(f"<b>{f.title}</b>. {f.detail}")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setWordWrap(True)
            set_role(lbl, _ROLE_FOR.get(f.severity, "statusInfo"))
            self._findings_box.addWidget(lbl)

    def _fill_drives(self) -> None:
        snap = self._snapshot
        rows = ra.protection_rows(snap.mounts, snap.protection, snap.storage)
        self._drive_table.setRowCount(len(rows))
        for r, v in enumerate(rows):
            if snap.protection is None:
                state, sev = "Unknown", "info"
            else:
                state, sev = ("On", "ok") if v.protected else ("Off", "warning")
            st = v.storage
            pct = st.used_of_max_percent if st else None
            cells = [
                centered_item(v.mount), centered_item(state),
                centered_item(ra.format_bytes(st.used) if st else ("n/a" if snap.storage is None else "-")),
                centered_item(ra.format_bytes(st.allocated) if st else "-"),
                centered_item(("unbounded" if st.unbounded else ra.format_bytes(st.maximum)) if st else "-"),
                numeric_item("-" if pct is None else f"{pct:.0f}%", pct),
            ]
            paint_severity(cells[1], sev)
            if pct is not None and pct >= 90:
                paint_severity(cells[5], "warning")
            for c, item in enumerate(cells):
                self._drive_table.setItem(r, c, item)
        fit_table_height(self._drive_table, 8)

    # ── create ──────────────────────────────────────────────────────────────

    def _require_admin(self) -> bool:
        if is_admin():
            return True
        QMessageBox.information(self._widget, "Administrator required", NEEDS_ADMIN)
        return False

    def _create_restore_point(self):
        if not self._require_admin():
            return
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
            self._widget, "Restore Point Description", "Description:",
            QInputDialog.InputMode.TextInput,
        )
        if not ok or not desc.strip():
            desc = "Windows Client Tool Restore Point"
        desc = desc.strip()

        self._progress.setVisible(True)
        self._show_status("Creating restore point...")
        self._create_started = datetime.now()
        self._create_description = desc

        def do_create(worker):
            from core.system_restore import create_restore_point
            return create_restore_point(desc, timeout=60)

        def _on_create_error(err: str) -> None:
            if not _alive(self._widget):
                return
            self._progress.setVisible(False)
            logger.error("Create restore point error: %s", err)

        self._worker = Worker(do_create)
        self._worker.signals.result.connect(lambda res: self._on_created(res))
        self._worker.signals.error.connect(lambda err: _on_create_error(err))
        self._workers.append(self._worker)
        self.app.thread_pool.start(self._worker)

    def _on_created(self, result):
        if not _alive(self._widget):
            return
        self._progress.setVisible(False)
        success, output = result
        if success:
            self._show_status("Checkpoint-Computer reported success; verifying it is listed...")
            self._verify_created = True
            self._load_restore_points()
        else:
            self._error_banner.set_error(
                f"Could not create restore point. {output} "
                "Note: some Windows editions restrict restore point creation via scripts.")
            self._show_status("Restore point creation may be restricted by policy", "statusWarning")

    def _verify_created_point(self) -> None:
        """Read back: a zero exit code is not proof Windows kept the point."""
        started, desc = self._create_started, self._create_description
        self._verify_created = False
        if started is None:
            return
        floor = started.replace(second=0, microsecond=0)
        found = any(p.description == desc and p.created and p.created >= floor for p in self._infos)
        if found:
            self._show_status(f"Restore point \"{desc}\" created and confirmed in the list.", "statusSuccess")
        else:
            self._show_status(f"Windows reported success but no new point named \"{desc}\" is listed.",
                              "statusWarning")
            self._error_banner.set_error(
                "Checkpoint-Computer returned success, but the new restore point is not in "
                "Windows' list. System Protection may be off for the system drive, or Windows "
                "skipped the point.")

    # ── deletion ────────────────────────────────────────────────────────────

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
        idle = not self._delete_running
        self._delete_btn.setEnabled(has_selection and idle)
        prunable = bool(sequence_numbers_to_prune(self._restore_points))
        self._prune_btn.setEnabled(prunable and idle)
        self._older_btn.setEnabled(prunable and idle)

    def _delete_selected(self):
        pairs = [(row, self._sequence_number_at(row)) for row in self._selected_rows()]
        pairs = [(row, seq) for row, seq in pairs if seq is not None]
        if not pairs:
            return
        if not self._require_admin():
            return

        names = []
        for row, _seq in pairs:
            item = self._table.item(row, 0)
            names.append(item.text() if item is not None else "Unnamed restore point")
        detail = "\n".join("• " + n for n in names[:10])
        if len(names) > 10:
            detail += "\n… and %d more" % (len(names) - 10)

        noun = "restore point" if len(pairs) == 1 else "restore points"
        if not confirm_destructive(
            self._widget, "Delete Restore Point",
            "Delete %d %s?" % (len(pairs), noun), detail=detail,
        ):
            return
        self._start_delete([seq for _row, seq in pairs])

    def _delete_all_but_latest(self):
        seqs = sequence_numbers_to_prune(self._restore_points)
        if not seqs:
            QMessageBox.information(
                self._widget, "Nothing to Delete",
                "There is only one restore point, so there is nothing older to remove.",
            )
            return
        if not self._require_admin():
            return

        noun = "restore point" if len(seqs) == 1 else "restore points"
        if not confirm_destructive(
            self._widget, "Keep Only Latest Restore Point",
            "Delete %d older %s, keeping only the most recent one?" % (len(seqs), noun),
        ):
            return
        self._start_delete(seqs)

    def _delete_older_than(self):
        days, ok = QInputDialog.getInt(
            self._widget, "Delete Older Restore Points",
            "Delete restore points older than this many days\n(the newest point is always kept):",
            30, 1, 3650)
        if not ok:
            return
        seqs = ra.sequences_older_than(self._infos, days)
        if not seqs:
            QMessageBox.information(self._widget, "Nothing to Delete",
                                    "No restore point is older than %d days." % days)
            return
        if not self._require_admin():
            return
        by_seq = {p.sequence: p for p in self._infos}
        detail = "\n".join(
            "• %s (%s)" % (by_seq[s].description,
                               by_seq[s].created.strftime("%Y-%m-%d") if by_seq[s].created else "?")
            for s in seqs[:10])
        if len(seqs) > 10:
            detail += "\n… and %d more" % (len(seqs) - 10)
        if not confirm_destructive(
            self._widget, "Delete Older Restore Points",
            "Delete %d restore point(s) older than %d days?" % (len(seqs), days), detail=detail,
        ):
            return
        self._start_delete(seqs)

    def _start_delete(self, sequence_numbers: List[int]):
        self._delete_running = True
        self._pending_verify = list(sequence_numbers)
        self._update_delete_buttons()
        self._progress.setVisible(True)
        self._show_status("Deleting %d restore point(s)…" % len(sequence_numbers))

        def do_delete(worker):
            from core.system_restore import delete_restore_points
            return delete_restore_points(sequence_numbers)

        def _on_delete_error(err: str) -> None:
            if not _alive(self._widget):
                return
            self._delete_running = False
            self._pending_verify = []
            self._progress.setVisible(False)
            self._show_status("Delete failed", "statusError")
            self._update_delete_buttons()
            logger.error("Delete restore points error: %s", err)
            self._error_banner.set_error("Could not delete restore points: %s" % err)

        self._worker = Worker(do_delete)
        self._worker.signals.result.connect(lambda res: self._on_deleted(res))
        self._worker.signals.error.connect(lambda err: _on_delete_error(err))
        self._workers.append(self._worker)
        self.app.thread_pool.start(self._worker)

    def _on_deleted(self, result):
        if not _alive(self._widget):
            return
        self._delete_running = False
        self._progress.setVisible(False)
        deleted, failures = result

        if failures:
            reasons = "\n".join(
                "• Restore point %d: %s" % (seq, msg) for seq, msg in failures[:10]
            )
            self._error_banner.set_error(
                "Deleted %d restore point(s); %d could not be deleted.  %s"
                % (deleted, len(failures), reasons.replace("\n", "  ")))
            self._show_status("Deleted %d, failed %d" % (deleted, len(failures)), "statusWarning")
        else:
            self._show_status("Deleted %d restore point(s)" % deleted)

        # Re-read from Windows rather than trusting our own bookkeeping; the
        # reload is also where the deletion is verified.
        self._load_restore_points()

    def _verify_pending(self) -> None:
        if self._verify_created:
            self._verify_created_point()
        if not self._pending_verify:
            return
        gone, still = ra.verify_deleted(self._pending_verify, self._infos)
        self._pending_verify = []
        if still:
            self._show_status(
                f"Read back: {len(gone)} confirmed gone, but Windows still lists {len(still)} "
                f"({', '.join(str(s) for s in still[:6])}).", "statusWarning")
        else:
            self._show_status(f"Read back: all {len(gone)} deleted restore point(s) confirmed gone.",
                              "statusSuccess")

    # ── misc ────────────────────────────────────────────────────────────────

    def _copy_markdown(self) -> None:
        import socket
        snap = self._snapshot
        findings = ra.restore_findings(self._infos, snap.protection, snap.storage, snap.storage_error,
                                       snap.policy_disabled, snap.frequency)
        QApplication.clipboard().setText(
            ra.points_to_markdown(self._infos, snap.storage, findings, socket.gethostname()))
        self._show_status("Copied the restore report as Markdown.")

    def _open_system_properties(self):
        try:
            subprocess.Popen(["SystemPropertiesProtection.exe"],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            self._error_banner.set_error(f"Could not open System Properties: {e}")
