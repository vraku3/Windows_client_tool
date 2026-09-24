import datetime
import os
from typing import List, Optional

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHeaderView, QLabel,
    QLineEdit, QProgressBar, QPushButton, QTableWidget,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.semantic_colors import semantic
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.table_ui import centered_item, center_header
from modules.certificate_viewer.cert_reader import CertInfo, fetch_certs

COLUMNS = [
    "Subject CN", "Issuer", "Expiry", "Thumbprint",
    "Key Usage", "Has Private Key", "Status",
]
STORES = [
    ("Personal",          "MY",   "user"),
    ("Computer",          "MY",   "machine"),
    ("Trusted Root",      "ROOT", "machine"),
    ("Intermediate CAs",  "CA",   "machine"),
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
        self._loaded_once = False
        self._setup_ui()

    def ensure_loaded(self) -> None:
        """Load this store the first time it is looked at, and only then."""
        if not self._loaded_once:
            self._load()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Toolbar: filter input + buttons
        tb = QWidget()
        from PyQt6.QtWidgets import QHBoxLayout
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
        layout.addWidget(tb)

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
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, 1)

        # Status bar
        self._status = QLabel("Click Refresh to load certificates.")
        self._status.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._status)

        # Connections
        self._refresh_btn.clicked.connect(self._load)
        self._export_btn.clicked.connect(self._export)
        self._view_btn.clicked.connect(self._view_detail)
        self._table.selectionModel().selectionChanged.connect(
            self._on_selection_changed)

    # ── worker ────────────────────────────────────────────────────────────

    def _load(self):
        self._loaded_once = True
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
        self._certs = certs
        self._populate_table(certs)

    def _populate_table(self, certs: List[CertInfo]):
        self._table.setRowCount(len(certs))
        for r, c in enumerate(certs):
            expiry_str = (
                c.expiry.strftime("%Y-%m-%d")
                if c.expiry != datetime.datetime.min else "N/A"
            )
            thumb_display = (
                c.thumbprint[:20] + "..."
                if len(c.thumbprint) > 20 else c.thumbprint
            )
            vals = [
                c.subject_cn,
                c.issuer,
                expiry_str,
                thumb_display,
                c.key_usage,
                "Yes" if c.has_private_key else "No",
                c.flag,
            ]
            for col, val in enumerate(vals):
                item = centered_item(str(val))
                self._table.setItem(r, col, item)

            if "🔴" in c.flag:
                fg = QColor("#CC2222")
            elif "🟠" in c.flag:
                fg = QColor("#CC7700")
            elif "🟢" in c.flag:
                fg = QColor(semantic("success"))
            else:
                fg = QColor("#e0e0e0")
            for col in range(len(COLUMNS)):
                cell = self._table.item(r, col)
                if cell:
                    cell.setForeground(fg)

        expired = sum(1 for c in certs if c.days_until_expiry < 0)
        expiring = sum(1 for c in certs if 0 <= c.days_until_expiry <= 30)
        self._status.setText(
            f"{len(certs)} shown ({len(self._all_certs)} total)"
            f" — {expired} expired, {expiring} expiring soon"
        )

    def _apply_filter(self, text: str):
        if not self._all_certs:
            return
        if not text:
            self._certs = list(self._all_certs)
        else:
            q = text.lower()
            self._certs = [
                c for c in self._all_certs
                if (q in c.subject_cn.lower() or
                    q in c.issuer.lower() or
                    q in c.thumbprint.lower())
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

    # ── cert lookup ─────────────────────────────────────────────────────

    def _find_cert(self, row: int) -> Optional[CertInfo]:
        if 0 <= row < len(self._certs):
            cn = self._table.item(row, 0)
            if cn:
                for c in self._all_certs:
                    if c.subject_cn == cn.text():
                        return c
        return None

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

        expiry_str = (
            cert.expiry.strftime("%Y-%m-%d %H:%M UTC")
            if cert.expiry != datetime.datetime.min else "N/A")

        flag_color = {
            "🔴 Expired": "#CC2222",
            "🟠 Expiring Soon": "#CC7700",
            "🟢 Valid": "#2ecc71",
        }.get(cert.flag.strip(), "#e0e0e0")

        html = f"""
        <html><body style="font-family:Consolas,monospace;
              font-size:13px; background:#252525; color:#e0e0e0; padding:12px;">
        <h3 style="color:#4488FF; margin-top:0;">
            &#x1F510; {cert.subject_cn}
        </h3>
        <table style="width:100%; border-collapse:collapse;">
        <tr><td style="color:#888; width:150px;">Subject</td>
            <td>{cert.subject_full or '&mdash;'}</td></tr>
        <tr><td style="color:#888;">Issuer</td>
            <td>{cert.issuer or '&mdash;'}</td></tr>
        <tr><td style="color:#888;">Expiry</td>
            <td style="color:{flag_color};">{expiry_str}</td></tr>
        <tr><td style="color:#888;">Days Remaining</td>
            <td>{cert.days_until_expiry}</td></tr>
        <tr><td style="color:#888;">Thumbprint</td>
            <td style="font-size:11px; color:#aaa; word-break:break-all;">
                {cert.thumbprint}</td></tr>
        <tr><td style="color:#888;">Key Usage</td>
            <td>{cert.key_usage or '&mdash;'}</td></tr>
        <tr><td style="color:#888;">Has Private Key</td>
            <td>{'Yes' if cert.has_private_key else 'No'}</td></tr>
        <tr><td style="color:#888;">Status</td>
            <td style="color:{flag_color}; font-weight:bold;">
                {cert.flag or 'Valid'}</td></tr>
        </table>
        </body></html>
        """

        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml(html)
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

        # Connected AFTER the addTab loop: addTab fires currentChanged for the
        # first tab, which would start a load during create_widget().
        self._tabs.currentChanged.connect(self._on_tab_changed)
        outer_layout.addWidget(self._tabs)
        return outer

    def _on_tab_changed(self, index: int) -> None:
        tab = self._tabs.widget(index) if self._tabs is not None else None
        if tab is not None and hasattr(tab, "ensure_loaded"):
            tab.ensure_loaded()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        for tab in self._each_tab():
            if hasattr(tab, "_load"):
                tab._load()

    def on_activate(self) -> None:
        # Opening the module used to load nothing: the certificate list stayed
        # empty until the 60s auto-refresh first ticked (the timer's first
        # tick is a full interval after the tab is opened), or until someone
        # thought to click Refresh. Load the visible store now, the others as
        # they are opened.
        if self._tabs is not None:
            current = self._tabs.currentWidget()
            if current is not None and hasattr(current, "ensure_loaded"):
                current.ensure_loaded()

    def on_deactivate(self) -> None:
        for tab in self._each_tab():
            if hasattr(tab, "_stop"):
                tab._stop()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.on_deactivate()
