"""Software Inventory pane: chips with live counts, search, computed findings,
a detail panel, runtime/duplicate/end-of-life flags and exports.  The logic is
in ``software_analysis`` and ``software_reader`` (Qt-free)."""
import logging
import re
import socket
import subprocess
from typing import List, Optional

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTableWidget,
    QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, fit_table, set_role
from core.worker import Worker
from modules.software_inventory import software_analysis as sa
# Re-exported: other panes import these from here.
from modules.software_inventory.software_reader import (  # noqa: F401
    SoftwareEntry, _read_registry_uninstall, fetch_software, fetch_software_inventory,
    format_estimated_size, format_install_date,
)
from ui.table_items import numeric_item, paint_severity

logger = logging.getLogger(__name__)

COLUMNS = ["Name", "Version", "Publisher", "Installed", "Age", "Size", "Type", "Category", "Note"]
_ROLE_FOR = {"error": "statusError", "warning": "statusWarning", "info": "statusInfo"}


def _alive(widget) -> bool:
    return widget is not None and not sip.isdeleted(widget)


def _size_mb(text: str) -> Optional[float]:
    m = re.match(r"([\d.]+)\s*MB", text or "")
    return float(m.group(1)) if m else None


class _SoftwarePane(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list = []
        self._rows: List[sa.Row] = []
        self._winget: Optional[dict] = None
        self._winget_error = ""
        self._note = ""            # survives the re-render that follows every load / check
        self._entries: List[SoftwareEntry] = []
        self._chip = "Apps"
        self._chip_buttons = {}
        self.loaded = False
        self._build()

    # ── construction ────────────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(self._build_toolbar())
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)
        layout.addLayout(self._build_chips())

        self._findings_box = QVBoxLayout()
        layout.addLayout(self._findings_box)

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        fit_table(self.table, stretch=[0, 2], content=[1, 3, 4, 5, 6, 7])
        self.table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_selection)
        split.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select a program for its detail, product code and uninstall commands.")
        split.addWidget(self.detail)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)

    def _button(self, text: str, slot, tip: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.clicked.connect(slot)
        if tip:
            btn.setToolTip(tip)
        return btn

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.refresh_btn = self._button("Refresh", self.load)
        self.winget_btn = self._button("Check winget updates", self.check_winget,
                                       "Runs 'winget upgrade' (a few seconds, uses the network) and marks matching rows")
        self.uninstall_btn = self._button("Uninstall", self._uninstall)
        self.copy_cmd_btn = self._button("Copy uninstall command", lambda: self._copy_selected("cmd"))
        self.copy_code_btn = self._button("Copy product code", lambda: self._copy_selected("code"))
        self.export_btn = self._button("Export CSV", self._export_csv)
        self.md_btn = self._button("Copy as Markdown", self._copy_markdown,
                                   "Copies the rows currently shown as a ticket-ready table")
        for b in (self.uninstall_btn, self.copy_cmd_btn, self.copy_code_btn):
            b.setEnabled(False)
        for b in (self.refresh_btn, self.winget_btn, self.uninstall_btn, self.copy_cmd_btn,
                  self.copy_code_btn, self.export_btn, self.md_btn):
            bar.addWidget(b)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search name, publisher, version, product code, path...")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(lambda _t: self._apply_filter())
        bar.addWidget(self.filter_edit, 1)
        self.status = QLabel("Click Refresh to load.")
        set_role(self.status, "muted")
        bar.addWidget(self.status)
        return bar

    def _build_chips(self) -> QHBoxLayout:
        row = QHBoxLayout()
        group = QButtonGroup(self)
        group.setExclusive(True)
        for name in sa.CHIPS:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(name == self._chip)
            btn.clicked.connect(lambda _c=False, n=name: self._set_chip(n))
            group.addButton(btn)
            self._chip_buttons[name] = btn
            row.addWidget(btn)
        row.addStretch(1)
        return row

    # ── loading ─────────────────────────────────────────────────────────────

    def _start(self, fn, on_result, on_error) -> None:
        w = Worker(lambda _w: fn())
        w.signals.result.connect(lambda r: on_result(r))
        w.signals.error.connect(lambda e: on_error(e))
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def load(self) -> None:
        self.refresh_btn.setEnabled(False)
        self.status.setText("Loading...")
        self._progress.show()
        self._start(fetch_software_inventory, self._on_loaded, self._on_load_error)

    def _on_loaded(self, entries) -> None:
        if not _alive(self):
            return
        self.loaded = True
        self.refresh_btn.setEnabled(True)
        self._progress.hide()
        self.set_entries(entries)

    def _on_load_error(self, err) -> None:
        if not _alive(self):
            return
        self.refresh_btn.setEnabled(True)
        self._progress.hide()
        self.status.setText(f"Error: {err}")
        logger.warning("Software inventory load failed: %s", err)

    def set_entries(self, entries: List[SoftwareEntry]) -> None:
        self._entries = list(entries)
        self._reanalyze()

    def _reanalyze(self) -> None:
        self._rows = sa.analyze(self._entries, winget=self._winget)
        counts = sa.chip_counts(self._rows)
        for name, btn in self._chip_buttons.items():
            btn.setText(f"{name} ({counts.get(name, 0)})")
        self._render_findings()
        self._apply_filter()

    def check_winget(self) -> None:
        self.winget_btn.setEnabled(False)
        self.status.setText("Asking winget for updates...")
        self._progress.show()

        def run():
            from modules.updates.winget_updater import _run_winget
            out = _run_winget(["upgrade", "--include-unknown", "--accept-source-agreements"])
            return sa.parse_winget_upgrades(out), out

        self._start(run, self._on_winget, self._on_winget_error)

    def _on_winget(self, result) -> None:
        if not _alive(self):
            return
        parsed, out = result
        self.winget_btn.setEnabled(True)
        self._progress.hide()
        if parsed is None:
            self._winget = None
            self._winget_error = (out.strip().splitlines() or ["no output"])[-1][:160]
            self._note = "winget did not return an update table."
        else:
            self._winget, self._winget_error = parsed, ""
            self._note = f"winget: {len(parsed)} update(s) available."
        self._reanalyze()

    def _on_winget_error(self, err) -> None:
        if not _alive(self):
            return
        self.winget_btn.setEnabled(True)
        self._progress.hide()
        self._winget_error = str(err)
        self._note = f"winget check failed: {err}"
        self._reanalyze()

    # ── rendering ───────────────────────────────────────────────────────────

    def _set_chip(self, name: str) -> None:
        self._chip = name
        self._apply_filter()

    def _visible_rows(self) -> List[sa.Row]:
        return sa.filter_rows(self._rows, self._chip, self.filter_edit.text())

    def _render_findings(self) -> None:
        while self._findings_box.count():
            item = self._findings_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for f in sa.software_findings(self._rows, self._winget_error)[:5]:
            lbl = QLabel(f"<b>{f.title}</b>. {f.detail}")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setWordWrap(True)
            set_role(lbl, _ROLE_FOR.get(f.severity, "statusInfo"))
            self._findings_box.addWidget(lbl)

    @staticmethod
    def _identity(row: Optional[sa.Row]):
        return None if row is None else (row.entry.name, row.entry.version, row.entry.type_)

    def _apply_filter(self) -> None:
        keep = self._identity(self.selected_row())        # the 2-minute refresh must not deselect
        rows = self._visible_rows()
        self.table.setSortingEnabled(False)          # a sorted fill scatters each row's cells
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self._fill_row(r, row)
        self.table.setSortingEnabled(True)
        if keep is not None:
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0)
                if item is not None and self._identity(item.data(Qt.ItemDataRole.UserRole)) == keep:
                    self.table.selectRow(r)
                    break
        self.status.setText(f"{len(rows)} shown of {len(self._rows)} entries."
                            + (f"  {self._note}" if self._note else ""))
        self._on_selection()

    def _fill_row(self, r: int, row: sa.Row) -> None:
        e = row.entry
        age = None if row.age_years is None else round(row.age_years, 1)
        installed = row.installed.toordinal() if row.installed else None
        category = ("System component" if e.system_component else "Update" if e.is_update
                    else row.family or "Application")
        cells = [
            centered_item(e.name), centered_item(e.version),
            centered_item(e.publisher),
            numeric_item(row.installed.isoformat() if row.installed else (e.install_date or "n/a"), installed),
            numeric_item("n/a" if age is None else f"{age:g} y", age),
            numeric_item(e.size_mb or "n/a", _size_mb(e.size_mb)),
            centered_item(e.type_), centered_item(category), centered_item(sa._note(row)),
        ]
        cells[0].setData(Qt.ItemDataRole.UserRole, row)
        if row.uninstaller_missing is True or (row.eol is not None and (row.eol_days_left or 0) < 0):
            paint_severity(cells[8], "warning")
        elif row.winget_available or row.superseded_by:
            paint_severity(cells[8], "info")
        for c, item in enumerate(cells):
            self.table.setItem(r, c, item)

    # ── selection and actions ───────────────────────────────────────────────

    def selected_row(self) -> Optional[sa.Row]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_selection(self) -> None:
        row = self.selected_row()
        self.detail.setPlainText(sa.detail_text(row) if row else "")
        e = row.entry if row else None
        self.uninstall_btn.setEnabled(bool(e and e.uninstall_string))
        self.copy_cmd_btn.setEnabled(bool(e and (e.uninstall_string or e.msi_uninstall)))
        self.copy_code_btn.setEnabled(bool(e and e.product_code))

    def _copy_selected(self, what: str) -> None:
        row = self.selected_row()
        if row is None:
            return
        e = row.entry
        text = (e.uninstall_string or e.msi_uninstall) if what == "cmd" else e.product_code
        QApplication.clipboard().setText(text)
        self.status.setText(f"Copied {'the uninstall command' if what == 'cmd' else 'the product code'}.")

    def _uninstall(self) -> None:
        row = self.selected_row()
        if row is None or not row.entry.uninstall_string:
            self.status.setText("No uninstall string available.")
            return
        e = row.entry
        if row.uninstaller_missing is True:
            # The command would launch nothing -- its own program is gone from
            # disk (see software_analysis.uninstaller_target_status). Saying
            # "Uninstall launched" here would be false reassurance.
            self.status.setText(
                f"Cannot uninstall '{e.name}': its own uninstaller program no longer exists on disk. "
                "Remove its install folder by hand, or try Programs and Features / msiexec if those still work.")
            return
        if not confirm_destructive(
                self, "Uninstall", f"Uninstall '{e.name}'?",
                detail=f"Runs: {e.uninstall_string}", irreversible=False):
            return
        # The command comes from the program's own registry entry, not from
        # anything typed here (see CLAUDE.md on shell=True).
        subprocess.Popen(e.uninstall_string, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.status.setText(f"Uninstall launched for {e.name}. Press Refresh when it finishes; "
                            "the entry disappears if the removal succeeded.")

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "software.csv", "CSV (*.csv)")
        if not path:
            return
        rows = self._visible_rows()
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                f.write(sa.rows_to_csv(rows))
        except OSError as e:
            self.status.setText(f"Export failed: {e}")
            logger.warning("Software CSV export failed: %s", e)
            return
        self.status.setText(f"Exported {len(rows)} rows.")

    def _copy_markdown(self) -> None:
        rows = self._visible_rows()
        QApplication.clipboard().setText(sa.rows_to_markdown(rows, socket.gethostname()))
        self.status.setText(f"Copied {len(rows)} rows as Markdown.")

    def cancel(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self.refresh_btn.setEnabled(True)
        self.winget_btn.setEnabled(True)
        self._progress.hide()


class SoftwareModule(BaseModule):
    name = "Software Inventory"
    icon = "📦"
    description = "Installed software: runtimes, duplicate versions, end-of-life flags, export"
    requires_admin = False
    group = ModuleGroup.TOOLS

    def create_widget(self) -> QWidget:
        self._pane = _SoftwarePane()
        # Kept under its old name: other code calls it to trigger a load.
        self._software_load_fn = self._pane.load
        return self._pane

    def on_start(self, app=None) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.cancel_all_workers()
        pane = getattr(self, "_pane", None)
        if pane is not None and _alive(pane):
            pane.cancel()

    def get_refresh_interval(self) -> Optional[int]:
        return 120_000

    def refresh_data(self) -> None:
        pane = getattr(self, "_pane", None)
        if pane is not None and _alive(pane):
            pane.load()

    def on_activate(self) -> None:
        pane = getattr(self, "_pane", None)
        if pane is not None and _alive(pane) and not pane.loaded:
            pane.load()

    def on_deactivate(self) -> None:
        self.cancel_all_workers()
        pane = getattr(self, "_pane", None)
        if pane is not None and _alive(pane):
            pane.cancel()
