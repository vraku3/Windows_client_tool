import csv
import datetime
import logging
import os
import subprocess
from html import escape as _html_escape
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QHeaderView, QLineEdit, QLabel,
    QProgressBar, QFileDialog, QCheckBox, QApplication, QMenu,
    QStackedWidget, QComboBox, QMessageBox, QInputDialog,
)
from PyQt6.QtCore import Qt, QThreadPool, QItemSelectionModel
from PyQt6.QtGui import QColor

from core.admin_utils import is_admin
from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.events import NAV_REQUEST_MODULE, NavRequestData
from core.module_groups import ModuleGroup
from core.search_provider import SearchProvider
from core.semantic_colors import semantic
from core.table_ui import (
    centered_item, center_header, fit_columns_once, NumericSortItem,
    restore_column_widths, save_column_widths,
)
from core.widget_life import widget_is_valid
from core.worker import COMWorker, Worker
from modules.driver_manager.driver_reader import (
    DriverInfo, classify_provider, fetch_drivers, list_restore_points,
    published_name_for, _dedup_key, _PSEUDO_CLASSES,
)
from modules.driver_manager.driver_search_provider import DriverSearchProvider
from modules.driver_manager.vendor_updates.provider import (
    provider_for, no_provider_reason, NoProviderReason,
)
from modules.driver_manager.vendor_updates import pipeline as vendor_pipeline
from modules.driver_manager.vendor_updates.nvidia_provider import NvidiaProvider  # noqa: F401 -- import registers the provider
from ui.empty_state import EmptyState

logger = logging.getLogger(__name__)

COLUMNS = ["Device Name", "Class", "Version", "Date", "Publisher", "Provider", "Signed", "Status"]

FLAG_FILTER_OPTIONS = ["All", "Signed only", "Unsigned only", "Has error", "Old"]

# A machine with default scheduled checkpoints can have dozens of restore
# points; the System Restore Points QMessageBox caps its DISPLAY at this
# many (newest first), never the underlying data, or the box grows
# unreadably tall.
_RESTORE_POINTS_DISPLAY_LIMIT = 20

# Task 8: the full inventory export -- distinct from COLUMNS/_do_export
# (which respect the current filter/visible rows and the table's own
# provider-classified view) -- carries the full field set including the
# Task 1/2/3 additions (hardware_id, whql_certified, inf_name).
_INVENTORY_COLUMNS = [
    "Device Name", "Class", "Version", "Date", "Publisher", "Signed",
    "WHQL Certified", "Error Code", "Hardware ID", "INF Name", "Flags",
]


def _inventory_row(d: DriverInfo) -> list:
    return [d.device_name, d.driver_class, d.version, d.date, d.publisher,
            d.signed, d.whql_certified, d.error_code, d.hardware_id,
            d.inf_name, d.flags]


def _row_dedup_key(name_item) -> str:
    """The same key `driver_reader._dedup_key` computes for a `DriverInfo`,
    rebuilt here from what `_populate` stores on a row's column-0 item --
    `device_id` (via UserRole) when the driver has one, else the identical
    name-based fallback. Task 36 established `device_name` is NOT a unique
    key (several distinct physical devices commonly share a generic name,
    e.g. multiple "USB Root Hub" entries) -- resolving a right-clicked or
    exported row back to its own `DriverInfo` by name alone can silently
    act on the wrong physical device. See `_on_context_menu`/`_do_export`.
    """
    device_id = name_item.data(Qt.ItemDataRole.UserRole) or ""
    return device_id or f"\x00name:{name_item.text()}"


def _date_sort_value(date_str: str) -> float:
    if not date_str:
        return 0.0
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").timestamp()
    except ValueError:
        return 0.0
    except OSError:
        # .timestamp() itself raises on a validly-parsed but out-of-range
        # date -- e.g. 1601-01-01, the CIM_DATETIME/FILETIME zero-epoch
        # sentinel WMI can report for a device with no genuine driver
        # date. strptime succeeded; the crash is in the conversion after
        # it, so a bare `except ValueError` around strptime doesn't catch
        # it. Group it with the unparseable case rather than crashing
        # _populate() (which runs on every refresh, filter keystroke,
        # checkbox toggle and flag-combo change).
        return 0.0


def _ask_baseline_name(parent) -> Optional[str]:
    name, ok = QInputDialog.getText(parent, "Save Baseline", "Baseline name:")
    return name.strip() if ok and name.strip() else None


def _choose_baseline(parent, metas: list) -> Optional[str]:
    if not metas:
        return None
    names = [m.name for m in metas]
    choice, ok = QInputDialog.getItem(
        parent, "Diff Against Baseline", "Baseline:", names, 0, False)
    return choice if ok else None


class DriverModule(BaseModule):
    name = "Driver Manager"
    icon = "🖨️"
    description = "View and manage installed drivers"
    #: Reading and browsing is always available with no elevation; the one
    #: destructive action (uninstalling a driver package) calls
    #: require_admin() itself and is refused with a message pointing at the
    #: "Restart as Admin" banner rather than failing silently.
    requires_admin = False
    #: Phase 1: reads (unchanged from Phase 0) need no elevation; only the
    #: new vendor-update write action below checks is_admin() itself.
    read_only_unelevated = True
    group = ModuleGroup.SYSTEM

    #: D23 sort-column persistence key prefix. Mirrors DebloatModule's
    #: `_CONFIG_PREFIX` pattern (src/modules/debloat/debloat_module.py).
    _CONFIG_PREFIX = "modules.driver_manager"

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._table: Optional[QTableWidget] = None
        self._table_stack: Optional[QStackedWidget] = None
        self._empty: Optional[EmptyState] = None
        self._progress: Optional[QProgressBar] = None
        self._status_lbl: Optional[QLabel] = None
        self._filter_edit: Optional[QLineEdit] = None
        self._flag_filter_combo: Optional[QComboBox] = None
        self._select_flagged_btn: Optional[QPushButton] = None
        self._hide_pseudo_cb: Optional[QCheckBox] = None
        self._refresh_btn: Optional[QPushButton] = None
        self._export_btn: Optional[QPushButton] = None
        self._export_inventory_btn: Optional[QPushButton] = None
        self._cancel_backup_btn: Optional[QPushButton] = None
        self._backup_worker: Optional[Worker] = None
        self._restore_points_btn: Optional[QPushButton] = None
        # [list of DriverInfo] -- a single-element cell, not reassigned after
        # this, so DriverSearchProvider (built once in get_search_provider(),
        # itself called from on_start() before create_widget() ever runs) can
        # hold this same object and still see every future refresh's data.
        self._drivers_ref = [[]]
        # Rollback tokens (snapshot_before_install()'s return value) for
        # every vendor update applied this session -- appended to by
        # _run_vendor_update, consumed by Task 9's rollback UI.
        self._applied_update_tokens: List[str] = []
        self._sort_col: int = -1
        #: C07 follow-up: has `_populate()` already run its one-time
        #: resizeColumnsToContents() fit this session? See `_populate()`.
        self._columns_fitted_this_session = False

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        toolbar = self._build_toolbar()
        layout.addLayout(toolbar)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # Interactive, not ResizeToContents: only Interactive columns can
        # hold a manually-set or persisted width (see core/table_ui.py's
        # fit_columns_once/save_column_widths/restore_column_widths). The
        # one-time content fit on first real population (below, in
        # _populate()) keeps a fresh install looking exactly as it did
        # under ResizeToContents.
        for i in range(1, len(COLUMNS)):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        self._table_stack = QStackedWidget()
        self._table_stack.addWidget(self._table)
        self._empty = EmptyState(
            "🖨️", "No drivers loaded", "Click Refresh to scan.", "Refresh")
        self._empty.action_triggered.connect(self._do_refresh)
        self._table_stack.addWidget(self._empty)
        layout.addWidget(self._table_stack, 1)

        self._table_stack.setCurrentIndex(1)

        # D23: restore the last sort column/order this session saved in
        # on_deactivate. Done ONCE here, via the header's indicator, rather
        # than reloaded from config on every _populate() call -- _populate
        # runs on every filter keystroke, hide-pseudo toggle and flag-combo
        # change, and a per-call reload-and-reapply silently reverted an
        # in-session header click back to the last-persisted order the
        # moment any of those fired. setSortingEnabled(True) (above) makes
        # the table re-sort itself on every future row rebuild using
        # whatever the header's indicator currently says, so setting it
        # once here is sufficient -- a later click updates it directly
        # (see _on_header_click) and on_deactivate persists whatever it
        # ends up at.
        cfg = self.app.config if self.app else None
        sort_col = int(cfg.get(f"{self._CONFIG_PREFIX}.sort_column", 0) or 0) if cfg else 0
        sort_order = (Qt.SortOrder(int(cfg.get(f"{self._CONFIG_PREFIX}.sort_order",
                      int(Qt.SortOrder.AscendingOrder.value)) or 0)) if cfg
                      else Qt.SortOrder.AscendingOrder)
        self._table.horizontalHeader().setSortIndicator(sort_col, sort_order)
        self._sort_col = sort_col

        # C07: restore any column widths saved from a previous session.
        if cfg is not None:
            restore_column_widths(self._table, cfg.get, self._CONFIG_PREFIX)

        return self._widget

    def _build_toolbar(self) -> QHBoxLayout:
        """Build and return the toolbar layout with all widgets and signal connections."""
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._export_btn = QPushButton("Export CSV")
        self._export_inventory_btn = QPushButton("Export Inventory")
        snapshots_btn = QPushButton("Snapshots ▾")
        snapshots_menu = QMenu(snapshots_btn)
        snapshots_menu.addAction("Save Baseline...", self._save_baseline_action)
        snapshots_menu.addAction("Diff Against...", self._diff_against_baseline_action)
        snapshots_btn.setMenu(snapshots_menu)
        self._restore_points_btn = QPushButton("System Restore Points")
        devmgr_btn = QPushButton("Open Device Manager")
        wu_btn = QPushButton("Check Windows Update")
        wu_btn.setToolTip(
            "Opens Windows Update settings — this app does not match a "
            "specific device to a specific driver update.")
        self._backup_btn = QPushButton("Backup Drivers")
        self._cancel_backup_btn = QPushButton("Cancel (stops after current file)")
        self._cancel_backup_btn.setVisible(False)
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter by name or class...")
        self._flag_filter_combo = QComboBox()
        self._flag_filter_combo.addItems(FLAG_FILTER_OPTIONS)
        self._select_flagged_btn = QPushButton("Select flagged")
        self._hide_pseudo_cb = QCheckBox("Hide pseudo-devices")
        self._hide_pseudo_cb.setChecked(True)
        self._status_lbl = QLabel("Click Refresh to load drivers.")
        interval = self.get_refresh_interval()
        auto_refresh_lbl = QLabel(f"Auto-refreshes every {interval // 1000}s")
        auto_refresh_lbl.setObjectName("muted")
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(self._export_btn)
        toolbar.addWidget(self._export_inventory_btn)
        toolbar.addWidget(snapshots_btn)
        toolbar.addWidget(self._restore_points_btn)
        toolbar.addWidget(devmgr_btn)
        toolbar.addWidget(wu_btn)
        toolbar.addWidget(self._backup_btn)
        toolbar.addWidget(self._cancel_backup_btn)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self._filter_edit, 1)
        toolbar.addWidget(QLabel("Flag:"))
        toolbar.addWidget(self._flag_filter_combo)
        toolbar.addWidget(self._select_flagged_btn)
        toolbar.addWidget(self._hide_pseudo_cb)
        toolbar.addWidget(self._status_lbl)
        toolbar.addWidget(auto_refresh_lbl)

        # Connect button signals
        self._refresh_btn.clicked.connect(self._do_refresh)
        self._export_btn.clicked.connect(self._do_export)
        self._export_inventory_btn.clicked.connect(self._export_inventory)
        devmgr_btn.clicked.connect(self._open_devmgr)
        self._restore_points_btn.clicked.connect(self._show_restore_points)
        wu_btn.clicked.connect(self._open_windows_update_settings)
        self._backup_btn.clicked.connect(self._backup_drivers)
        self._cancel_backup_btn.clicked.connect(self._on_cancel_backup)
        self._select_flagged_btn.clicked.connect(self._select_all_flagged)
        self._filter_edit.textChanged.connect(
            lambda txt: self._populate(self._drivers_ref[0], txt)
        )
        self._hide_pseudo_cb.stateChanged.connect(
            lambda _state: self._populate(self._drivers_ref[0], self._filter_edit.text())
        )
        self._flag_filter_combo.currentTextChanged.connect(
            lambda _txt: self._populate(self._drivers_ref[0], self._filter_edit.text())
        )

        return toolbar

    def on_start(self, app) -> None:
        self.app = app

    def on_activate(self) -> None:
        if not self._drivers_ref[0]:
            self._do_refresh()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        self._do_refresh()

    def get_search_provider(self) -> Optional[SearchProvider]:
        # A fresh instance per call, same as DebloatSearchProvider -- but
        # unlike that one, this needs a LIVE handle: there is no catalog file
        # to re-read, only whatever the last refresh put in _drivers_ref.
        # Passing the shared cell (never reassigned -- see __init__) means
        # every instance, however many are built, sees the same live data.
        return DriverSearchProvider(self._drivers_ref)

    def on_deactivate(self) -> None:
        header = self._table.horizontalHeader() if self._table else None
        if header is not None and self.app and getattr(self.app, "config", None):
            # `Qt.SortOrder` is a plain `enum.Enum` in this PyQt6 build, not
            # `IntEnum` -- `int(header.sortIndicatorOrder())` raises
            # `TypeError: int() argument must be a string, a bytes-like
            # object or a real number, not 'SortOrder'` (confirmed by a
            # real test exercising this exact call). `.value` first.
            self.app.config.set(f"{self._CONFIG_PREFIX}.sort_column",
                                int(header.sortIndicatorSection()))
            self.app.config.set(f"{self._CONFIG_PREFIX}.sort_order",
                                int(header.sortIndicatorOrder().value))
        if self._table is not None and self.app and getattr(self.app, "config", None):
            save_column_widths(self._table, self.app.config.set, self._CONFIG_PREFIX)
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_header_click(self, logical_index: int) -> None:
        if self._table is None:
            return
        hdr = self._table.horizontalHeader()
        if logical_index == self._sort_col:
            # Toggle order when clicking the same column
            order = hdr.sortIndicatorOrder()
            order = Qt.SortOrder.DescendingOrder if order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        else:
            order = Qt.SortOrder.AscendingOrder
        self._sort_col = logical_index
        self._table.sortItems(logical_index, order)

    def _populate(self, drivers: List[DriverInfo], filter_text: str = "") -> None:
        if self._table is None:
            return
        ft = filter_text.lower()
        hide_pseudo = self._hide_pseudo_cb.isChecked() if self._hide_pseudo_cb else True
        flag = self._flag_filter_combo.currentText() if self._flag_filter_combo else "All"
        visible = [
            d for d in drivers
            if (not ft or ft in d.device_name.lower() or ft in d.driver_class.lower())
            and not (hide_pseudo and d.driver_class in _PSEUDO_CLASSES)
            and (flag == "All"
                 or (flag == "Signed only" and d.signed)
                 or (flag == "Unsigned only" and not d.signed)
                 or (flag == "Has error" and d.error_code != 0)
                 or (flag == "Old" and "Old" in d.flags))
        ]
        self._table.setRowCount(len(visible))
        for r, d in enumerate(visible):
            provider = classify_provider(d.publisher)
            values = [
                d.device_name, d.driver_class, d.version, d.date,
                d.publisher, provider, "✓" if d.signed else "✗", d.flags,
            ]
            for c, val in enumerate(values):
                if c == 3:
                    item = NumericSortItem(str(val), _date_sort_value(d.date))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    item = centered_item(str(val), sortable=(c == 0))
                if c == 0:
                    # Task 36: device_name alone can't tell two distinct
                    # devices apart. Carry the real identity on the row so
                    # a later context-menu click or export can resolve it
                    # back to the right DriverInfo -- see _row_dedup_key.
                    item.setData(Qt.ItemDataRole.UserRole, d.device_id)
                self._table.setItem(r, c, item)
            if d.error_code != 0 or not d.signed:
                for c in range(len(COLUMNS)):
                    cell = self._table.item(r, c)
                    if cell:
                        cell.setForeground(QColor(semantic("error")))

        # C07 follow-up: fit Interactive columns to content ONCE, on the
        # first real population this session, when nothing was persisted
        # for them -- never again after that (_populate runs on every
        # filter keystroke, hide-pseudo toggle and flag-combo change; doing
        # this every time would silently undo an in-session column drag,
        # the same class of bug Task 35 fixed for sort order).
        if not self._columns_fitted_this_session:
            self._columns_fitted_this_session = True
            cfg = self.app.config if self.app else None
            if cfg is not None:
                fit_columns_once(self._table, cfg.get, self._CONFIG_PREFIX)
            else:
                self._table.resizeColumnsToContents()

    def _select_all_flagged(self) -> None:
        if self._table is None:
            return
        self._table.clearSelection()
        selection = self._table.selectionModel()
        if selection is None:
            return
        for r in range(self._table.rowCount()):
            flags_item = self._table.item(r, 7)
            if flags_item and flags_item.text().strip():
                selection.select(self._table.model().index(r, 0),
                                 QItemSelectionModel.SelectionFlag.Select
                                 | QItemSelectionModel.SelectionFlag.Rows)

    def _do_refresh(self) -> None:
        if self._refresh_btn:
            self._refresh_btn.setEnabled(False)
        if self._status_lbl:
            self._status_lbl.setText("Loading...")
        if self._progress:
            self._progress.show()
        if self._table:
            self._table.setRowCount(0)

        worker = COMWorker(lambda _w: fetch_drivers())

        def on_result(data: List[DriverInfo]) -> None:
            # Guard against the widget having been torn down (module
            # switched away, app shutting down, or -- in tests -- the
            # module instance from a previous test) by the time this
            # closure fires; a Qt call on a deleted C++ object crashes
            # the process rather than raising. See core/widget_life.py.
            if not widget_is_valid(self._widget):
                return
            self._drivers_ref[0] = data
            if self._refresh_btn:
                self._refresh_btn.setEnabled(True)
            if self._progress:
                self._progress.hide()
            filter_text = self._filter_edit.text() if self._filter_edit else ""
            self._populate(data, filter_text)
            if self._table_stack is not None:
                self._table_stack.setCurrentIndex(0 if data else 1)
            if self._status_lbl:
                issues = sum(1 for d in data if d.error_code != 0 or not d.signed)
                self._status_lbl.setText(f"{len(data)} drivers, {issues} with issues.")

        def on_error(err_str: str) -> None:
            if not widget_is_valid(self._widget):
                return
            if self._refresh_btn:
                self._refresh_btn.setEnabled(True)
            if self._progress:
                self._progress.hide()
            if self._status_lbl:
                self._status_lbl.setText(f"Error: {err_str}")

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        self._workers.append(worker)

        if self.app and getattr(self.app, "thread_pool", None) is not None:
            self.app.thread_pool.start(worker)
        else:
            QThreadPool.globalInstance().start(worker)

    def _do_export(self) -> None:
        if self._widget is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self._widget, "Export CSV", "drivers.csv", "CSV (*.csv)"
        )
        if not path:
            return
        # Task 36: device_name is not unique -- filter by the same
        # device_id-based key _on_context_menu resolves rows with, or
        # exporting a visible row also exports every OTHER driver sharing
        # its visible name, not just the rows actually shown.
        visible_ids = {_row_dedup_key(self._table.item(r, 0))
                       for r in range(self._table.rowCount())
                       if not self._table.isRowHidden(r)}
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            for d in self._drivers_ref[0]:
                if _dedup_key(d) not in visible_ids:
                    continue
                prov = classify_provider(d.publisher)
                writer.writerow([
                    d.device_name, d.driver_class, d.version, d.date,
                    d.publisher, prov, d.signed, d.flags,
                ])
        if self._status_lbl:
            self._status_lbl.setText(f"Exported to {os.path.basename(path)}")

    def _export_inventory(self) -> None:
        # Distinct from _do_export: this exports the FULL current driver
        # list (self._drivers_ref[0], unfiltered), with the full field set
        # including the Task 1/2/3 additions.
        if self._widget is None:
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self._widget, "Export Driver Inventory", "driver_inventory.csv",
            "CSV (*.csv);;HTML (*.html)")
        if not path:
            return
        drivers = self._drivers_ref[0]
        if path.lower().endswith(".html") or "HTML" in selected_filter:
            self._write_inventory_html(path, drivers)
        else:
            self._write_inventory_csv(path, drivers)
        if self._status_lbl:
            self._status_lbl.setText(f"Exported inventory to {os.path.basename(path)}")

    def _write_inventory_csv(self, path: str, drivers: List[DriverInfo]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_INVENTORY_COLUMNS)
            for d in drivers:
                writer.writerow(_inventory_row(d))

    def _write_inventory_html(self, path: str, drivers: List[DriverInfo]) -> None:
        rows_html = []
        for d in drivers:
            cells = "".join(f"<td>{_html_escape(str(v))}</td>" for v in _inventory_row(d))
            rows_html.append(f"<tr>{cells}</tr>")
        header_html = "".join(f"<th>{_html_escape(c)}</th>" for c in _INVENTORY_COLUMNS)
        html = (
            "<html><head><meta charset='utf-8'><title>Driver Inventory</title></head>"
            "<body><table border='1' cellspacing='0' cellpadding='4'>"
            f"<tr>{header_html}</tr>{''.join(rows_html)}</table></body></html>"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    def _save_baseline_action(self) -> None:
        name = _ask_baseline_name(self._widget)
        if not name:
            return
        from modules.driver_manager import driver_baselines as db
        if db.baseline_exists(name):
            if QMessageBox.question(
                    self._widget, "Save Baseline",
                    f"A baseline named '{name}' already exists. "
                    f"Overwrite it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        try:
            db.save_baseline(name, self._drivers_ref[0])
        except OSError as exc:
            # The baseline name is free-typed user text -- a very long or
            # otherwise problematic name can make save_baseline's own
            # os.makedirs()/open() raise (e.g. a path exceeding MAX_PATH).
            # Uncaught, that would abort the whole process via qFatal()
            # rather than being caught by main.py's exception handler --
            # this is a PyQt6 slot, not a plain function call.
            logger.warning("Could not save baseline %r: %s", name, exc)
            QMessageBox.warning(
                self._widget, "Save Baseline",
                f"Could not save baseline '{name}': {exc}")
            return
        if self._status_lbl:
            self._status_lbl.setText(f"Saved baseline '{name}'.")

    def _diff_against_baseline_action(self) -> None:
        from modules.driver_manager import driver_baselines as db
        metas = db.list_baselines()
        name = _choose_baseline(self._widget, metas)
        if not name:
            return
        baseline = db.load_baseline(name)
        if baseline is None:
            # Task 5: load_baseline() returns None (rather than raising) when
            # the named baseline can't be read -- a real race against
            # list_baselines(), which just enumerated the sidecar file
            # successfully moments before. Never pass None into
            # diff_against_baseline(); show the user why nothing happened.
            QMessageBox.warning(
                self._widget, "Diff Against Baseline",
                f"Could not load baseline '{name}' -- it may have been "
                f"deleted or corrupted.")
            return
        diff = db.diff_against_baseline(baseline, self._drivers_ref[0])
        lines = [f"Added ({len(diff.added)}):"] + [f"  + {d.device_name}" for d in diff.added]
        lines += [f"Removed ({len(diff.removed)}):"] + [f"  - {d.device_name}" for d in diff.removed]
        lines += [f"Changed ({len(diff.changed)}):"] + [
            f"  ~ {old.device_name}: {old.version} -> {new.version}"
            for old, new in diff.changed]
        QMessageBox.information(self._widget, f"Diff against '{name}'", "\n".join(lines))

    def _export_one_driver(self, published: str) -> None:
        folder = QFileDialog.getExistingDirectory(self._widget, "Export Driver")
        if not folder:
            return
        result = subprocess.run(
            ["pnputil", "/export-driver", published, folder],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            msg = f"Exported {published}"
        else:
            msg = f"Could not export {published}: {result.stdout + result.stderr}"
        logger.info(msg)
        self._status_lbl.setText(msg)

    def _copy_hardware_id(self, hardware_id: str) -> None:
        if hardware_id:
            QApplication.clipboard().setText(hardware_id)

    _FLAG_EXPLANATIONS = {
        "Unsigned": "This driver's publisher could not be verified by "
                    "Windows. It may still work fine, but an unsigned "
                    "driver is a common vector for malware pretending to "
                    "be a hardware driver.",
        "Error": "Windows reports a problem with this device right now -- "
                "see the exact error text in the Status column for what "
                "it is.",
        "Old": "This driver has not been updated in over two years. Many "
              "devices work fine on an old driver forever, but graphics, "
              "network and storage controllers benefit most from staying "
              "current.",
        "date unreadable": "Windows reported a driver date this app could "
                          "not parse -- not necessarily a problem, just "
                          "an unusual value.",
        "No driver installed": "Windows found this device but has no "
                              "driver for it at all -- it will not work "
                              "until one is installed.",
        "Shared Hardware ID": "This device's hardware ID is claimed by "
                             "more than one installed driver package -- "
                             "for example a generic driver and a vendor "
                             "one both bound to it. Not necessarily a "
                             "problem, but worth a look in Device Manager "
                             "if the device is misbehaving.",
    }

    def _explain_flags(self, flags: str) -> str:
        matched = [text for key, text in self._FLAG_EXPLANATIONS.items()
                  if key in flags]
        return "\n\n".join(matched) if matched else (
            "This driver has a flag this app doesn't have an explanation for yet.")

    def _show_flag_explanation(self, flags: str) -> None:
        QMessageBox.information(self._widget, "Why does this matter?",
                                self._explain_flags(flags))

    def _check_for_vendor_update(self, driver: DriverInfo) -> None:
        reason = no_provider_reason(driver)
        if reason == NoProviderReason.UNRECOGNIZED_VENDOR:
            QMessageBox.information(
                self._widget, "Check for Vendor Update",
                f"Could not identify {driver.device_name}'s vendor from "
                f"its hardware ID -- no update check is possible.")
            return
        if reason == NoProviderReason.NO_ADAPTER_FOR_VENDOR:
            QMessageBox.information(
                self._widget, "Check for Vendor Update",
                f"{driver.device_name}'s vendor is recognized, but no "
                f"update source is configured for it yet.")
            return
        provider = provider_for(driver)
        # provider.check_for_update() is a real, uncached-per-call network
        # round trip (NvidiaProvider: a pfid lookup plus a driver-lookup
        # call, up to ~15s each, worse on a cold 7-day pfid cache) -- it
        # must not run on the UI thread, the same reason _do_refresh and
        # _show_restore_points already background their own I/O.
        if self._status_lbl:
            self._status_lbl.setText(
                f"Checking {provider.vendor_name} for an update...")

        def do_check(worker):
            return provider.check_for_update(driver)

        worker = Worker(do_check)

        def on_check_result(update) -> None:
            if not widget_is_valid(self._widget):
                return
            if self._status_lbl:
                self._status_lbl.setText("Click Refresh to load drivers.")
            if update is None:
                QMessageBox.information(
                    self._widget, "Check for Vendor Update",
                    f"No update available for {driver.device_name} "
                    f"(currently {driver.version}).")
                return
            if not is_admin():
                QMessageBox.information(
                    self._widget, "Check for Vendor Update",
                    f"A newer driver ({update.latest_version}) is available "
                    f"for {driver.device_name}, but installing it needs "
                    f"administrator rights. Restart this app as "
                    f"administrator to install it.")
                return
            confirm = QMessageBox.question(
                self._widget, "Check for Vendor Update",
                f"{update.vendor} has version {update.latest_version} "
                f"available for {driver.device_name} (currently "
                f"{update.current_version}).\n\nDownload from "
                f"{update.download_url}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if confirm != QMessageBox.StandardButton.Yes:
                return
            self._run_vendor_update(driver, provider, update)

        def on_check_error(err_str: str) -> None:
            if not widget_is_valid(self._widget):
                return
            if self._status_lbl:
                self._status_lbl.setText("Click Refresh to load drivers.")
            QMessageBox.warning(
                self._widget, "Check for Vendor Update",
                f"Could not check for an update: {err_str}")

        worker.signals.result.connect(on_check_result)
        worker.signals.error.connect(on_check_error)
        self._workers.append(worker)
        if self.app and getattr(self.app, "thread_pool", None) is not None:
            self.app.thread_pool.start(worker)
        else:
            QThreadPool.globalInstance().start(worker)

    def _run_vendor_update(self, driver: DriverInfo, provider, update) -> None:
        if self._status_lbl:
            self._status_lbl.setText(f"Downloading {update.vendor} driver...")

        def do_update(worker):
            download_result = vendor_pipeline.download_and_verify(
                update, allowed_domains=provider.allowed_download_domains)
            if download_result.path is None:
                return ("download_failed", download_result.reason)
            from modules.driver_manager.vendor_updates.rollback import snapshot_before_install
            token = snapshot_before_install(driver)
            install_result = vendor_pipeline.install_light(download_result.path, driver)
            return ("installed", install_result, token)

        worker = Worker(do_update)

        def on_result(result) -> None:
            if not widget_is_valid(self._widget):
                return
            if self._status_lbl:
                self._status_lbl.setText("Click Refresh to load drivers.")
            if result[0] == "download_failed":
                QMessageBox.warning(self._widget, "Check for Vendor Update",
                                   f"Could not use this update: {result[1]}")
                return
            _, install_result, token = result
            if not install_result.ok:
                QMessageBox.warning(self._widget, "Check for Vendor Update",
                                   f"Install failed: {install_result.reason}")
                return
            if token:
                self._applied_update_tokens.append(token)
            QMessageBox.information(self._widget, "Check for Vendor Update",
                                   f"{driver.device_name} updated to "
                                   f"{update.latest_version}. Click Refresh "
                                   f"to see the change.")

        def on_error(err_str: str) -> None:
            if not widget_is_valid(self._widget):
                return
            if self._status_lbl:
                self._status_lbl.setText("Click Refresh to load drivers.")
            QMessageBox.warning(self._widget, "Check for Vendor Update",
                               f"Update failed unexpectedly: {err_str}")

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        self._workers.append(worker)
        if self.app and getattr(self.app, "thread_pool", None) is not None:
            self.app.thread_pool.start(worker)
        else:
            QThreadPool.globalInstance().start(worker)

    def _open_windows_update_settings(self) -> None:
        os.startfile("ms-settings:windowsupdate")

    def _open_devmgr(self) -> None:
        subprocess.Popen(["mmc", "devmgmt.msc"])

    def _show_restore_points(self) -> None:
        # list_restore_points() runs a PowerShell subprocess with a 30s
        # timeout -- doing that synchronously in a toolbar click handler
        # froze the whole window for however long it took (final-review
        # finding I4). A plain Worker, not COMWorker: this is a subprocess
        # call, no WMI/COM involved (core/worker.py's own guidance).
        if self._restore_points_btn:
            self._restore_points_btn.setEnabled(False)
            self._restore_points_btn.setText("Loading…")

        worker = Worker(lambda _w: list_restore_points())

        def on_result(points) -> None:
            if not widget_is_valid(self._widget):
                return
            self._reset_restore_points_btn()
            self._present_restore_points(points)

        def on_error(err_str: str) -> None:
            if not widget_is_valid(self._widget):
                return
            self._reset_restore_points_btn()
            QMessageBox.information(
                self._widget, "System Restore Points",
                f"Could not read System Restore points: {err_str}")

        def on_cancelled() -> None:
            # Worker.run() emits `cancelled` -- never `result` or `error`
            # -- when the cancel flag is set by the time the function
            # returns. on_deactivate() calls cancel_all_workers()
            # unconditionally on module switch, and list_restore_points()
            # can legitimately still be running (up to its own 30s
            # subprocess timeout) when that happens. Without this handler
            # the button stayed stuck on "Loading…"/disabled forever --
            # the same class of bug _backup_drivers's
            # `signals.cancelled.connect(self._on_backup_cancelled)`
            # exists to prevent. No message box here, just recovery: a
            # cancelled load isn't an error worth interrupting the user
            # for, and the widget may already be on its way out anyway.
            if not widget_is_valid(self._widget):
                return
            self._reset_restore_points_btn()

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        worker.signals.cancelled.connect(on_cancelled)
        self._workers.append(worker)

        if self.app and getattr(self.app, "thread_pool", None) is not None:
            self.app.thread_pool.start(worker)
        else:
            QThreadPool.globalInstance().start(worker)

    def _reset_restore_points_btn(self) -> None:
        if self._restore_points_btn:
            self._restore_points_btn.setEnabled(True)
            self._restore_points_btn.setText("System Restore Points")

    def _present_restore_points(self, points: Optional[list]) -> None:
        """Same branching logic `_show_restore_points` ran inline before
        it moved to a background Worker -- unchanged, just called from the
        async result handler."""
        if points is None:
            QMessageBox.information(
                self._widget, "System Restore Points",
                "Could not read System Restore points -- this may need "
                "administrator rights, or System Restore may be off.")
            return
        if not points:
            QMessageBox.information(
                self._widget, "System Restore Points",
                "No restore points found.")
            return
        # Newest first -- CreationTime is a WMI datetime string
        # ("20260201000000.000000-000"), which sorts correctly as plain
        # text since it's already zero-padded, fixed-width, and
        # year-first, without needing to parse it into a real datetime.
        ordered = sorted(points, key=lambda p: p.get("CreationTime", ""), reverse=True)
        # A machine with default scheduled checkpoints can have dozens of
        # restore points -- cap the DISPLAY only (the sort above still
        # covers every point) so the QMessageBox doesn't grow unreadably
        # tall.
        shown = ordered[:_RESTORE_POINTS_DISPLAY_LIMIT]
        lines = [
            f"{p.get('CreationTime', 'Unknown time')}: {p.get('Description', '')}"
            for p in shown
        ]
        remaining = len(ordered) - len(shown)
        if remaining > 0:
            lines.append(f"...and {remaining} more.")
        QMessageBox.information(self._widget, "System Restore Points", "\n".join(lines))

    def _resolve_driver_for_row(self, row: int) -> Optional[DriverInfo]:
        """The row's real `DriverInfo`, resolved the same way
        `_on_context_menu` always has -- Task 36: `device_name` is not
        unique (several distinct physical devices commonly share a
        generic name, e.g. multiple "USB Root Hub" entries), so this
        keys on `device_id` via `_row_dedup_key`/`_dedup_key`, never on
        the visible name alone. Shared by the context menu and the
        double-click handler so the two can never resolve a click on the
        same row to two different devices."""
        name_item = self._table.item(row, 0)
        if name_item is None:
            return None
        row_key = _row_dedup_key(name_item)
        return next((d for d in self._drivers_ref[0]
                    if _dedup_key(d) == row_key), None)

    def _on_cell_double_clicked(self, row: int, _column: int) -> None:
        self._show_driver_details(self._resolve_driver_for_row(row))

    def _show_driver_details(self, driver: Optional[DriverInfo]) -> None:
        if driver is None:
            return
        from modules.driver_manager.driver_detail_dialog import DriverDetailDialog
        # reliability_records is omitted (defaults to None): this module
        # never actually fetches Reliability Monitor data -- see Task 10's
        # documented scope decision in driver_detail_dialog.py's module
        # docstring. None tells the dialog "we never looked", distinct
        # from a real, empty search result.
        dlg = DriverDetailDialog(driver, parent=self._widget)
        dlg.exec()

    def _on_context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        name_item = self._table.item(row, 0)
        device_name = name_item.text()
        # Task 36: device_name is not unique -- resolve by device_id (via
        # the same key driver_reader._dedup_key computes), not by name, or
        # two rows sharing a generic name ("USB Root Hub" x N) can resolve
        # to the wrong physical device.
        driver = self._resolve_driver_for_row(row)
        published = published_name_for(driver.inf_name) if driver else None

        menu = QMenu(self._table)
        act_copy = menu.addAction("Copy device name")
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(device_name))
        act_details = menu.addAction("Details...")
        act_details.setEnabled(bool(driver))
        act_details.triggered.connect(lambda: self._show_driver_details(driver))
        menu.addSeparator()
        act_uninstall = menu.addAction("Uninstall driver package…")
        act_uninstall.setEnabled(bool(published))
        act_uninstall.setToolTip(
            "" if published else
            "This is a driver Windows ships inline, not an installed "
            "OEM package — remove it from Device Manager instead.")
        act_uninstall.triggered.connect(
            lambda: self._do_uninstall_driver(published, device_name))
        act_export_one = menu.addAction("Export this driver…")
        act_export_one.setEnabled(bool(published))
        act_export_one.triggered.connect(
            lambda: self._export_one_driver(published))
        act_copy_hwid = menu.addAction("Copy Hardware ID")
        act_copy_hwid.setEnabled(bool(driver and driver.hardware_id))
        act_copy_hwid.triggered.connect(
            lambda: self._copy_hardware_id(driver.hardware_id if driver else ""))
        act_rollback = menu.addAction("Roll back to previous version…")
        act_rollback.setToolTip(
            "Needs the previous driver still cached, which this app does "
            "not track — opens Device Manager, where Windows can check.")
        act_rollback.triggered.connect(self._open_devmgr)
        act_check_update = menu.addAction("Check for Vendor Update...")
        act_check_update.triggered.connect(
            lambda: self._check_for_vendor_update(driver) if driver else None)
        menu.addSeparator()
        act_cleanup = menu.addAction("Open Cleanup's Superseded Drivers panel")
        act_cleanup.triggered.connect(self._open_cleanup_driver_panel)
        if driver and driver.flags.strip():
            act_explain = menu.addAction("Why does this matter?")
            act_explain.triggered.connect(
                lambda: self._show_flag_explanation(driver.flags))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _do_uninstall_driver(self, published: str, device_name: str) -> None:
        if not self.require_admin():
            return
        if not confirm_destructive(
                self._widget, "Uninstall Driver Package",
                f"Uninstall the driver package for {device_name}?",
                detail=f"Runs: pnputil /delete-driver {published} /uninstall\n"
                      f"The device may stop working until Windows finds "
                      f"another driver for it."):
            return
        result = subprocess.run(
            ["pnputil", "/delete-driver", published, "/uninstall", "/force"],
            capture_output=True, text=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            msg = f"Removed driver package {published}"
        else:
            msg = f"Could not remove {published}: {result.stdout + result.stderr}"
        logger.info(msg)
        self._status_lbl.setText(msg)
        self._do_refresh()

    def _open_cleanup_driver_panel(self) -> None:
        if self.app and hasattr(self.app, "event_bus"):
            self.app.event_bus.publish(
                NAV_REQUEST_MODULE, NavRequestData(module_name="Cleanup"))
            # The Superseded Drivers panel lives on Cleanup's Large Items
            # tab -- selecting the module is what this app's existing
            # navigation event already does; switching to that specific
            # inner tab is Cleanup's own concern, not something Driver
            # Manager reaches into.

    def _backup_drivers(self) -> None:
        """Export all third-party drivers to a user-selected folder using pnputil."""
        if self._widget is None:
            return
        folder = QFileDialog.getExistingDirectory(
            self._widget, "Select Backup Folder"
        )
        if not folder:
            return
        if not confirm_destructive(
                self._widget, "Export All Drivers",
                f"Export every driver to {folder}?",
                detail="This can take a while for a lot of drivers. Progress "
                      "is shown per driver, and Cancel stops after the "
                      "current one.",
                irreversible=False):
            return
        self._backup_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._filter_edit.setEnabled(False)
        self._cancel_backup_btn.setVisible(True)
        self._cancel_backup_btn.setEnabled(True)
        if self._status_lbl:
            self._status_lbl.setText(f"Exporting drivers to {folder}...")

        exportable = [d for d in self._drivers_ref[0] if published_name_for(d.inf_name)]
        if self._progress:
            self._progress.setRange(0, len(exportable))
            self._progress.setValue(0)
            self._progress.show()

        def do_backup(worker):
            # `exportable` is the SAME list computed above, before the
            # worker starts -- not recomputed from `self._drivers_ref[0]`
            # here, which could have been replaced by then (a refresh can
            # land mid-backup; the auto-refresh timer is not gated on a
            # backup being in progress). Recomputing risked desyncing the
            # worker's real export count from the progress bar's range.
            combined_output = []
            failures = 0
            for i, d in enumerate(exportable):
                if worker.is_cancelled:
                    combined_output.append("Cancelled.")
                    break
                published = published_name_for(d.inf_name)
                result = subprocess.run(
                    ["pnputil", "/export-driver", published, folder],
                    capture_output=True, text=True, timeout=300,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                combined_output.append(result.stdout + result.stderr)
                if result.returncode != 0:
                    failures += 1
                worker.signals.progress.emit(i + 1)
            returncode = 0 if failures == 0 else 1
            return "\n".join(combined_output), returncode

        worker = Worker(do_backup)
        worker.signals.progress.connect(self._progress.setValue)
        worker.signals.result.connect(self._on_backup_done)
        worker.signals.error.connect(self._on_backup_error)
        # Worker.run() emits `cancelled` -- never `result` or `error` -- if
        # the cancel flag is set when `do_backup` returns, even when the
        # loop noticed it and returned cleanly. Without this connection a
        # cancelled backup (via the button below, or simply navigating away
        # while one is running, since on_deactivate()/on_stop() call
        # cancel_all_workers() unconditionally) left every control disabled
        # and the progress bar frozen for the rest of the session.
        worker.signals.cancelled.connect(self._on_backup_cancelled)
        self._workers.append(worker)
        self._backup_worker = worker
        if self.app and getattr(self.app, "thread_pool", None) is not None:
            self.app.thread_pool.start(worker)
        else:
            QThreadPool.globalInstance().start(worker)

    def _on_cancel_backup(self) -> None:
        if self._backup_worker is not None:
            self._backup_worker.cancel()
        self._cancel_backup_btn.setEnabled(False)

    def _on_backup_error_line(self, stderr: str) -> str:
        if "access" in stderr.lower() and "denied" in stderr.lower():
            return "denied — this driver package needs administrator"
        return stderr.strip() or "failed for an unreported reason"

    def _on_backup_done(self, result) -> None:
        output, returncode = result
        self._reset_backup_controls()
        if returncode == 0:
            msg = "Driver backup complete — exported to selected folder."
        else:
            msg = f"Driver backup finished: {self._on_backup_error_line(output)}"
        logger.info(msg)
        if self._status_lbl:
            self._status_lbl.setText(msg)

    def _on_backup_error(self, err_str: str) -> None:
        self._reset_backup_controls()
        msg = f"Backup error: {err_str}"
        logger.info(msg)
        if self._status_lbl:
            self._status_lbl.setText(msg)

    def _on_backup_cancelled(self) -> None:
        self._reset_backup_controls()
        msg = "Driver backup cancelled."
        logger.info(msg)
        if self._status_lbl:
            self._status_lbl.setText(msg)

    def _reset_backup_controls(self) -> None:
        self._backup_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._filter_edit.setEnabled(True)
        self._cancel_backup_btn.setVisible(False)
        self._cancel_backup_btn.setEnabled(True)
        if self._progress:
            self._progress.hide()
        self._backup_worker = None
