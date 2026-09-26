# src/modules/disk_health/disk_health_module.py
"""Disk Health pane: computed findings, a drive table, a detail panel and a
volume table.  All logic lives in ``disk_reader`` (Qt-free); this only draws."""
import logging
from typing import List, Optional

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QTableWidget, QVBoxLayout, QWidget,
)

from core.admin_utils import is_admin
from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, fit_table, set_role
from core.worker import Worker
from modules.disk_health import disk_reader as dr
from modules.disk_health.smart_reader import DiskInfo, SmartAttribute, _query_disks  # noqa: F401
from ui.empty_state import EmptyState
from ui.table_items import fit_table_height, numeric_item, paint_severity

logger = logging.getLogger(__name__)

_ROLE_FOR = {"error": "statusError", "warning": "statusWarning", "info": "statusInfo"}
DRIVE_COLUMNS = ["Drive", "Bus", "Media", "Size", "Health", "Temp", "Life used",
                 "Power-on", "Verdict"]
VOLUME_COLUMNS = ["Volume", "Label", "FS", "Size", "Free", "Free %", "BitLocker",
                  "TRIM", "Health"]


def _alive(widget) -> bool:
    return widget is not None and not sip.isdeleted(widget)


def _new_table(columns: List[str]) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    return t


def _fill_drives(table: QTableWidget, disks: List[dr.PhysicalDiskInfo]) -> None:
    table.setSortingEnabled(False)          # a sorted fill scatters each row's cells
    table.setRowCount(len(disks))
    for r, d in enumerate(disks):
        sev, verdict = dr.disk_verdict(d)
        temp = d.temperature if d.temperature and not d.is_virtual else None
        cells = [
            centered_item(d.name), centered_item(d.bus), centered_item(d.media),
            numeric_item(d.size_text, d.size_bytes),
            centered_item(d.health or "unknown"),
            numeric_item(f"{temp} C" if temp else "n/a", temp),
            numeric_item("n/a" if d.wear_percent is None else f"{d.wear_percent}%", d.wear_percent),
            numeric_item(dr.power_on_hours_short(d.power_on_hours), d.power_on_hours),
            centered_item(verdict),
        ]
        paint_severity(cells[8], sev)
        for c, item in enumerate(cells):
            table.setItem(r, c, item)
        cells[0].setData(Qt.ItemDataRole.UserRole, d.device_id)
    table.setSortingEnabled(True)


def _fill_volumes(table: QTableWidget, report: dr.DiskReport) -> None:
    vols = [v for v in report.volumes if v.letter]
    table.setSortingEnabled(False)
    table.setRowCount(len(vols))
    for r, v in enumerate(vols):
        pct = v.free_percent
        trim = report.trim_enabled.get(v.filesystem)
        cells = [
            centered_item(v.display), centered_item(v.label), centered_item(v.filesystem),
            numeric_item(dr.format_size(v.size_bytes), v.size_bytes),
            numeric_item(dr.format_size(v.free_bytes), v.free_bytes),
            numeric_item("n/a" if pct is None else f"{pct:.0f}%", pct),
            centered_item(v.bitlocker or "not read"),
            centered_item({True: "on", False: "OFF", None: "n/a"}[trim]),
            centered_item(v.health or "?"),
        ]
        if pct is not None and pct < 5:
            paint_severity(cells[5], "error")
        elif pct is not None and pct < 10:
            paint_severity(cells[5], "warning")
        for c, item in enumerate(cells):
            table.setItem(r, c, item)
    table.setSortingEnabled(True)


def detail_text(d: dr.PhysicalDiskInfo, elevated: bool) -> str:
    lines = [
        f"{d.name}",
        f"Serial: {d.serial or 'not reported'}    Firmware: {d.firmware or 'not reported'}",
        f"Type: {d.media or '?'} over {d.bus or '?'}    Size: {d.size_text}",
        f"Power-on time: {dr.power_on_text(d.power_on_hours)}",
    ]
    if d.temperature and not d.is_virtual:
        warn, crit = dr.temperature_limits(d)
        lines.append(f"Temperature: {d.temperature} C (warns at {warn} C, critical {crit} C)")
    if d.power_on_hours is None and not elevated:
        lines.append("Power-on hours and some error counters may need administrator rights.")
    for f in dr.disk_findings(d):
        lines.append(f"[{f.severity}] {f.title}. {f.detail}".rstrip())
    if d.smart_attrs:
        lines.append("")
        lines.append("S.M.A.R.T. attributes (id, name, value/worst/threshold, raw):")
        for a in d.smart_attrs:
            flag = "  <-- at threshold" if a.failing else ""
            lines.append(f"  {a.id:3d} {a.name:<28} {a.value}/{a.worst}/{a.threshold}  raw {a.raw}{flag}")
    return "\n".join(lines)


class _ResultsView(QWidget):
    """Everything one scan produced."""

    def __init__(self, report: dr.DiskReport, parent=None):
        super().__init__(parent)
        self.report = report
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        self._add_findings(box)
        self._add_drives(box)
        self._add_volumes(box)

    def _add_findings(self, box: QVBoxLayout) -> None:
        head = QLabel("Needs attention")
        set_role(head, "heading")
        box.addWidget(head)
        findings = dr.all_findings(self.report)
        self.finding_labels: List[QLabel] = []
        if not findings:
            ok = QLabel("Nothing needs attention. Every drive and volume read is within normal limits.")
            set_role(ok, "statusSuccess")
            box.addWidget(ok)
        for f in findings:
            lbl = QLabel(f"<b>{f.subject}</b>: {f.title}. {f.detail}")
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            set_role(lbl, _ROLE_FOR.get(f.severity, "statusInfo"))
            self.finding_labels.append(lbl)
            box.addWidget(lbl)

    def _add_drives(self, box: QVBoxLayout) -> None:
        head = QLabel("Drives")
        set_role(head, "heading")
        box.addWidget(head)
        self.drive_table = _new_table(DRIVE_COLUMNS)
        fit_table(self.drive_table, stretch=[8], content=[0, 1, 2, 3, 4, 5, 6, 7])
        _fill_drives(self.drive_table, self.report.disks)
        fit_table_height(self.drive_table)
        box.addWidget(self.drive_table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(170)
        self.detail.setPlaceholderText("Select a drive for its detail.")
        box.addWidget(self.detail)
        self.drive_table.itemSelectionChanged.connect(self._on_select)

    def _add_volumes(self, box: QVBoxLayout) -> None:
        head = QLabel("Volumes")
        set_role(head, "heading")
        box.addWidget(head)
        self.volume_table = _new_table(VOLUME_COLUMNS)
        fit_table(self.volume_table, stretch=[1], content=[0, 2, 3, 4, 5, 6, 7, 8])
        _fill_volumes(self.volume_table, self.report)
        fit_table_height(self.volume_table)
        box.addWidget(self.volume_table)

    def selected_disk(self) -> Optional[dr.PhysicalDiskInfo]:
        rows = self.drive_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.drive_table.item(rows[0].row(), 0)
        wanted = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((d for d in self.report.disks if d.device_id == wanted), None)

    def _on_select(self) -> None:
        d = self.selected_disk()
        self.detail.setPlainText(detail_text(d, self.report.elevated) if d else "")


class _DiskHealthWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list = []
        self._scanning = False
        self._report: Optional[dr.DiskReport] = None
        self._thread_pool = None
        self._setup_ui()

    def _pool(self):
        if self._thread_pool is None:
            from PyQt6.QtCore import QThreadPool
            self._thread_pool = QThreadPool.globalInstance()
        return self._thread_pool

    def _setup_ui(self) -> None:
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)

        tb = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Drives")
        self._scan_btn.clicked.connect(self._do_scan)
        tb.addWidget(self._scan_btn)
        self._copy_btn = QPushButton("Copy as Markdown")
        self._copy_btn.setToolTip("Copy the drives, volumes and findings as a ticket-ready table")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy_markdown)
        tb.addWidget(self._copy_btn)
        self._status_lbl = QLabel("Click Scan to read drive health.")
        set_role(self._status_lbl, "muted")
        tb.addWidget(self._status_lbl, stretch=1)
        vbox.addLayout(tb)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        self._progress.hide()
        vbox.addWidget(self._progress)

        self._note = QLabel()
        self._note.setWordWrap(True)
        set_role(self._note, "infoNote")
        self._note.setText(
            "Health, temperature, wear and volume state are read without administrator rights. "
            "Power-on hours, error counters and S.M.A.R.T. attribute tables may need elevation (and many NVMe drives never report them)."
            if not is_admin() else
            "Running elevated: S.M.A.R.T. attributes and failure prediction are included "
            "where the drive supports them.")
        vbox.addWidget(self._note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_widget)
        self._scroll = scroll
        vbox.addWidget(scroll, stretch=1)

        # Takes the scroll area's place until there is something to show: the
        # cards layout ends in a stretch, which would pin a message to the top.
        self._empty = EmptyState(
            "\U0001F4BD", "No drives scanned yet",
            "Drive health is read on demand: Windows' health verdict, temperature, wear, "
            "volume free space, TRIM and BitLocker state.",
            action_text="Scan Drives")
        self._empty.action_triggered.connect(self._do_scan)
        vbox.addWidget(self._empty, stretch=1)
        self._update_empty_state()

    def refresh(self) -> None:
        """Background rescan; skipped while one is running."""
        if not self._scanning:
            self._do_scan()

    def _do_scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._scan_btn.setEnabled(False)
        self._status_lbl.setText("Scanning drives...")
        self._progress.show()
        elevated = is_admin()

        def work(_w):
            return dr.read_disk_report(elevated=elevated)

        w = Worker(work)
        w.signals.result.connect(lambda rep: self._on_result(rep))
        w.signals.error.connect(lambda err: self._on_error(err))
        self._workers.append(w)
        self._pool().start(w)

    def _finish_scan(self) -> None:
        self._scanning = False
        self._progress.hide()
        self._scan_btn.setEnabled(True)

    def _on_result(self, report: dr.DiskReport) -> None:
        if not _alive(self):
            return
        self._finish_scan()
        self._clear_cards()
        self._report = report
        self._cards_layout.insertWidget(self._cards_layout.count() - 1, _ResultsView(report))
        self._copy_btn.setEnabled(True)
        real = [d for d in report.disks if not d.is_virtual]
        findings = dr.all_findings(report)
        bad = sum(1 for f in findings if f.severity in ("error", "warning"))
        self._status_lbl.setText(
            f"{len(real)} drive(s), {len(report.volumes)} volume(s) -- "
            + (f"{bad} finding(s) need attention" if bad else "nothing needs attention"))
        self._update_empty_state()

    def _on_error(self, err: str) -> None:
        if not _alive(self):
            return
        self._finish_scan()
        self._status_lbl.setText(f"Scan failed: {err}")
        logger.warning("Disk scan failed: %s", err)

    def _copy_markdown(self) -> None:
        if self._report is None:
            return
        import socket
        QApplication.clipboard().setText(dr.report_to_markdown(self._report, socket.gethostname()))
        self._status_lbl.setText("Copied the disk report as Markdown.")

    def _clear_cards(self) -> None:
        # Everything except the empty state, which is a fixture of the pane.
        for index in reversed(range(self._cards_layout.count())):
            item = self._cards_layout.itemAt(index)
            widget = item.widget() if item else None
            if widget is not None and widget is not self._empty:
                self._cards_layout.takeAt(index)
                widget.deleteLater()
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        """Show the placeholder exactly when there is no result to show."""
        cards = 0
        for index in range(self._cards_layout.count()):
            item = self._cards_layout.itemAt(index)
            widget = item.widget() if item else None
            if widget is not None and widget is not self._empty:
                cards += 1
        self._empty.setVisible(cards == 0)
        self._scroll.setVisible(cards > 0)

    def cancel_all_workers(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self._scanning = False
        self._progress.hide()
        self._scan_btn.setEnabled(True)


class DiskHealthModule(BaseModule):
    name = "Disk Health"
    icon = "\U0001F4BE"
    description = "Drive health, temperature, wear, TRIM, BitLocker and computed findings"
    requires_admin = True
    # Health, temperature, wear and volumes read fine unelevated; only power-on
    # hours, error counters and SMART tables need elevation.
    read_only_unelevated = True
    group = ModuleGroup.SYSTEM

    def create_widget(self) -> QWidget:
        self._widget = _DiskHealthWidget()
        return self._widget

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.cancel_all_workers()
        widget = getattr(self, "_widget", None)
        if widget is not None and _alive(widget):
            widget.cancel_all_workers()

    def on_activate(self) -> None:
        widget = getattr(self, "_widget", None)
        if widget is not None and _alive(widget) and widget._report is None:
            widget.refresh()

    def on_deactivate(self) -> None:
        widget = getattr(self, "_widget", None)
        if widget is not None and _alive(widget):
            widget.cancel_all_workers()

    def refresh_data(self) -> None:
        widget = getattr(self, "_widget", None)
        if widget is not None and _alive(widget):
            widget.refresh()

    def get_refresh_interval(self) -> Optional[int]:
        """Rescan every five minutes."""
        return 300_000

    def get_status_info(self) -> str:
        return "Disk Health"
