"""Category group widget for expandable cleanup categories.

Provides:
- Expand/collapse tree sections
- Auto-refresh support (external control — start/stop via public methods)
- Batch selection
- Context menus
- Background scan via Worker threads
"""
import dataclasses
import os

from PyQt6.QtCore import Qt, QTimer, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QSizePolicy, QLabel, QMenu, QToolButton, QHeaderView,
)

from core.widget_life import widget_is_valid
from core.worker import Worker

try:
    from modules.cleanup.cleanup_scanner import ScanResult, ScanItem, format_size
except ImportError:
    ScanResult = object
    ScanItem = object
    def format_size(s):
        return str(s)


class CategoryGroup(QWidget):
    """Expandable category group with tree view and background scan.

    Lifecycle:
        - Auto-refresh is controlled externally via start_auto_refresh() /
          stop_auto_refresh(). Do NOT start timers in __init__.
        - scan() and clean_all() run in background workers.
    """

    # Emitted when scan completes: (item_count, total_size)
    scan_done = pyqtSignal(int, int)
    # Emitted when a clean completes: the bytes MEASURED as freed.
    freed_bytes = pyqtSignal(int)

    def __init__(
        self,
        group_name: str,
        scanner_fn,
        auto_refresh: bool = False,
        refresh_interval_ms: int = 30_000,
        parent=None,
    ):
        super().__init__(parent)
        self._group_name = group_name
        self._scanner_fn = scanner_fn
        self._results = None
        self._scanning = False
        self._cleaning = False
        self._syncing_ticks = False
        self._auto_refresh_enabled = auto_refresh
        self._refresh_interval_ms = refresh_interval_ms
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_timer_refresh)
        self._workers = []
        self._setup_ui()

    # ── Public API ────────────────────────────────────────────────────────────

    def start_auto_refresh(self) -> None:
        """Start the auto-refresh timer. Safe to call even if already started."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(self._refresh_interval_ms)

    def stop_auto_refresh(self) -> None:
        """Stop the auto-refresh timer."""
        self._refresh_timer.stop()

    def is_auto_refresh_enabled(self) -> bool:
        return self._auto_refresh_enabled

    def scan(self) -> None:
        """Trigger a background scan. Idempotent — ignores if already scanning."""
        if self._scanning:
            return
        self._do_scan()

    def clean_all(self) -> None:
        """Clean all checked items in the tree."""
        self._do_clean_all()

    def cancel(self) -> None:
        """Cancel any in-flight workers."""
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self._scanning = False
        # A cancelled Worker emits `cancelled`, never `result`: nothing else
        # would ever clear this, and _do_clean_all would refuse forever.
        self._cleaning = False
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("Scan")

    # ── Setup ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)

        self._expand_btn = QToolButton()
        self._expand_btn.setText("🗂️ " + self._group_name + " ▼")
        self._expand_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._expand_btn.setFixedHeight(32)
        self._expand_btn.pressed.connect(self._toggle_expand)
        toolbar.addWidget(self._expand_btn)

        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self.scan)
        self._scan_btn.setFixedHeight(32)
        toolbar.addWidget(self._scan_btn)

        self._clean_all_btn = QPushButton("Clean All")
        self._clean_all_btn.clicked.connect(self.clean_all)
        self._clean_all_btn.setEnabled(False)
        self._clean_all_btn.setFixedHeight(32)
        toolbar.addWidget(self._clean_all_btn)

        self._expand_all_btn = QToolButton()
        self._expand_all_btn.setText("Expand All")
        self._expand_all_btn.clicked.connect(self._expand_all)
        self._expand_all_btn.setFixedHeight(32)
        toolbar.addWidget(self._expand_all_btn)

        self._collapse_all_btn = QToolButton()
        self._collapse_all_btn.setText("Collapse All")
        self._collapse_all_btn.clicked.connect(self._collapse_all)
        self._collapse_all_btn.setFixedHeight(32)
        toolbar.addWidget(self._collapse_all_btn)

        self._status_label = QLabel("Ready")
        self._status_label.setFixedWidth(200)
        toolbar.addWidget(self._status_label)

        layout.addLayout(toolbar)

        # Path tree — added directly to the layout (no inner scroll area). When a
        # category is expanded the tree auto-sizes vertically to fit its rows (see
        # _fit_tree_height) so the whole list is readable and the OUTER page scrolls;
        # only very long lists cap their height and scroll internally.
        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Path", "Size"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setStretchLastSection(False)
        # One line per path, elided in the middle (start + file name stay visible);
        # the full path is on the tooltip. No horizontal scrollbar — never overflows.
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._tree.itemChanged.connect(self._on_tick_changed)
        self._tree.setUniformRowHeights(True)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._tree)

        # Collapsed by default — the header acts as a disclosure/dropdown. The path
        # tree is revealed only when the user expands a category, so the dashboard
        # stays compact instead of stacking ten full trees on screen at once.
        self._expanded = False
        self._tree.setVisible(False)
        self._expand_btn.setText("🗂️ " + self._group_name + " ▶")

        # Error label
        self._err_lbl = QLabel()
        self._err_lbl.setObjectName("statusError")
        self._err_lbl.setWordWrap(True)
        self._err_lbl.hide()
        layout.addWidget(self._err_lbl)

    # ── Auto-refresh ─────────────────────────────────────────────────────────

    def _on_timer_refresh(self):
        """Timer-fired refresh — skips if already scanning or cleaning."""
        if self._scanning or self._cleaning:
            return
        self._scan_btn.setText("Scanning...")
        self._scan_btn.setEnabled(False)
        self._do_scan()

    # ── Scan ─────────────────────────────────────────────────────────────────

    def _do_scan(self):
        if self._scanning:
            return
        self._scanning = True
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("Scan")
        self._tree.clear()
        self._err_lbl.hide()
        self._status_label.setText("Scanning...")
        self._clean_all_btn.setEnabled(False)

        def _run(_worker):
            # scanner_fn may be a plain function or a callable
            result = self._scanner_fn()
            return result

        w = Worker(_run)
        w.signals.result.connect(self._on_scan_result)
        w.signals.error.connect(self._on_scan_error)
        w.signals.finished.connect(lambda _wn=w: self._workers.remove(_wn) if _wn in self._workers else None)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_scan_result(self, result):
        self._scanning = False
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("Scan")
        if result is None:
            result = ScanResult(items=[], total_size=0)
        self._apply_result(result)
        self.scan_done.emit(len(result.items), result.total_size)

    def set_result(self, result) -> None:
        """Display a ScanResult produced elsewhere (e.g. the dashboard 'Scan All'),
        so expanding this category's dropdown shows its paths without a re-scan."""
        if result is None:
            result = ScanResult(items=[], total_size=0)
        self._apply_result(result)

    def _apply_result(self, result):
        """Render a ScanResult into the path tree and update the status/clean button."""
        self._results = result
        self._tree.clear()

        if result.items:
            root = QTreeWidgetItem([
                self._group_name,
                format_size(result.total_size),
            ])
            # NOT auto-tristate: Qt would tick every child from the root, an
            # orphaned VM disk included. _on_tick_changed lets the root reach
            # only the rows the scanner selected.
            root.setCheckState(0, Qt.CheckState.Checked)
            root.setFlags(root.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            root.setExpanded(True)
            self._tree.addTopLevelItem(root)

            for item in result.items:
                child = QTreeWidgetItem([item.path, format_size(item.size)])
                child.setToolTip(0, item.path)  # full path on hover (column elides it)
                # A scanner leaves selected=False on what must never go in a bulk
                # clean (orphaned VM disks, old profiles); the tick shows exactly
                # that, and only a person's own tick overrides it.
                child.setCheckState(0, Qt.CheckState.Checked if item.selected
                                    else Qt.CheckState.Unchecked)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setData(0, Qt.ItemDataRole.UserRole, item)
                child.setTextAlignment(
                    1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                root.addChild(child)
            self._sync_root_tick(root)

            self._status_label.setText(
                f"Found {len(result.items)} item(s) — {format_size(result.total_size)}"
            )
            self._clean_all_btn.setEnabled(True)
            if self._expanded:
                root.setExpanded(True)
        else:
            self._status_label.setText("No items found.")
            self._clean_all_btn.setEnabled(False)

        # If this category is open, re-fit its height to the new row count.
        if self._expanded:
            self._fit_tree_height()
            QTimer.singleShot(0, self._fit_tree_height)

    def _on_scan_error(self, err: str):
        self._scanning = False
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("Scan")
        self._status_label.setText(f"Scan error: {err}")

    # ── Clean ────────────────────────────────────────────────────────────────

    @staticmethod
    def _sync_root_tick(root) -> None:
        """Root is ticked iff every row the SCANNER selected is ticked; the rows it
        left unselected neither tick nor untick it."""
        rows = [root.child(j) for j in range(root.childCount())]
        safe = [c for c in rows if getattr(c.data(0, Qt.ItemDataRole.UserRole), "selected", False)]
        is_ticked = [c.checkState(0) == Qt.CheckState.Checked for c in (safe or rows)]
        ticked = all(is_ticked) if safe else any(is_ticked)
        root.setCheckState(0, Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)

    def _on_tick_changed(self, tree_item, column) -> None:
        """A click on the root ticks/unticks only scanner-selected rows (unticking
        clears every row); an unselected row is ticked by its own click alone."""
        if self._syncing_ticks or column != 0:
            return
        self._syncing_ticks = True
        try:
            root = tree_item.parent()
            if root is None:
                want = tree_item.checkState(0)
                for j in range(tree_item.childCount()):
                    child = tree_item.child(j)
                    item = child.data(0, Qt.ItemDataRole.UserRole)
                    if want == Qt.CheckState.Unchecked:
                        child.setCheckState(0, Qt.CheckState.Unchecked)
                    elif item is not None and item.selected:
                        child.setCheckState(0, Qt.CheckState.Checked)
                root = tree_item
            self._sync_root_tick(root)
        finally:
            self._syncing_ticks = False

    def _items_to_clean(self) -> list:
        """The ticked rows, as items the deleter will act on.

        The deleter skips selected=False items, so a row the person ticked by
        hand is passed as a COPY marked selected -- the tick is their choice --
        leaving the scan result itself untouched.
        """
        chosen: list = []
        for i in range(self._tree.topLevelItemCount()):
            root = self._tree.topLevelItem(i)
            for j in range(root.childCount()):
                child = root.child(j)
                item = child.data(0, Qt.ItemDataRole.UserRole)
                if item is not None and child.checkState(0) == Qt.CheckState.Checked:
                    chosen.append(item if item.selected else dataclasses.replace(item, selected=True))
        return chosen

    def _do_clean_all(self):
        if self._cleaning or not self._results:
            return

        checked = self._items_to_clean()
        if not checked:
            return

        self._cleaning = True
        self._clean_all_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._status_label.setText("Cleaning...")

        def _run(worker):
            from modules.cleanup import cleanup_scanner as cs
            report, refused, per_item = cs.delete_items_report(
                checked, stop_wuauserv=False, cancelled=lambda: worker.is_cancelled)
            return (*cs.item_counts(refused, per_item), report.freed)

        # CLOSURES connected to worker signals: Qt cannot auto-disconnect
        # them when this widget is destroyed, so each checks it is alive.
        def _done(result):
            if not widget_is_valid(self):
                return
            deleted, errors, freed = result
            self._cleaning = False
            self._scan_btn.setEnabled(True)
            msg = f"Cleaned {deleted} item(s)"
            if errors:
                msg += f" — {errors} could not be deleted"
                self._err_lbl.setText(f"⚠ {errors} file(s) could not be deleted.")
                self._err_lbl.show()
            self._status_label.setText(msg)
            self.freed_bytes.emit(freed)
            # Rescan to refresh
            self._do_scan()

        def _err(err: str):
            if not widget_is_valid(self):
                return
            self._cleaning = False
            self._scan_btn.setEnabled(True)
            self._status_label.setText(f"Clean error: {err}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    # ── Tree helpers ─────────────────────────────────────────────────────────

    def _toggle_expand(self):
        """Toggle the dropdown that reveals/hides the path tree for this category."""
        self._set_expanded(not self._expanded)

    def _set_expanded(self, expanded: bool):
        """Show or hide the path tree and update the header arrow."""
        self._expanded = expanded
        self._tree.setVisible(expanded)
        arrow = "▼" if expanded else "▶"
        self._expand_btn.setText("🗂️ " + self._group_name + " " + arrow)
        if expanded:
            for i in range(self._tree.topLevelItemCount()):
                self._tree.topLevelItem(i).setExpanded(True)
            self._fit_tree_height()
            QTimer.singleShot(0, self._fit_tree_height)  # re-fit after layout settles

    # ── Auto-size the expanded tree to its content ─────────────────────────────

    _MAX_TREE_HEIGHT = 600  # px; longer lists cap here and scroll internally

    def _visible_row_count(self) -> int:
        """Number of currently-visible rows (roots + children of expanded roots)."""
        rows = 0
        for i in range(self._tree.topLevelItemCount()):
            root = self._tree.topLevelItem(i)
            rows += 1
            if root.isExpanded():
                rows += root.childCount()
        return rows

    def _fit_tree_height(self) -> None:
        """Grow the tree to fit all visible rows so the full list is readable and the
        outer page scrolls; cap very long lists and let them scroll internally."""
        if not self._expanded or not self._tree.isVisible():
            return
        rows = self._visible_row_count()
        if rows == 0:
            self._tree.setFixedHeight(40)
            return
        row_h = self._tree.sizeHintForRow(0) or 22
        header_h = 0 if self._tree.header().isHidden() else self._tree.header().height()
        content_h = header_h + rows * row_h + 2 * self._tree.frameWidth() + 4
        if content_h <= self._MAX_TREE_HEIGHT:
            self._tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._tree.setFixedHeight(content_h)
        else:
            self._tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._tree.setFixedHeight(self._MAX_TREE_HEIGHT)

    def _expand_all(self):
        """Open the dropdown and expand every tree node."""
        self._set_expanded(True)
        for i in range(self._tree.topLevelItemCount()):
            self._tree.topLevelItem(i).setExpanded(True)

    def _collapse_all(self):
        """Hide the path tree (collapse the dropdown)."""
        self._set_expanded(False)

    def _show_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        scan_item = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        open_action = menu.addAction("Open in Explorer")
        action = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if action == open_action and scan_item:
            target = scan_item.path if scan_item.is_dir else os.path.dirname(scan_item.path)
            os.startfile(target)
