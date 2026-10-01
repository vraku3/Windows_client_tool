import datetime
import os
from typing import List, Optional

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QProgressBar, QPushButton, QTableWidget,
    QTabWidget, QPlainTextEdit, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.semantic_colors import semantic
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.table_ui import centered_item, center_header, set_role
from modules.certificate_viewer import cert_findings as cf
from modules.certificate_viewer.cert_reader import CertInfo, fetch_certs
from PyQt6.QtGui import QGuiApplication

COLUMNS = [
    "Subject CN", "Issuer", "Expiry", "Days", "Thumbprint",
    "Key Usage", "Has Private Key", "Status", "Findings",
]
STORES = [
    ("Personal",          "MY",   "user"),
    ("Computer",          "MY",   "machine"),
    ("Trusted Root",      "ROOT", "machine"),
    ("Intermediate CAs",  "CA",   "machine"),
    ("Disallowed",        "Disallowed", "machine"),
    ("Trusted Publishers", "TrustedPublisher", "machine"),
]


class _CertTab(QWidget):
    def __init__(self, store_name: str, store_location: str,
                 thread_pool, parent=None):
        super().__init__(parent)
        self._store_name = store_name
        self._store_location = store_location
        self._thread_pool = thread_pool
        self._certs: List[CertInfo] = []
        self._worker: Optional[Worker] = None
        self._all_certs: List[CertInfo] = []
        self._chip = "All"
        self._attempted = False
        self._chip_buttons = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Toolbar: filter input + buttons
        tb = QWidget()
        tb_layout = QHBoxLayout(tb)
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.setSpacing(4)

        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText(
            "Filter by name, issuer, or thumbprint…")
        self._filter_input.textChanged.connect(self._apply_filter)
        tb_layout.addWidget(self._filter_input, 1)

        self._refresh_btn = QPushButton("🔄 Refresh")
        self._export_btn = QPushButton("Export .cer")
        self._view_btn = QPushButton("View Detail")
        self._export_btn.setEnabled(False)
        self._view_btn.setEnabled(False)
        tb_layout.addWidget(self._refresh_btn)
        tb_layout.addWidget(self._export_btn)
        tb_layout.addWidget(self._view_btn)
        self._copy_btn = QPushButton("Copy details")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy_details)
        tb_layout.addWidget(self._copy_btn)
        layout.addWidget(tb)

        chip_row = QHBoxLayout()
        for chip in cf.CHIPS:
            b = QPushButton(chip)
            b.setCheckable(True)
            b.setChecked(chip == "All")
            b.clicked.connect(lambda _=False, name=chip: self._set_chip(name))
            self._chip_buttons[chip] = b
            chip_row.addWidget(b)
        chip_row.addStretch()
        layout.addLayout(chip_row)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        # Certificate table
        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        center_header(self._table)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(COLUMNS)):
            self._table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, 1)

        # Status bar
        self._status = QLabel("Click Refresh to load certificates.")
        set_role(self._status, "muted")
        layout.addWidget(self._status)

        # Connections
        self._refresh_btn.clicked.connect(self._load)
        self._export_btn.clicked.connect(self._export)
        self._view_btn.clicked.connect(self._view_detail)
        self._table.selectionModel().selectionChanged.connect(
            self._on_selection_changed)

    # ── worker ────────────────────────────────────────────────────────────

    def ensure_loaded(self):
        if not self._all_certs and self._worker is None and not self._attempted:
            self._load()

    def _load(self):
        self._attempted = True
        self._stop()
        self._refresh_btn.setEnabled(False)
        self._status.setText("Loading certificates…")
        self._progress.show()
        self._table.setRowCount(0)

        self._worker = Worker(
            lambda _w: fetch_certs(self._store_name, self._store_location))
        self._worker.signals.result.connect(self._on_result)
        self._worker.signals.error.connect(self._on_error)
        self._worker.signals.finished.connect(self._on_finished)
        self._thread_pool.start(self._worker)

    def _stop(self):
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None

    def _on_finished(self):
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._worker = None

    # ── display ───────────────────────────────────────────────────────────

    def _on_result(self, certs: List[CertInfo]):
        self._all_certs = certs
        counts = cf.chip_counts(certs)
        for chip, b in self._chip_buttons.items():
            b.setText(f"{chip} ({counts[chip]})")
        self._apply_filter(self._filter_input.text())

    def _populate_table(self, certs: List[CertInfo]):
        self._table.setRowCount(len(certs))
        for r, c in enumerate(certs):
            expiry_str = (
                c.expiry.strftime("%Y-%m-%d")
                if c.expiry != datetime.datetime.min else "N/A"
            )
            vals = [
                c.subject_cn, c.issuer, expiry_str, str(c.days_until_expiry),
                c.thumbprint, c.key_usage,
                "Yes" if c.has_private_key else "No",
                c.flag, cf.findings_text(c),
            ]
            problem = c.days_until_expiry < 0 or bool(cf.weakness(c))
            soon = (c.days_until_expiry < 90 or cf.self_signed_non_root(c)
                    or cf.has_exportable_private_key(c))
            fg = None
            if problem:
                fg = QColor(semantic("error"))
            elif soon:
                fg = QColor(semantic("warning"))
            for col, val in enumerate(vals):
                item = centered_item(str(val))
                if fg is not None:
                    item.setForeground(fg)
                self._table.setItem(r, col, item)

        by_store = f"{self._store_name}/{self._store_location}"
        self._status.setText(
            f"{len(certs)} shown of {len(self._all_certs)} in {by_store}"
        )

    def _set_chip(self, name: str):
        self._chip = name
        for chip, b in self._chip_buttons.items():
            b.setChecked(chip == name)
        self._apply_filter(self._filter_input.text())

    def _apply_filter(self, text: str):
        q = text.strip()
        self._certs = [
            c for c in self._all_certs
            if cf.matches_chip(c, self._chip) and (not q or cf.search_match(c, q))
        ]
        self._populate_table(self._certs)

    def _on_error(self, err_str: str):
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText(f"Error: {err_str}")
        self._worker = None

    def _on_selection_changed(self):
        has_sel = bool(self._table.selectedItems())
        self._export_btn.setEnabled(has_sel)
        self._view_btn.setEnabled(has_sel)
        self._copy_btn.setEnabled(has_sel)

    # ── cert lookup ─────────────────────────────────────────────────────

    def _find_cert(self, row: int) -> Optional[CertInfo]:
        # self._certs is exactly what the table shows, in row order.
        if 0 <= row < len(self._certs):
            return self._certs[row]
        return None

    def _copy_details(self):
        rows = {i.row() for i in self._table.selectedIndexes()}
        cert = self._find_cert(min(rows)) if rows else None
        if cert:
            QGuiApplication.clipboard().setText(cf.details_text(cert))
            self._status.setText("Details copied to the clipboard.")

    # ── actions ─────────────────────────────────────────────────────────

    def _export(self):
        rows = {i.row() for i in self._table.selectedIndexes()}
        cert = self._find_cert(min(rows)) if rows else None
        if not cert:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Certificate",
            f"{cert.subject_cn}.cer",
            "DER Certificate (*.cer)")
        if not path:
            return
        with open(path, "wb") as f:
            f.write(cert.raw_der)
        self._status.setText(f"Exported to {os.path.basename(path)}")

    def _view_detail(self):
        rows = {i.row() for i in self._table.selectedIndexes()}
        cert = self._find_cert(min(rows)) if rows else None
        if not cert:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Certificate — {cert.subject_cn}")
        dlg.resize(650, 480)
        layout = QVBoxLayout(dlg)

        text = QPlainTextEdit(cf.details_text(cert))
        text.setReadOnly(True)
        set_role(text, "mono")
        layout.addWidget(text)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(dlg.accept)
        layout.addWidget(btns)
        dlg.exec()


class CertModule(BaseModule):
    name = "Certificates"
    icon = "🔐"
    description = "View installed certificates with expiry tracking"
    requires_admin = False
    group = ModuleGroup.MANAGE

    def __init__(self):
        super().__init__()
        # Declared here, not only in create_widget: the auto-refresh timer and
        # on_stop() both walk these tabs, and either can run before the widget
        # has ever been built.
        self._tabs: Optional[QTabWidget] = None

    def _each_tab(self):
        """Yield each built tab page, or nothing if there is no widget yet."""
        if self._tabs is None:
            return
        for i in range(self._tabs.count()):
            yield self._tabs.widget(i)

    def create_widget(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        for label, store_name, store_location in STORES:
            tab = _CertTab(store_name, store_location, self.thread_pool)
            self._tabs.addTab(tab, label)

        self._tabs.currentChanged.connect(lambda _i: self._load_current())
        outer_layout.addWidget(self._tabs)
        return outer

    def _load_current(self):
        if self._tabs is not None:
            page = self._tabs.currentWidget()
            if hasattr(page, "ensure_loaded"):
                page.ensure_loaded()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        for tab in self._each_tab():
            if hasattr(tab, "_load"):
                tab._load()

    def on_activate(self) -> None:
        self._load_current()

    def on_deactivate(self) -> None:
        for tab in self._each_tab():
            if hasattr(tab, "_stop"):
                tab._stop()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.on_deactivate()
