"""The TreeSize pane: ribbon, nav bar, panes, status bar (spec 5.1).

Layout, top to bottom:

    ribbon (tabs + active page)
    nav bar: ◂ ▸ ▴ | path combo | ▶ | scan overview
    splitter: [ directory tree / drive list ] | [ view tabs ]
    status bar

The chart is a view in the right-hand tab strip, not a separate panel; the
Drive List, scan overview and status bar are each individually toggleable from
the View tab, as in Pro.

The shell owns wiring and no scanning: it hands a target to a Worker and turns
results into widget state. That keeps the whole file readable and means the
engine stays testable without a display.
"""
import logging
import os

from PyQt6.QtCore import QModelIndex, Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ..scan.filters import FilterSet
from ..store import compare, scan_file, snapshots
from ..store.aggregates import AggregateCache
from ..store.node_store import EXCLUDED
from .backstage import Backstage, FindResults, TitleRow
from .context_menu import RowActions
from .directory_tree import DirectoryTree
from .formatting import Mode, Unit
from .panels import ElevationBanner, DriveList, ScanOverview, TreeSizeStatusBar, drive_space
from ui.error_banner import ErrorBanner
from .options_dialog import OptionsDialog, load_settings, save_settings
from .ribbon import Ribbon
from .search_dialog import DuplicatesDialog, SearchDialog
from ..actions import exporters, scheduler
from core.admin_utils import is_admin, restart_as_admin

from ..scan.watcher import Watcher, apply_change
from .scan_worker import ScanWorker
from .tree_model import NodeIndexRole
from .views.chart import ChartView
from .views.details import DEFAULT_COLUMNS, DetailsView
from .views.history import HistoryView
from .views.tables import (
    AgeView, ExtensionsView, FileGroupsView, TopFilesView, UsersView,
)

logger = logging.getLogger(__name__)

MODE_ACTIONS = {
    "mode.size": Mode.SIZE,
    "mode.allocated": Mode.ALLOCATED,
    "mode.files": Mode.FILES,
    "mode.percent": Mode.PERCENT,
}
UNIT_ACTIONS = {
    "unit.auto": Unit.AUTO, "unit.tb": Unit.TB, "unit.gb": Unit.GB,
    "unit.mb": Unit.MB, "unit.kb": Unit.KB, "unit.b": Unit.B,
}
# The right-hand tab strip, in the product's order. The chart is a VIEW here,
# not a separate panel -- that is spec 5.1's arrangement and the thing most
# clones get wrong.
VIEW_TABS = ("Chart", "Details", "Extensions", "File groups", "Users",
             "Age of Files", "Top Files", "History")


class TreeSizeShell(QWidget):
    scan_finished = pyqtSignal(object)
    #: Emitted from the watcher thread; consumed on the UI thread.
    _changes_seen = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._store = None
        self._root = -1
        self._result = None
        self._worker: ScanWorker | None = None
        self._filters = FilterSet()
        self.row_actions = RowActions(self)
        self._paused = False
        self._pool = QThreadPool.globalInstance()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.ribbon = Ribbon(self)
        self.title_row = TitleRow(self.ribbon, self)
        layout.addWidget(self.title_row)
        layout.addWidget(self.ribbon)

        self.find_results = FindResults(self.ribbon, self)
        layout.addWidget(self.find_results)

        # The File tab opens a full-pane page, not a dropdown. It replaces the
        # whole body rather than sitting inside it, which is what "backstage"
        # means in a ribbon application.
        self.backstage = Backstage(self)
        self.backstage.hide()
        layout.addWidget(self.backstage, 1)

        self.elevation_banner = ElevationBanner(self)
        self.elevation_banner.elevation_requested.connect(self.request_elevation)
        self.elevation_banner.set_elevated(is_admin())
        layout.addWidget(self.elevation_banner)

        self.error_banner = ErrorBanner()
        self.error_banner.hide()
        layout.addWidget(self.error_banner)

        self.nav_bar = self._build_nav_bar()
        layout.addWidget(self.nav_bar)

        self.scan_overview = ScanOverview(self)
        layout.addWidget(self.scan_overview)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        left = QSplitter(Qt.Orientation.Vertical, self.splitter)
        self.directory_tree = DirectoryTree(left)
        self.drive_list = DriveList(left)
        left.addWidget(self.directory_tree)
        left.addWidget(self.drive_list)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 1)

        self.views = QTabWidget(self.splitter)
        self.chart = ChartView(self.views)
        self.details = DetailsView(self.views)
        self.extensions = ExtensionsView(self.views)
        self.file_groups = FileGroupsView(self.views)
        self.users = UsersView(self.views)
        self.ages = AgeView(self.views)
        self.top_files = TopFilesView(parent=self.views)
        self.history = HistoryView(self.views)
        for widget, title in ((self.chart, "Chart"),
                              (self.details, "Details"),
                              (self.extensions, "Extensions"),
                              (self.file_groups, "File groups"),
                              (self.users, "Users"),
                              (self.ages, "Age of Files"),
                              (self.top_files, "Top Files"),
                              (self.history, "History")):
            self.views.addTab(widget, title)
        self._aggregates = AggregateCache()

        self.splitter.addWidget(left)
        self.splitter.addWidget(self.views)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter, 1)

        self.status_bar = TreeSizeStatusBar(self)
        layout.addWidget(self.status_bar)

        self._body = (self.nav_bar, self.splitter, self.scan_overview,
                      self.status_bar)
        self._recent: list[str] = []
        self._backstage_open = False
        self._group_scans = False
        self._comparison = None
        self._autosize_columns = False
        self._watcher: Watcher | None = None
        self._permanent_excludes: tuple[str, ...] = ()
        self._temp_excludes: tuple[str, ...] = ()
        self.config = None
        self._settings = load_settings(None)

        self._wire()
        self.drive_list.refresh()
        self._seed_path_combo()

    # ---- construction ---------------------------------------------------

    def _build_nav_bar(self) -> QWidget:
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(4)
        self.back_button = QPushButton("◂", bar)
        self.forward_button = QPushButton("▸", bar)
        self.up_button = QPushButton("▴", bar)
        for button in (self.back_button, self.forward_button, self.up_button):
            button.setFixedWidth(28)
            button.setObjectName("navButton")
            row.addWidget(button)
        self.path_combo = QComboBox(bar)
        self.path_combo.setEditable(True)
        self.path_combo.setMinimumWidth(320)
        row.addWidget(self.path_combo, 1)
        self.go_button = QPushButton("▶", bar)
        self.go_button.setFixedWidth(28)
        self.go_button.setObjectName("navButton")
        row.addWidget(self.go_button)
        self.scan_state = QLabel("", bar)
        self.scan_state.setObjectName("scanState")
        row.addWidget(self.scan_state)
        return bar

    def _wire(self) -> None:
        self.go_button.clicked.connect(self._start_scan_from_combo)
        self.path_combo.lineEdit().returnPressed.connect(self._start_scan_from_combo)
        self.path_combo.activated.connect(self._on_path_chosen)
        self.up_button.clicked.connect(self._go_up)
        self.drive_list.drive_activated.connect(self.start_scan)
        self.directory_tree.node_selected.connect(self._on_node_selected)
        self.directory_tree.node_activated.connect(self._on_node_selected)
        self.details.node_activated.connect(self._drill_into)
        self.chart.node_clicked.connect(self._on_node_selected)
        self.top_files.node_activated.connect(self._drill_into)
        self.history.snapshot_chosen.connect(self.compare_with_file)

        # One context menu for every pane: the same right-click must offer the
        # same things wherever it happens, or the module stops feeling like a
        # single product.
        self.directory_tree.customContextMenuRequested.connect(
            self._tree_context_menu)
        for table in (self.details, self.top_files, self.extensions,
                      self.file_groups, self.users, self.ages):
            table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.details.customContextMenuRequested.connect(
            lambda pos: self._table_context_menu(self.details, pos))
        self.top_files.customContextMenuRequested.connect(
            lambda pos: self._table_context_menu(self.top_files, pos))
        # Aggregates are computed lazily per tab: building all six for every
        # selection would walk the subtree six times for five views nobody is
        # looking at.
        self.views.currentChanged.connect(lambda _i: self._refresh_right_pane())
        self.views.currentChanged.connect(self._update_contextual_tabs)

        for action_id, mode in MODE_ACTIONS.items():
            self.ribbon.action(action_id).triggered.connect(
                lambda _checked=False, m=mode: self.set_mode(m))
        for action_id, unit in UNIT_ACTIONS.items():
            self.ribbon.action(action_id).triggered.connect(
                lambda _checked=False, u=unit: self.set_unit(u))

        self.ribbon.action("scan.stop").triggered.connect(self.stop_scan)
        self.ribbon.action("scan.pause").triggered.connect(self.toggle_pause)
        self.ribbon.action("scan.refresh").triggered.connect(self.refresh_scan)
        self.ribbon.action("scan.remove").triggered.connect(self.clear_scan)
        self.ribbon.action("tree.expand").triggered.connect(
            lambda: self.directory_tree.expandToDepth(1))
        self.ribbon.action("sort.size").triggered.connect(
            lambda: self.directory_tree.sortByColumn(1, Qt.SortOrder.DescendingOrder))
        self.ribbon.action("sort.name").triggered.connect(
            lambda: self.directory_tree.sortByColumn(0, Qt.SortOrder.AscendingOrder))

        for action_id, widget in (("panel.drives", self.drive_list),
                                  ("panel.overview", self.scan_overview),
                                  ("panel.status", self.status_bar)):
            action = self.ribbon.action(action_id)
            action.setChecked(True)
            action.toggled.connect(widget.setVisible)

        self.title_row.find_requested.connect(self.find_results.search)
        self.find_results.command_chosen.connect(self._run_command)
        self.ribbon.tab_changed.connect(self._on_tab_changed)
        self.backstage.closed.connect(self._close_backstage)
        self.backstage.scan_requested.connect(self.start_scan)
        self.backstage.action_requested.connect(self._run_command)

        self._changes_seen.connect(self._on_watched_changes)

        self._wire_menus()

        self.ribbon.action("mode.size").setChecked(True)
        self.ribbon.action("unit.auto").setChecked(True)
        self.ribbon.action("unit.decimals.1").setChecked(True)
        self.ribbon.action("hidesmall.off").setChecked(True)
        self.ribbon.set_enabled("view.changes", False)
        self._set_scanning(False)

    def _wire_menus(self) -> None:
        """Give every dropdown item something to do.

        A menu entry that does nothing is worse than an absent one: it reads as
        broken rather than unfinished.
        """
        act = self.ribbon.action

        for depth in (1, 2, 3):
            act("tree.expand.%d" % depth).triggered.connect(
                lambda _c=False, d=depth: self.directory_tree.expandToDepth(d - 1))
        act("tree.expand.all").triggered.connect(self.directory_tree.expandAll)
        act("tree.expand.size").triggered.connect(self.expand_to_threshold)
        act("tree.collapse.all").triggered.connect(self.directory_tree.collapseAll)

        for decimals in (0, 1, 2):
            act("unit.decimals.%d" % decimals).triggered.connect(
                lambda _c=False, d=decimals: self.set_decimals(d))

        for suffix, widget in (("chart", self.chart), ("details", self.details),
                               ("extensions", self.extensions),
                               ("groups", self.file_groups), ("users", self.users),
                               ("age", self.ages), ("top", self.top_files)):
            act("view.go." + suffix).triggered.connect(
                lambda _c=False, w=widget: self.views.setCurrentWidget(w))

        # Menu parents: clicking the FACE does the obvious thing rather than
        # nothing, and the arrow still offers the variants. A split button
        # whose left half is dead is a button that looks broken.
        act("scan.select").triggered.connect(self._browse_for_folder)
        act("result.export").triggered.connect(self.export_any)
        act("scan.exclude").triggered.connect(self._exclude_pattern)
        act("view.select").triggered.connect(self._cycle_view)
        act("view.hidesmall").triggered.connect(self._prompt_hide_smaller)
        act("unit.decimals").triggered.connect(self._prompt_decimals)
        act("scan.schedule").triggered.connect(lambda: self.schedule_scan("DAILY"))
        act("details.autosize").toggled.connect(self.set_autosize_columns)

        act("tools.search").triggered.connect(self.open_search)
        act("tools.search.open").triggered.connect(self.open_search)
        act("tools.duplicates").triggered.connect(self.open_duplicates)
        act("tools.scheduled").triggered.connect(self.manage_scheduled)
        act("tools.restore").triggered.connect(self.open_system_restore)
        act("tools.software").triggered.connect(self.open_installed_software)

        act("scan.watch").toggled.connect(self.set_watching)
        act("tools.snapshot").triggered.connect(self.create_snapshot)
        act("compare.saved").triggered.connect(self.compare_with_saved_scan)
        act("compare.snapshot").triggered.connect(self.compare_with_snapshot)
        act("compare.path").triggered.connect(self.compare_with_path)
        act("view.changes").toggled.connect(self.set_show_changes)

        act("details.reset").triggered.connect(
            lambda: self.details.set_visible_columns(DEFAULT_COLUMNS))
        act("details.fit").triggered.connect(self._fit_details_columns)
        act("details.columns").triggered.connect(self._popup_column_menu)

        act("tree.find").triggered.connect(self.find_in_tree)
        act("view.hideempty").toggled.connect(self.set_hide_empty)
        act("view.group").toggled.connect(self.set_group_scans)
        act("help.contents").triggered.connect(self.show_help)
        act("result.email").triggered.connect(self.email_result)
        act("tools.options.export").triggered.connect(self.export_settings)
        act("tools.options.import").triggered.connect(self.import_settings)

        act("tools.options.open").triggered.connect(self.open_options)
        act("tools.options").triggered.connect(self.open_options)
        act("tools.options.reset").triggered.connect(self.reset_options)
        act("tools.recyclebin").triggered.connect(self.empty_recycle_bin)
        act("tools.mapdrive").triggered.connect(self.map_network_drive)
        act("help.about").triggered.connect(self.show_about)
        act("tools.admin").triggered.connect(self.request_elevation)

        act("scan.refresh.all").triggered.connect(self.refresh_scan)
        act("scan.refresh.selected").triggered.connect(self._refresh_selected)
        act("scan.stop.all").triggered.connect(self.stop_scan)

        act("export.file").triggered.connect(self.export_any)
        for extension in ("csv", "xlsx", "pdf", "html", "xml", "db", "txt"):
            act("export." + extension).triggered.connect(
                lambda _c=False, e=extension: self._export(e))
        act("schedule.daily").triggered.connect(
            lambda: self.schedule_scan("DAILY"))
        act("schedule.weekly").triggered.connect(
            lambda: self.schedule_scan("WEEKLY"))
        act("schedule.remove").triggered.connect(self.unschedule_scan)
        act("export.clipboard").triggered.connect(self._export_clipboard)

        act("exclude.selected").triggered.connect(self._exclude_selected)
        act("exclude.pattern").triggered.connect(self._exclude_pattern)
        act("exclude.permanent").triggered.connect(self._exclude_permanent)
        act("exclude.clear").triggered.connect(self._clear_exclusions)

        for suffix, threshold in (("off", 0), ("1mb", 1 << 20),
                                  ("10mb", 10 << 20), ("100mb", 100 << 20)):
            act("hidesmall." + suffix).triggered.connect(
                lambda _c=False, t=threshold, sfx=suffix: self._set_min_size(t, sfx))

        self._refresh_target_menu()

    def _run_command(self, action_id: str) -> None:
        """Fire a ribbon command by id, from the Find box or the backstage.

        A menu parent (Export, Select scan target) has no useful click action of
        its own, so its menu is popped up instead of triggering nothing.
        """
        self._close_backstage()
        menu = self.ribbon.menu(action_id)
        action = self.ribbon.actions_by_id.get(action_id)
        if menu is not None and menu.actions():
            menu.popup(self.mapToGlobal(self.rect().topLeft()))
            return
        if action is not None:
            action.trigger()

    def _on_tab_changed(self, name: str) -> None:
        if name == "File":
            self.backstage.set_recent(self._recent)
            for widget in self._body:
                widget.hide()
            self.ribbon.pages.hide()
            self.backstage.show()
            self._backstage_open = True
        else:
            self._close_backstage()

    def _close_backstage(self) -> None:
        # Tracked explicitly rather than read off isVisible(): a widget that has
        # not been shown yet reports isVisible() False even while the backstage
        # is the active page, and this method would then return without ever
        # restoring the body.
        if not self._backstage_open:
            return
        self._backstage_open = False
        self._group_scans = False
        self._comparison = None
        self._autosize_columns = False
        self._watcher: Watcher | None = None
        self.config = None
        self._settings = load_settings(None)
        self.backstage.hide()
        self.ribbon.pages.show()
        for widget in self._body:
            widget.show()
        # Panel toggles own the visibility of two of these, so honour them
        # rather than forcing everything back on.
        for action_id, widget in (("panel.overview", self.scan_overview),
                                  ("panel.status", self.status_bar)):
            widget.setVisible(self.ribbon.action(action_id).isChecked())
        if self.ribbon.tab_bar.currentIndex() == 0:
            self.ribbon.tab_bar.setCurrentIndex(1)

    # ---- remote targets -------------------------------------------------

    def scan_remote(self) -> None:
        """Scan an SSH or WebDAV target into the same store (spec 6).

        Runs on the calling thread deliberately: the remote walk is one request
        per directory, and threading it before the local scan\u2019s incremental
        population exists would mean two different threading stories to keep
        straight. It honours the same cancel contract, so moving it later is
        wiring rather than redesign.
        """
        from .remote_dialog import RemoteTargetDialog
        from ..targets.base import TargetError
        from ..store.node_store import DIR
        from ..store.node_store import NodeStore
        from ..store.rollup import rollup

        dialog = RemoteTargetDialog(self)
        if dialog.exec() != RemoteTargetDialog.DialogCode.Accepted:
            return
        target, label = dialog.selected()
        if target is None:
            self.error_banner.set_error(f"Remote target: {label}")
            return

        self._set_scanning(True)
        store = NodeStore()
        root = store.add(-1, label, attrs=DIR)
        try:
            target.enumerate(store, root, on_batch=self._on_batch)
        except TargetError as exc:
            self.error_banner.set_error(f"Remote target: {exc}")
            self._set_scanning(False)
            return
        except Exception as exc:                    # noqa: BLE001
            self.error_banner.set_error(
                f"Remote target: {type(exc).__name__}: {exc}")
            self._set_scanning(False)
            return
        finally:
            target.close()
        rollup(store)

        errors = tuple(getattr(target, "errors", ()) or ())

        class _Result:
            pass

        result = _Result()
        result.store, result.root = store, root
        result.node_count = len(store)
        result.excluded = 0
        result.engine = target.id
        result.volume_info = None            # no cluster geometry remotely
        result.complete = not errors
        result.errors = errors
        result.error_count = len(errors)
        result.elapsed = 0.0
        self.path_combo.setEditText(label)
        self.show_result(result)

    # ---- menu-parent faces ----------------------------------------------

    def _cycle_view(self) -> None:
        """Advance to the next view tab, wrapping at the end."""
        self.views.setCurrentIndex(
            (self.views.currentIndex() + 1) % self.views.count())

    def _prompt_hide_smaller(self) -> None:
        megabytes, ok = QInputDialog.getInt(
            self, "Hide elements smaller than",
            "Hide anything below this many MB (0 shows everything):",
            self._filters.min_size // (1 << 20), 0, 1_000_000)
        if ok:
            self._set_min_size(megabytes << 20,
                               "off" if megabytes == 0 else "custom")

    def _prompt_decimals(self) -> None:
        current = self.directory_tree.tree_model._decimals
        value, ok = QInputDialog.getInt(
            self, "Decimals", "Digits after the decimal point:", current, 0, 3)
        if ok:
            self.set_decimals(value)

    def expand_to_threshold(self) -> int:
        """Spec 5.4's expand-to-size-threshold.

        Asked for in MB because that is the unit anyone thinks in when they
        mean "big enough to bother with"; under Number of files it is a plain
        count, since a file count in megabytes is nonsense.
        """
        mode = self.directory_tree.tree_model.mode
        counting = mode is Mode.FILES
        label = ("Expand folders holding at least this many files:"
                 if counting else
                 "Expand folders larger than (MB):")
        value, ok = QInputDialog.getDouble(
            self, "Expand to size", label,
            100.0, 0.0, 1e9, 0 if counting else 1)
        if not ok:
            return 0
        threshold = value if counting else value * (1024 ** 2)
        opened = self.directory_tree.expand_to_size(threshold)
        self.scan_state.setText(f"Expanded {format(opened, ',')} folder(s)")
        return opened

    def set_autosize_columns(self, enabled: bool) -> None:
        """Resize Details columns to their content after every refresh."""
        self._autosize_columns = enabled
        if enabled:
            self._fit_details_columns()

    # ---- tools ----------------------------------------------------------

    def open_search(self) -> None:
        if self._store is None:
            QMessageBox.information(self, "Find files", "Scan something first.")
            return
        dialog = SearchDialog(self, self)
        dialog.node_activated.connect(self.reveal_node)
        dialog.exec()

    def open_duplicates(self) -> None:
        if self._store is None:
            QMessageBox.information(self, "Find duplicates",
                                    "Scan something first.")
            return
        DuplicatesDialog(self, self).exec()

    def manage_scheduled(self) -> None:
        """Report whether a scheduled scan exists, and offer to remove it.

        Deliberately not a task editor: Windows already ships one, and a
        half-featured copy of it would be worse than a button that opens the
        real thing.
        """
        if scheduler.is_scheduled():
            answer = QMessageBox.question(
                self, "Scheduled scans",
                f"A scheduled TreeSize scan exists ({scheduler.TASK_NAME}).\n\n"
                f"Remove it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Open)
            if answer == QMessageBox.StandardButton.Yes:
                self.unschedule_scan()
            elif answer == QMessageBox.StandardButton.Open:
                self._launch(["mmc.exe", "taskschd.msc"])
            return
        answer = QMessageBox.question(
            self, "Scheduled scans",
            "No TreeSize scan is scheduled.\n\n"
            "Open Windows Task Scheduler?",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Open:
            self._launch(["mmc.exe", "taskschd.msc"])

    def open_system_restore(self) -> None:
        self._launch(["SystemPropertiesProtection.exe"])

    def open_installed_software(self) -> None:
        """Windows own uninstall list. Removing software is not this module's
        job, and a second uninstaller is a liability, not a feature."""
        self._launch(["control.exe", "appwiz.cpl"])

    def _launch(self, args) -> None:
        import subprocess
        try:
            subprocess.Popen(args)
        except OSError as exc:
            self.error_banner.set_error(f"Could not start {args[0]}: {exc}")

    # ---- scheduling -----------------------------------------------------

    def schedule_scan(self, frequency: str) -> None:
        target = self.path_combo.currentText().strip()
        if not target:
            QMessageBox.information(self, "Schedule scan",
                                    "Pick a scan target first.")
            return
        ok, message = scheduler.schedule(target, frequency)
        if ok:
            QMessageBox.information(self, "Schedule scan", message)
        else:
            self.error_banner.set_error(f"Schedule scan: {message}")
        self.scan_state.setText(message)

    def unschedule_scan(self) -> None:
        ok, message = scheduler.unschedule()
        QMessageBox.information(self, "Scheduled scan", message)
        self.scan_state.setText(message)

    # ---- live updates ---------------------------------------------------

    def set_watching(self, enabled: bool) -> None:
        """Spec 3.5. Off by default: it holds a handle on the scanned root."""
        if not enabled:
            self._stop_watching()
            return
        target = self.path_combo.currentText().strip()
        if self._store is None or not target or not os.path.isdir(target):
            self.ribbon.action("scan.watch").setChecked(False)
            self.scan_state.setText("Scan a folder first to watch it")
            return
        self._watcher = Watcher(target, self._changes_seen.emit)
        self._watcher.start()
        self.scan_state.setText(f"Watching {target}")

    def _stop_watching(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
            self.scan_state.setText("")

    def _on_watched_changes(self, changes) -> None:
        """Apply a coalesced batch. Runs on the UI thread by signal.

        The watcher fires from its own thread, so the store must not be touched
        there: it is emitted through a signal and mutated here, which is the
        same threading contract the scan worker follows.
        """
        if self._store is None:
            return
        # Without the cluster size, `alloc` cannot be recomputed and is left
        # alone rather than guessed -- see watcher._alloc_for.
        cluster = (self._result.volume_info.bytes_per_cluster
                   if self._result and self._result.volume_info else 0)
        total = 0
        structural = False
        for change in changes:
            applied = apply_change(self._store, self._root, change.path, cluster)
            total += applied.delta
            structural = structural or applied.structural
        if not total and not structural:
            return
        if structural:
            # A row appeared or vanished. The model caches a child tuple per
            # node, so a repaint alone would leave the new file invisible and
            # the deleted one still listed.
            self.directory_tree.tree_model.refresh_structure()
        else:
            self.directory_tree.tree_model.refresh_values()
        self.scan_overview.show_node(self._store,
                                     getattr(self, "_selected", self._root),
                                     self.directory_tree.tree_model.unit)
        self._refresh_right_pane()
        from .formatting import format_bytes
        self.scan_state.setText(f"Watching — {format_bytes(total)} changed")

    # ---- snapshots and comparison ---------------------------------------

    def _snapshots(self):
        try:
            return snapshots.enumerate_snapshots()
        except OSError:
            return []

    def create_snapshot(self) -> None:
        if self._store is None:
            QMessageBox.information(self, "Create snapshot",
                                    "Scan something first.")
            return
        target = self.path_combo.currentText().strip() or self._store.name(self._root)
        engine = self._result.engine if self._result else ""
        cluster = (self._result.volume_info.bytes_per_cluster
                   if self._result and self._result.volume_info else 0)
        try:
            path = snapshots.create(self._store, self._root, target,
                                    engine=engine, bytes_per_cluster=cluster)
        except OSError as exc:
            self.error_banner.set_error(f"Create snapshot: {exc}")
            return
        self.scan_state.setText(f"Snapshot saved: {os.path.basename(path)}")
        if self.views.currentWidget() is self.history:
            self.history.set_snapshots(self._snapshots())

    def save_scan_as(self) -> None:
        if self._store is None:
            QMessageBox.information(self, "Save scan", "Scan something first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save scan", "", "TreeSize scan (*.tss)")
        if not path:
            return
        header = scan_file.ScanHeader(
            target=self.path_combo.currentText().strip(),
            engine=self._result.engine if self._result else "")
        try:
            scan_file.save(path, self._store, self._root, header)
        except OSError as exc:
            self.error_banner.set_error(f"Save scan: {exc}")
            return
        self.scan_state.setText("Scan saved")

    def compare_with_saved_scan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Compare with saved scan", "",
            "TreeSize scans (*.tss *.tssnap)")
        if path:
            self.compare_with_file(path)

    def compare_with_snapshot(self) -> None:
        available = self._snapshots()
        if not available:
            QMessageBox.information(
                self, "Compare with snapshot",
                "No snapshots yet. Use Tools then Create snapshot after a scan.")
            return
        labels = [f"{s.when}  —  {s.target}" for s in available]
        choice, ok = QInputDialog.getItem(self, "Compare with snapshot",
                                          "Snapshot:", labels, 0, False)
        if ok and choice in labels:
            self.compare_with_file(available[labels.index(choice)].path)

    def compare_with_path(self) -> None:
        """Compare the current scan against a live folder, scanned now."""
        if self._store is None:
            QMessageBox.information(self, "Compare with path", "Scan something first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Compare with path")
        if not folder:
            return
        from ..scan.scanner import Scanner
        try:
            other = Scanner(folder, filters=self._filters).scan()
        except OSError as exc:
            self.error_banner.set_error(f"Compare with path: {exc}")
            return
        self._show_comparison(other.store, other.root, folder)

    def compare_with_file(self, path: str) -> None:
        if self._store is None:
            QMessageBox.information(self, "Compare", "Scan something first.")
            return
        try:
            other_store, other_root, header = scan_file.load(path)
        except (scan_file.ScanFileError, OSError) as exc:
            self.error_banner.set_error(f"Compare: {exc}")
            return
        self._show_comparison(other_store, other_root,
                              header.target or os.path.basename(path))

    def _show_comparison(self, other_store, other_root, label: str) -> None:
        """Diff runs OLD then NEW: the saved scan is the past, this is now."""
        delta = compare.diff(other_store, other_root, self._store, self._root)
        self._comparison = delta
        self.views.setCurrentWidget(self.history)
        self.history.set_snapshots(self._snapshots())
        self.history.show_comparison(
            f"Compared against {label}: {compare.summarise(delta)}")
        self.details.show_comparison(compare.flatten(delta))
        self.views.setCurrentWidget(self.details)
        # Show size changes only becomes meaningful once a comparison exists,
        # which is exactly what spec 5.5 says.
        self.ribbon.set_enabled("view.changes", True)
        self.ribbon.action("view.changes").setChecked(True)

    def set_show_changes(self, enabled: bool) -> None:
        if enabled and self._comparison is None:
            self.ribbon.action("view.changes").setChecked(False)
            self.scan_state.setText(
                "Compare against a saved scan or snapshot first")
            return
        if enabled:
            self.details.show_comparison(compare.flatten(self._comparison))
        else:
            self.details.show_children_of(self._store,
                                          getattr(self, "_selected", self._root))

    # ---- find, filters, help --------------------------------------------

    def find_in_tree(self, text: str | None = None) -> None:
        """Select the largest node whose name matches, and reveal it.

        Largest rather than first: on a real scan a name fragment matches
        hundreds of nodes, and the one worth jumping to is almost always the
        big one -- that is the question this tool exists to answer.
        """
        if self._store is None:
            return
        if text is None:
            text, ok = QInputDialog.getText(self, "Find in tree",
                                            "Find a file or folder by name:")
            if not ok:
                return
        query = (text or "").strip().lower()
        if not query:
            return
        store = self._store
        best, best_size = -1, -1
        for node in range(len(store)):
            if store.attrs[node] & EXCLUDED:
                continue
            if query in store.name(node).lower() and store.size[node] > best_size:
                best, best_size = node, store.size[node]
        if best < 0:
            self.scan_state.setText(f"No match for {text!r}")
            return
        self.reveal_node(best)
        self.scan_state.setText(f"Found {store.name(best)}")

    def reveal_node(self, node: int) -> None:
        """Expand the tree down to a node and select it."""
        store = self._store
        if store is None or not (0 <= node < len(store)):
            return
        chain = []
        walker = node
        seen = set()
        while walker >= 0 and walker not in seen:
            seen.add(walker)
            chain.append(walker)
            if walker == self._root:
                break
            walker = store.parent[walker]
        model = self.directory_tree.tree_model
        index = model.index(0, 0, QModelIndex())
        for ancestor in reversed(chain[:-1]):
            self.directory_tree.expand(index)
            kids = model.children_of(int(index.data(NodeIndexRole)))
            if ancestor not in kids:
                break
            index = model.index(kids.index(ancestor), 0, index)
        self.directory_tree.setCurrentIndex(index)
        self.directory_tree.scrollTo(index)
        self._on_node_selected(node)

    def set_hide_empty(self, hidden: bool) -> None:
        """Hide zero-byte folders. Pro's "Hide empty folders"."""
        self.directory_tree.tree_model.set_hide_empty(hidden)
        self._refresh_right_pane()

    def set_group_scans(self, grouped: bool) -> None:
        # Grouping applies to multiple scan roots, and this pane holds one at
        # a time. Recorded as state so the toggle is not a lie, and honoured
        # the moment multi-scan lands.
        self._group_scans = grouped

    def show_help(self) -> None:
        QMessageBox.information(
            self, "TreeSize help",
            "Pick a drive from the Drive List, or a folder via Select scan "
            "target.\n\n"
            "Home changes what the numbers mean (Mode) and how they are "
            "written (Unit). Scan controls a running scan. View switches the "
            "right-hand pane and toggles the panels.\n\n"
            "Right-click any row to open it, reveal it in Explorer, exclude "
            "it, or delete it.\n\n"
            "Whole-drive scans are far faster when the application runs as "
            "administrator, which lets it read the NTFS master file table "
            "directly.")

    def email_result(self) -> None:
        """Hand the summary to the default mail client via a mailto: link."""
        import urllib.parse
        import webbrowser
        rows = self._export_rows()
        if len(rows) < 2:
            QMessageBox.information(self, "Send by email", "Nothing to send.")
            return
        target = self.path_combo.currentText().strip() or "scan"
        body = "\n".join("\t".join(row) for row in rows[:40])
        if len(rows) > 41:
            body += f"\n… and {len(rows) - 41:,} more rows"
        link = "mailto:?subject=%s&body=%s" % (
            urllib.parse.quote(f"TreeSize scan of {target}"),
            urllib.parse.quote(body))
        # mailto has a length limit in most clients, so the body is the first
        # 40 rows rather than the whole scan; Export is the route for the rest.
        webbrowser.open(link)

    def export_settings(self) -> None:
        import json
        path, _ = QFileDialog.getSaveFileName(self, "Export settings", "",
                                              "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self._settings, handle, indent=2)
        except OSError as exc:
            self.error_banner.set_error(f"Export failed: {exc}")
            return
        self.scan_state.setText("Settings exported")

    def import_settings(self) -> None:
        import json
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", "",
                                              "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as handle:
                values = json.load(handle)
        except (OSError, ValueError) as exc:
            self.error_banner.set_error(f"Import failed: {exc}")
            return
        if not isinstance(values, dict):
            self.error_banner.set_error(
                "Import failed: that file does not contain TreeSize settings.")
            return
        from .options_dialog import DEFAULTS, save_settings
        merged = dict(DEFAULTS)
        # Only known keys are taken: an arbitrary JSON file must not be able to
        # inject settings this build has never heard of.
        merged.update({k: v for k, v in values.items() if k in DEFAULTS})
        save_settings(self.config, merged)
        self.apply_settings(merged)
        self.scan_state.setText("Settings imported")

    # ---- options --------------------------------------------------------

    def apply_settings(self, settings: dict) -> None:
        """Push settings into the widgets that consume them."""
        self._settings = dict(settings)
        try:
            self.set_unit(Unit(settings["unit"]))
            self.set_decimals(int(settings["decimals"]))
            self.set_mode(Mode(settings["mode"]))
        except (ValueError, KeyError):
            # A stored value from an older build should not stop the module
            # opening; the defaults already loaded are usable.
            logger.debug("apply_settings: giving up on this read", exc_info=True)
            pass
        self._filters.exclude_hidden = bool(settings.get("exclude_hidden", False))
        self._permanent_excludes = tuple(settings.get("exclude_patterns") or ())
        self._apply_exclusions()
        self.top_files._limit = int(settings.get("top_files_limit", 100))
        self.chart.treemap.max_depth = int(settings.get("treemap_depth", 6))
        self._aggregates.clear()
        self._refresh_right_pane()

    def open_options(self) -> None:
        dialog = OptionsDialog(self._settings, self)
        if dialog.exec() != OptionsDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        save_settings(self.config, values)
        self.apply_settings(values)
        self.scan_state.setText("Options saved")

    def reset_options(self) -> None:
        from .options_dialog import DEFAULTS
        save_settings(self.config, dict(DEFAULTS))
        self.apply_settings(dict(DEFAULTS))
        self.scan_state.setText("Options reset to defaults")

    # ---- tools ----------------------------------------------------------

    def empty_recycle_bin(self) -> None:
        import ctypes
        if QMessageBox.question(
                self, "Empty Recycle Bin",
                "Permanently remove everything in the Recycle Bin?"
        ) != QMessageBox.StandardButton.Yes:
            return
        # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND: the
        # confirmation above is ours, so the shell must not ask again.
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
        if result == 0:
            self.scan_state.setText("Recycle Bin emptied")
        else:
            self.scan_state.setText("Recycle Bin was already empty")

    def map_network_drive(self) -> None:
        import subprocess
        subprocess.Popen(["rundll32.exe", "shell32.dll,SHHelpShortcuts_RunDLL",
                          "Connect"])

    def show_about(self) -> None:
        QMessageBox.information(
            self, "About TreeSize",
            "TreeSize\n\nDisk space analysis built on a direct NTFS MFT reader, "
            "with a directory-walk fallback when the fast path is unavailable.\n\n"
            "Part of the Windows client tool.")

    def _is_elevated(self) -> bool:
        return is_admin()

    def _confirm_restart(self) -> bool:
        """Same question main_window asks before the same restart."""
        answer = QMessageBox.question(
            self, "Start as administrator",
            "Whole-drive scans use a much faster path when elevated, and can "
            "read locations a normal user cannot.\n\nThe application will "
            "restart with elevated privileges. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def request_elevation(self) -> bool:
        """Restart elevated, reusing the app's own flow (spec 9).

        This used to be a message box explaining that the user could restart
        the application as administrator themselves -- a description of the
        feature standing in for the feature. `restart_as_admin` is the same
        `core.admin_utils` call main_window's own button makes.

        Returns whether the restart was actually launched.
        """
        if self._is_elevated():
            QMessageBox.information(
                self, "Already elevated",
                "This session is already running as administrator, so "
                "whole-drive scans use the fast MFT path.")
            return False
        if not self._confirm_restart():
            return False
        restart_as_admin()
        return True

    def _update_contextual_tabs(self, *_args) -> None:
        self.ribbon.set_contextual_visible(
            "Details Tools", self.views.currentWidget() is self.details)

    def _fit_details_columns(self) -> None:
        for column in range(self.details.columnCount()):
            if not self.details.isColumnHidden(column):
                self.details.resizeColumnToContents(column)

    def _popup_column_menu(self) -> None:
        from PyQt6.QtCore import QPoint
        self.details._column_menu(QPoint(0, 0))

    def _refresh_target_menu(self) -> None:
        """The scan-target list: drives now attached, then the places worth
        looking at, then the two ways of naming something else.

        Explorer offers somewhere to go rather than making you type a path
        every time; this is that, weighted towards where disk space actually
        goes. `locations` drops anything absent, so the server entries simply
        do not appear on a laptop.
        """
        from .panels import list_drives
        from . import locations

        items = []
        for letter, _total, _free in list_drives():
            root = letter + ":" + chr(92)
            items.append((root, lambda _c=False, r=root: self.start_scan(r)))

        submenus = []
        for group in locations.known_locations():
            entries = [(item.label,
                        lambda _c=False, p=item.path: self.start_scan(p))
                       for item in group.items]
            if group.name == locations.PLACES:
                # The everyday ones sit inline; burying Desktop one level down
                # would cost a click on the entry people reach for most.
                items.append((None, None))
                items.extend(entries)
            else:
                submenus.append((group.name, entries))
        if submenus:
            # One separator before the block, none between: they are the same
            # kind of thing, and a rule between two rows of two reads as a
            # boundary that is not there.
            items.append((None, None))
            items.extend(submenus)

        items.append((None, None))
        items.append(("Select folder\u2026", self._browse_for_folder))
        items.append((None, None))
        items.append(("Remote target\u2026", self.scan_remote))
        self.ribbon.set_menu_items("scan.select", items)

    def _seed_path_combo(self) -> None:
        """Give the address box the same places, Explorer-style.

        It stays editable: pick one from the list or type over it. Scanned
        targets append themselves afterwards, so the two coexist.
        """
        from . import locations

        for group in locations.known_locations():
            for item in group.items:
                if self.path_combo.findText(item.path) < 0:
                    self.path_combo.addItem(item.path)
        # Nothing is preselected -- a path sitting in the box on startup looks
        # like a scan target that has already been chosen.
        self.path_combo.setCurrentIndex(-1)
        self.path_combo.setEditText("")

    def _on_path_chosen(self, index: int) -> None:
        """Choosing from the dropdown scans, as picking a place in Explorer
        navigates. Typing still needs Enter or the Go button."""
        target = self.path_combo.itemText(index).strip()
        if target:
            self.start_scan(target)

    def _browse_for_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder to scan")
        if folder:
            self.start_scan(folder)

    def set_decimals(self, decimals: int) -> None:
        unit = self.directory_tree.tree_model.unit
        self.directory_tree.tree_model.set_unit(unit, decimals)
        for view in (self.details, self.extensions, self.file_groups,
                     self.users, self.ages, self.top_files):
            view.set_unit(unit, decimals)
        for value in (0, 1, 2):
            self.ribbon.action("unit.decimals.%d" % value).setChecked(value == decimals)
        self._refresh_right_pane()

    def _set_min_size(self, threshold: int, chosen: str) -> None:
        self._filters.min_size = threshold
        # A typed-in threshold matches none of the presets, so none stays lit
        # rather than one of them lying about the current value.
        for suffix in ("off", "1mb", "10mb", "100mb"):
            self.ribbon.action("hidesmall." + suffix).setChecked(suffix == chosen)
        if self._result is not None:
            self.refresh_scan()

    def _refresh_selected(self) -> None:
        node = getattr(self, "_selected", -1)
        if self._store is not None and node >= 0:
            self.start_scan(self._store.path(node))

    # ---- exclusions (spec 3.6) ------------------------------------------
    #
    # Two kinds. Temporary rules last this scan; permanent ones go through
    # ConfigManager and apply to every future one. They are held apart rather
    # than in one list so that "clear what I did for this scan" cannot throw
    # away a standing rule.

    def _apply_exclusions(self) -> None:
        self._filters.exclude_globs = tuple(self._permanent_excludes) + tuple(
            self._temp_excludes)

    def add_exclusion(self, pattern: str, permanent: bool = False) -> bool:
        """Add one rule. Returns whether it was added.

        A blank pattern is refused: fnmatch would match nothing, and a rule
        that silently does nothing is worse than no rule at all in a tool
        that deletes things.
        """
        pattern = (pattern or "").strip()
        if not pattern:
            return False
        if permanent:
            if pattern in self._permanent_excludes:
                return False
            self._permanent_excludes = self._permanent_excludes + (pattern,)
            self._persist_exclusions()
        else:
            if pattern in self._temp_excludes:
                return False
            self._temp_excludes = self._temp_excludes + (pattern,)
        self._apply_exclusions()
        return True

    def clear_exclusions(self, permanent: bool = False) -> None:
        self._temp_excludes = ()
        if permanent:
            self._permanent_excludes = ()
            self._persist_exclusions()
        self._apply_exclusions()

    def _persist_exclusions(self) -> None:
        self._settings["exclude_patterns"] = list(self._permanent_excludes)
        save_settings(self.config, {"exclude_patterns":
                                    list(self._permanent_excludes)})

    def _exclude_selected(self) -> None:
        node = getattr(self, "_selected", -1)
        if self._store is None or node < 0:
            return
        if self.add_exclusion(self._store.name(node)):
            self.refresh_scan()

    def _exclude_pattern(self) -> None:
        pattern, ok = QInputDialog.getText(
            self, "Exclude by pattern",
            "Exclude names matching, for this scan (for example *.tmp):")
        if ok and self.add_exclusion(pattern):
            self.refresh_scan()

    def _exclude_permanent(self) -> None:
        pattern, ok = QInputDialog.getText(
            self, "Always exclude by pattern",
            "Exclude names matching, in every scan from now on\n"
            "(for example node_modules). Edit the list under Options.")
        if ok and self.add_exclusion(pattern, permanent=True):
            self.scan_state.setText(f"Always excluding {pattern.strip()}")
            self.refresh_scan()

    def _clear_exclusions(self) -> None:
        # Temporary only. The permanent list is edited under Options, so that
        # one stray click here cannot silently drop a standing rule.
        self.clear_exclusions(permanent=False)
        self._filters.exclude_path_globs = ()
        self._filters.min_size = 0
        self.ribbon.action("hidesmall.off").setChecked(True)
        self.ribbon.set_enabled("view.changes", False)
        if self._result is not None:
            self.refresh_scan()

    def _export_rows(self):
        """The visible subtree as rows, which is what an export should contain."""
        node = getattr(self, "_selected", self._root)
        if self._store is None or node < 0:
            return []
        store = self._store
        rows = [("Name", "Size (bytes)", "Allocated (bytes)", "Files",
                 "Folders", "Path")]
        for child in store.children(node):
            if store.attrs[child] & EXCLUDED:
                continue
            rows.append((store.name(child), str(store.size[child]),
                         str(store.alloc[child]), str(store.file_count[child]),
                         str(store.folder_count[child]), store.path(child)))
        return rows

    def _has_rows_to_export(self) -> bool:
        """Checked BEFORE the file dialog opens.

        Asking someone to name a file and then telling them there was nothing
        to put in it is a worse order than saying so first.
        """
        if len(self._export_rows()) >= 2:
            return True
        QMessageBox.information(self, "Export", "There is nothing to export.")
        return False

    def export_any(self) -> None:
        """One dialog offering every format this machine can produce."""
        if not self._has_rows_to_export():
            return
        formats = exporters.available_formats()
        chooser = ";;".join(formats.values())
        path, _ = QFileDialog.getSaveFileName(
            self, "Export scan result", "", chooser)
        if path:
            self._export_to(path)

    def _export(self, kind: str) -> None:
        if not self._has_rows_to_export():
            return
        formats = exporters.available_formats()
        if kind not in formats:
            QMessageBox.information(
                self, "Export",
                f"The {kind.upper()} format needs a package that is not "
                f"installed on this machine.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export scan result", "", formats[kind])
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += "." + kind
        self._export_to(path)

    def _export_to(self, path: str) -> None:
        rows = self._export_rows()
        title = self.path_combo.currentText().strip() or "TreeSize scan"
        try:
            exporters.export(path, rows, title=title)
        except exporters.ExportError as exc:
            self.error_banner.set_error(f"Export failed: {exc}")
            return
        self.scan_state.setText(
            "Exported %s rows to %s" % (format(len(rows) - 1, ","),
                                        os.path.basename(path)))

    def _export_clipboard(self) -> str:
        """Copy the visible rows and RETURN what was copied.

        Returning the text is not decoration: reading it back off the
        Windows clipboard is timing-dependent while widgets are being
        created and destroyed around it, so a test that round-trips through
        the OS is flaky for reasons unrelated to the export being right.
        """
        rows = self._export_rows()
        if len(rows) < 2:
            return ""
        text = "\n".join("\t".join(row) for row in rows)
        QApplication.clipboard().setText(text)
        self.scan_state.setText("Copied %s rows" % format(len(rows) - 1, ","))
        return text

    def set_mode(self, mode: Mode) -> None:
        """Spec 5.5: Mode is PANE state, not per-view state.

        It reached the tree and stopped there, so asking for Allocated space
        on a volume where size and allocated differ by 240 GB redrew an
        identical chart. The chart is part of the pane.
        """
        self.directory_tree.tree_model.set_mode(mode)
        self.chart.set_value_mode(mode)
        for action_id, candidate in MODE_ACTIONS.items():
            self.ribbon.action(action_id).setChecked(candidate is mode)
        self._refresh_right_pane()

    def set_unit(self, unit: Unit) -> None:
        self.directory_tree.tree_model.set_unit(unit)
        for view in (self.details, self.extensions, self.file_groups,
                     self.users, self.ages, self.top_files):
            view.set_unit(unit)
        for action_id, candidate in UNIT_ACTIONS.items():
            self.ribbon.action(action_id).setChecked(candidate is unit)
        self._refresh_right_pane()

    def _set_scanning(self, scanning: bool) -> None:
        self.ribbon.set_enabled("scan.stop", scanning)
        self.ribbon.set_enabled("scan.pause", scanning)
        self.ribbon.set_enabled("scan.refresh", not scanning and self._store is not None)
        self.ribbon.set_enabled("scan.remove", not scanning and self._store is not None)
        self.scan_state.setText("Scanning…" if scanning else "")

    # ---- scanning -------------------------------------------------------

    def _start_scan_from_combo(self) -> None:
        target = self.path_combo.currentText().strip()
        if target:
            self.start_scan(target)

    def make_scan_worker(self, target: str,
                         filters: FilterSet | None = None) -> ScanWorker:
        """Build the worker with the scan options the user actually chose.

        Its own method because the options have to arrive here from Options,
        and for a whole phase they did not: `charge_all_hardlinks` was a
        checkbox that never reached a Scanner.
        """
        return ScanWorker(
            target, filters=filters or self._filters,
            charge_all_hardlinks=bool(
                self._settings.get("charge_all_hardlinks", False)),
            collect_owners=bool(self._settings.get("collect_owners", False)))

    def start_scan(self, target: str, filters: FilterSet | None = None) -> None:
        """Run the scan on a worker thread and return immediately.

        Everything the UI needs during the scan arrives by signal; nothing
        blocks here, which is what makes Stop and Pause mean anything.
        """
        if self._worker is not None:
            return                       # a scan is already running
        self.path_combo.setEditText(target)
        if self.path_combo.findText(target) < 0:
            self.path_combo.addItem(target)
        if target in self._recent:
            self._recent.remove(target)
        self._recent.insert(0, target)
        del self._recent[8:]

        worker = self.make_scan_worker(target, filters)
        worker.signals.batch_ready.connect(self._on_batch)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.failed.connect(self._on_failed)
        worker.signals.cancelled.connect(self._on_cancelled)
        self._worker = worker
        self._paused = False
        self._set_scanning(True)
        self._pool.start(worker)

    def wait_for_scan(self, timeout_ms: int = 120_000) -> bool:
        """Block until the running scan finishes. For tests and shutdown only."""
        from PyQt6.QtCore import QDeadlineTimer, QCoreApplication
        deadline = QDeadlineTimer(timeout_ms)
        while self._worker is not None and not deadline.hasExpired():
            QCoreApplication.processEvents()
        return self._worker is None

    def _on_batch(self, index_range) -> None:
        start, end = index_range
        self.scan_state.setText(f"Scanning… {end:,} items")

    def _on_finished(self, result) -> None:
        self._worker = None
        self.show_result(result)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self._set_scanning(False)
        self.scan_state.setText(f"Scan failed: {message}")

    def _on_cancelled(self) -> None:
        self._worker = None
        self._set_scanning(False)
        self.scan_state.setText("Scan stopped")

    def show_result(self, result) -> None:
        self._result = result
        self._store = result.store
        self._root = result.root
        self.directory_tree.set_scan(result.store, result.root)
        letter = result.store.name(result.root)[:1]
        total, free = drive_space(letter) if letter.isalpha() else (0, 0)
        self.status_bar.show_result(result, total, free)
        self._aggregates.clear()
        self._on_node_selected(result.root)
        self._set_scanning(False)
        self.scan_state.setText("")
        self.scan_finished.emit(result)

    def stop_scan(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        else:
            self._set_scanning(False)

    def toggle_pause(self) -> None:
        if self._worker is None:
            return
        self._paused = not self._paused
        if self._paused:
            self._worker.pause()
            self.scan_state.setText("Paused")
        else:
            self._worker.resume()
        self.ribbon.action("scan.pause").setText("Resume" if self._paused else "Pause")

    def refresh_scan(self) -> None:
        target = self.path_combo.currentText().strip()
        if target:
            self.start_scan(target, filters=self._filters)

    def clear_scan(self) -> None:
        self._stop_watching()
        self.ribbon.action("scan.watch").setChecked(False)
        self._store = None
        self._root = -1
        self._result = None
        self._aggregates.clear()
        self.directory_tree.tree_model.clear()
        self.details.show_children_of(None, -1)
        self.chart.set_scan(None, -1)
        for view in (self.extensions, self.file_groups, self.users,
                     self.ages, self.top_files):
            view.clear()
        self.scan_overview.clear()
        self.status_bar.clear()
        self._set_scanning(False)

    # ---- context menus --------------------------------------------------

    def _tree_context_menu(self, pos) -> None:
        index = self.directory_tree.indexAt(pos)
        if not index.isValid():
            return
        node = index.data(NodeIndexRole)
        if node is None:
            return
        menu = self.row_actions.menu_for(int(node), self.directory_tree)
        if menu is not None:
            menu.exec(self.directory_tree.viewport().mapToGlobal(pos))

    def _table_context_menu(self, table, pos) -> None:
        item = table.itemAt(pos)
        if item is None:
            return
        from PyQt6.QtCore import Qt as _Qt
        node = item.data(0, _Qt.ItemDataRole.UserRole)
        if node is None:
            from .views.tables import NodeRole
            node = item.data(0, NodeRole)
        if node is None:
            return
        menu = self.row_actions.menu_for(int(node), table)
        if menu is not None:
            menu.exec(table.viewport().mapToGlobal(pos))

    # ---- selection ------------------------------------------------------

    def _on_node_selected(self, node: int) -> None:
        self._selected = node
        self.scan_overview.show_node(self._store, node,
                                     self.directory_tree.tree_model.unit)
        self._refresh_right_pane()

    def _refresh_right_pane(self) -> None:
        """Populate only the visible view."""
        node = getattr(self, "_selected", self._root)
        if self._store is None or node < 0:
            return
        current = self.views.currentWidget()
        if current is self.details:
            self.details.show_children_of(self._store, node)
            if getattr(self, "_autosize_columns", False):
                self._fit_details_columns()
        elif current is self.chart:
            self.chart.set_scan(self._store, node)
        elif current is self.top_files:
            self.top_files.show_subtree(self._store, node, self._aggregates)
        elif current is self.history:
            self.history.set_snapshots(self._snapshots())
        elif current in (self.extensions, self.file_groups, self.users, self.ages):
            current.show_subtree(self._store, node, self._aggregates)

    def _drill_into(self, node: int) -> None:
        self._on_node_selected(node)

    def _go_up(self) -> None:
        node = getattr(self, "_selected", -1)
        if self._store is None or node < 0:
            return
        parent = self._store.parent[node]
        if 0 <= parent < len(self._store):
            self._on_node_selected(parent)
