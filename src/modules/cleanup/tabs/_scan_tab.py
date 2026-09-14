"""_ScanTab — generic scan/clean tab supporting multiple scanners."""
import logging
import os
from typing import Optional

from PyQt6.QtCore import Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget,
    QTreeWidgetItem, QLabel, QSpinBox, QProgressBar, QMenu,
    QHeaderView,
)

from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup import clean_safe_runner as csr
from modules.cleanup.cleanup_scanner import breakdown, scan_cache

logger = logging.getLogger(__name__)


# ── Safety colour helpers (shared with other tabs) ────────────────────────────

SAFETY_STYLES = {
    "safe":    ("#4caf50", "Safe"),
    "caution": ("#ff9800", "Caution"),
    "danger":  ("#f44336", "Risky"),
}

CONFIRM_BYTES = 500 * 1024 * 1024   # 500 MB

#: Rows that are scaffolding rather than a reading — "Measuring…",
#: "Expand to see what is inside…" — and the fallback for an unknown
#: safety level. Not a semantic colour: it carries no meaning to convey,
#: which is the point of it.
MUTED = "#888888"

# Column-1 UserRole markers for the lazy breakdown of an oversized item.
_NEEDS_BREAKDOWN = "needs-breakdown"
_BREAKDOWN_RUNNING = "breakdown-running"
_BREAKDOWN_DONE = "breakdown-done"


# A breakdown worker can finish after this tab is gone. See the same guard,
# and the reason for `from PyQt6 import sip` rather than `import sip`, in
# _overview_tab.py.
_alive = widget_is_valid



def _sc(level: str) -> str:
    return SAFETY_STYLES.get(level, (MUTED, ""))[0]


def _confirm_large(parent: QWidget, nbytes: int) -> bool:
    if nbytes < CONFIRM_BYTES:
        return True
    from PyQt6.QtWidgets import QMessageBox
    mb = QMessageBox(parent)
    mb.setWindowTitle("Confirm Delete")
    mb.setIcon(QMessageBox.Icon.Warning)
    mb.setText(
        f"You are about to permanently delete <b>{cs.format_size(nbytes)}</b>."
        "<br>This cannot be undone."
    )
    mb.setStandardButtons(
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
    )
    mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return mb.exec() == QMessageBox.StandardButton.Ok


# ── _ScanTab ──────────────────────────────────────────────────────────────────

class _ScanTab(QWidget):
    """
    Generic scan/clean tab supporting multiple scanners.

    scanners: dict  { scanner_fn: (display_label, safety_level) }
    wu_cache: True if wuauserv must be stopped during clean.
    """
    freed_bytes = pyqtSignal(int)

    #: Give up on a scan that has not finished in this long, hand the tab
    #: back, and say what never reported. The worst measured sweep on a real
    #: machine is 35.6s (System Junk, elevated), so this cannot fire on a
    #: healthy scan — if it fires, something is wrong and a dead tab is the
    #: worst possible way to find out.
    SCAN_WATCHDOG_MS = 300_000

    def __init__(self, scanners: dict, wu_cache: bool = False, parent=None):
        super().__init__(parent)
        self._scanners = scanners
        self._wu_cache = wu_cache
        self._result: Optional[cs.ScanResult] = None
        self._scanning = False
        self._cleaning = False
        self._scanned  = False
        self._pending_freed = 0
        self._workers: list = []   # track ALL workers (scan + clean) for cancellation
        self._scan_fns: list = []  # the scanners of the run in flight, in order
        self._scan_worker = None
        self._clean_worker = None
        self._thread_pool = QThreadPool.globalInstance()
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_scan_watchdog)
        self._setup_ui()

    # ── Setup ──

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        tb = QHBoxLayout()
        self._scan_btn   = QPushButton("Scan")
        self._stop_btn   = QPushButton("Stop")
        self._stop_btn.setToolTip("Stop the scan after the scanner now running")
        self._stop_btn.setEnabled(False)
        self._clean_btn  = QPushButton("Clean Selected")
        self._quick_btn  = QPushButton("Quick Clean (Safe Only)")
        self._sel_btn    = QPushButton("Select All")
        self._desel_btn  = QPushButton("Deselect All")
        self._age_lbl    = QLabel("Age filter:")
        self._age_spin   = QSpinBox()
        self._age_spin.setRange(0, 3650)
        self._age_spin.setValue(0)
        self._age_spin.setSuffix(" days")
        self._age_spin.setToolTip(
            "Only include files/folders older than this many days (0 = no filter)"
        )
        self._status     = QLabel("Ready — click Scan or switch to this tab")
        self._clean_btn.setEnabled(False)
        self._quick_btn.setEnabled(False)

        for w in (self._scan_btn, self._stop_btn, self._clean_btn,
                  self._quick_btn, self._sel_btn, self._desel_btn):
            tb.addWidget(w)
        tb.addStretch()
        tb.addWidget(self._age_lbl)
        tb.addWidget(self._age_spin)
        tb.addWidget(self._status)
        layout.addLayout(tb)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        # Tree
        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Path / Category", "Size"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._ctx_menu)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        layout.addWidget(self._tree, 1)

        # Safety legend
        legend = QHBoxLayout()
        legend.setSpacing(12)
        for level, (color, label) in SAFETY_STYLES.items():
            dot = QLabel(f"<span style='color:{color};font-size:16px'>●</span>")
            lbl = QLabel(f"<span style='color:{color}'>{label}</span>")
            lbl.setStyleSheet("font-size:11px")
            legend.addWidget(dot)
            legend.addWidget(lbl)
        legend.addStretch()
        layout.addLayout(legend)

        # Error label
        self._err_lbl = QLabel()
        self._err_lbl.setObjectName("statusError")
        self._err_lbl.setWordWrap(True)
        self._err_lbl.hide()
        layout.addWidget(self._err_lbl)

        self._scan_btn.clicked.connect(self._do_scan)
        self._stop_btn.clicked.connect(self._stop_scan)
        self._clean_btn.clicked.connect(self._do_clean)
        self._quick_btn.clicked.connect(self._do_quick_clean)
        self._sel_btn.clicked.connect(self._select_all)
        self._desel_btn.clicked.connect(self._deselect_all)

    # ── Public ──

    def auto_scan(self):
        """Scan automatically on first activation."""
        if not self._scanned:
            self._do_scan()

    # ── Scan ──

    def _do_scan(self):
        if self._scanning:
            return
        self._scanning = True
        self._scanned  = True
        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._clean_btn.setEnabled(False)
        self._quick_btn.setEnabled(False)
        self._tree.clear()
        self._err_lbl.hide()

        min_age = self._age_spin.value()
        scanner_fns = list(self._scanners.keys())
        self._scan_fns = scanner_fns

        # A determinate bar over the scanner count, not a barber's pole.
        # System Junk is 75 scanners and App & Game Caches is 301; the tab
        # used to say "Scanning…" for all of them and nothing else.
        self._progress.setRange(0, len(scanner_fns))
        self._progress.setValue(0)
        self._progress.show()
        self._status.setText(f"Scanning 0/{len(scanner_fns)}…")

        def _run(worker):
            per: dict = {}
            for index, fn in enumerate(scanner_fns, start=1):
                # Worker.cancel() only sets a flag — this is what makes Stop
                # (and switching modules) actually stop the sweep instead of
                # letting it run to completion on the shared pool.
                if worker.is_cancelled:
                    logger.info("Scan stopped after %d/%d scanners",
                                index - 1, len(scanner_fns))
                    break
                worker.signals.progress.emit(index)
                try:
                    r = scan_cache.cached_scan(fn, min_age)
                    if r:
                        per[fn] = r
                except Exception as e:
                    logger.warning(f"Scan function {fn.__name__} failed: {e}")
            merged = cs.ScanResult()
            for r in per.values():
                merged.items.extend(r.items)
            # Two scanners can legitimately find the same directory —
            # %TEMP% and %LOCALAPPDATA%\Temp are the same path, so
            # scan_temp_files and scan_user_crash_dumps both measured
            # 39.4 GB here and summing them offered 78.8 GB of "junk" for
            # 39.4 GB of files. It also had Clean deleting the same tree
            # twice.
            merged.items = cs.dedupe_items(merged.items)
            merged.total_size = sum(i.size for i in merged.items)
            return merged, per

        self._watchdog.start(self.SCAN_WATCHDOG_MS)
        self._scan_worker = Worker(_run)
        self._scan_worker.signals.progress.connect(self._on_scan_progress)
        self._scan_worker.signals.result.connect(self._on_scan_result)
        self._scan_worker.signals.error.connect(self._on_scan_error)
        self._workers.append(self._scan_worker)
        self._thread_pool.start(self._scan_worker)

    def _on_scan_progress(self, index: int) -> None:
        """Name the scanner now running, and how far through the list it is."""
        if not self._scanning:
            return
        total = len(self._scan_fns)
        self._progress.setValue(index)
        label = ""
        if 0 < index <= total:
            meta = self._scanners.get(self._scan_fns[index - 1])
            label = meta[0] if meta else self._scan_fns[index - 1].__name__
        self._status.setText(f"Scanning {index}/{total} — {label}")

    def _stop_scan(self) -> None:
        """Stop after the scanner now running, keeping whatever was found."""
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        self._cancel_all()

    # ── Breakdown of an oversized item ──

    def _on_item_expanded(self, node: QTreeWidgetItem) -> None:
        if node.data(1, Qt.ItemDataRole.UserRole) == _NEEDS_BREAKDOWN:
            self._fill_breakdown(node)

    def _fill_breakdown(self, node: QTreeWidgetItem) -> None:
        """Replace the placeholder with what is actually inside.

        Measured on a worker: a breakdown of %TEMP% walks 46,825
        directories, and doing that on the UI thread freezes the app for
        the three seconds it takes.
        """
        scan_item = node.data(0, Qt.ItemDataRole.UserRole)
        if scan_item is None:
            return
        node.setData(1, Qt.ItemDataRole.UserRole, _BREAKDOWN_RUNNING)
        node.takeChildren()
        measuring = QTreeWidgetItem(["Measuring…", ""])
        measuring.setFlags(Qt.ItemFlag.NoItemFlags)
        measuring.setForeground(0, QBrush(QColor(MUTED)))
        node.addChild(measuring)

        path = scan_item.path
        safety = scan_item.safety

        def _run(_worker):
            return breakdown.children_by_size(path)

        def _done(rows):
            if not _alive(self):
                return
            node.takeChildren()
            node.setData(1, Qt.ItemDataRole.UserRole, _BREAKDOWN_DONE)
            if not rows:
                empty = QTreeWidgetItem(["(nothing readable inside)", ""])
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                empty.setForeground(0, QBrush(QColor(MUTED)))
                node.addChild(empty)
                return
            color = _sc(safety)
            for child_path, size in rows:
                inner = cs.ScanItem(path=child_path, size=size,
                                    is_dir=os.path.isdir(child_path),
                                    selected=False, safety=safety)
                row = QTreeWidgetItem(
                    [os.path.basename(child_path) or child_path,
                     cs.format_size(size)])
                row.setCheckState(0, Qt.CheckState.Unchecked)
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setData(0, Qt.ItemDataRole.UserRole, inner)
                row.setForeground(0, QBrush(QColor(color)))
                row.setToolTip(0, child_path)
                row.setTextAlignment(
                    1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                node.addChild(row)

        def _err(message: str):
            if not _alive(self):
                return
            node.takeChildren()
            node.setData(1, Qt.ItemDataRole.UserRole, _BREAKDOWN_DONE)
            failed = QTreeWidgetItem([f"(could not read: {message})", ""])
            failed.setFlags(Qt.ItemFlag.NoItemFlags)
            node.addChild(failed)

        worker = Worker(_run)
        worker.signals.result.connect(_done)
        worker.signals.error.connect(_err)
        self._workers.append(worker)
        self._thread_pool.start(worker)

    def _on_scan_result(self, data):
        merged, per_scanner = data
        self._watchdog.stop()
        self._result  = merged
        self._scanning = False
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress.hide()
        self._tree.clear()

        # Build path → (label, safety) lookup
        path_info: dict = {}
        for fn, (label, safety) in self._scanners.items():
            r = per_scanner.get(fn)
            if r:
                for item in r.items:
                    path_info[item.path] = (label, safety)

        # Group by scanner label → collapsible parent nodes
        grouped: dict = {}
        for item in merged.items:
            label, safety = path_info.get(item.path, ("Other", item.safety))
            if label not in grouped:
                grouped[label] = {"safety": safety, "items": []}
            grouped[label]["items"].append(item)

        # Sort groups by total size descending
        for label, group in sorted(
            grouped.items(),
            key=lambda kv: sum(i.size for i in kv[1]["items"]),
            reverse=True,
        ):
            safety = group["safety"]
            items  = sorted(group["items"], key=lambda x: x.size, reverse=True)
            color  = _sc(safety)
            total  = sum(i.size for i in items)

            parent = QTreeWidgetItem(
                [f"{label}  ({len(items)} item(s))", cs.format_size(total)]
            )
            parent.setFlags(
                parent.flags()
                | Qt.ItemFlag.ItemIsAutoTristate
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            # A group's initial check state must reflect its children's
            # ScanItem.selected values, not force everything to Checked --
            # a group can hold a mix (e.g. an orphaned-profile "danger" item
            # that arrives with selected=False alongside items that don't).
            if all(i.selected for i in items):
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif not any(i.selected for i in items):
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            parent.setForeground(0, QBrush(QColor(color)))
            parent.setForeground(1, QBrush(QColor(color)))
            parent.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._tree.addTopLevelItem(parent)
            parent.setExpanded(True)

            for item in items:
                child = QTreeWidgetItem([item.path, cs.format_size(item.size)])
                child.setCheckState(
                    0, Qt.CheckState.Checked if item.selected else Qt.CheckState.Unchecked)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setData(0, Qt.ItemDataRole.UserRole, item)
                child.setForeground(0, QBrush(QColor(color)))
                child.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                parent.addChild(child)

                # A directory too big to be a single checkbox gets a
                # placeholder, filled in when someone opens it. %TEMP% here
                # is 47.36 GB in one row; measuring every such item up front
                # would repeat the most expensive part of the sweep for rows
                # nobody expands.
                if breakdown.is_worth_expanding(item):
                    child.setData(1, Qt.ItemDataRole.UserRole, _NEEDS_BREAKDOWN)
                    placeholder = QTreeWidgetItem(["Expand to see what is inside…", ""])
                    placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
                    placeholder.setForeground(0, QBrush(QColor(MUTED)))
                    child.addChild(placeholder)

        total_safe = sum(1 for i in merged.items if i.safety == "safe")
        logger.info(
            "Cleanup scan complete: %d item(s) (%d safe), %s",
            len(merged.items), total_safe, cs.format_size(merged.total_size),
        )
        self._status.setText(
            f"{len(merged.items)} item(s)  ({total_safe} safe) — "
            f"{cs.format_size(merged.total_size)}"
        )
        self._clean_btn.setEnabled(len(merged.items) > 0)
        self._quick_btn.setEnabled(total_safe > 0)

    def _on_scan_error(self, err: str):
        self._watchdog.stop()
        self._scanning = False
        self._stop_btn.setEnabled(False)
        self._scan_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText(f"Scan error: {err}")

    # ── Clean ──

    def _get_selected_items(self) -> list:
        """Every ScanItem in the tree, `selected` set from its checkbox.

        Breakdown rows are ScanItems too, so a path can appear twice: once
        as `%TEMP%` and once as `%TEMP%\\wct_p3l` inside it. Anything
        already covered by a selected ancestor is dropped, or the same
        bytes get counted twice in the "about to delete N" confirmation and
        queued for deletion twice.
        """
        items = []
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                scan_item = child.data(0, Qt.ItemDataRole.UserRole)
                if scan_item is None:
                    continue
                scan_item.selected = child.checkState(0) == Qt.CheckState.Checked
                items.append(scan_item)
                for k in range(child.childCount()):
                    grandchild = child.child(k)
                    inner = grandchild.data(0, Qt.ItemDataRole.UserRole)
                    if inner is None:
                        continue
                    inner.selected = (
                        grandchild.checkState(0) == Qt.CheckState.Checked
                        and not scan_item.selected)
                    items.append(inner)
        return items

    def _do_clean(self):
        if self._cleaning or self._result is None:
            return
        selected = self._get_selected_items()
        to_delete = [i for i in selected if i.selected]
        if not to_delete:
            return

        total = sum(i.size for i in to_delete)

        def _on_done(deleted, errors):
            self._on_clean_done((deleted, errors))

        worker = csr.run_clean_safe(
            self, selected, stop_wuauserv=self._wu_cache, confirm="size_gated",
            on_done=_on_done, on_error=self._on_clean_error)
        if worker is None:
            return  # user declined the confirm -- nothing was disabled yet

        self._cleaning = True
        self._pending_freed = total
        self._clean_btn.setEnabled(False)
        self._quick_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._status.setText("Cleaning…")
        self._err_lbl.hide()
        self._progress.setRange(0, 0)
        self._progress.show()
        self._clean_worker = worker
        self._workers.append(worker)

    def _on_clean_done(self, result: tuple):
        deleted, errors = result
        self._cleaning = False
        self._scan_btn.setEnabled(True)
        self._progress.hide()
        msg = f"Cleaned {deleted} item(s)"
        if errors:
            msg += f" — {errors} could not be deleted"
            self._err_lbl.setText(
                f"⚠ {errors} file(s) could not be deleted (in use or access denied)."
            )
            self._err_lbl.show()
        freed = self._pending_freed
        logger.info("Cleanup complete: deleted %d item(s), freed %s, %d error(s)", deleted, cs.format_size(freed), errors)
        self._status.setText(msg)
        self._clean_btn.setEnabled(False)
        self._quick_btn.setEnabled(False)
        self.freed_bytes.emit(self._pending_freed)
        self._pending_freed = 0
        self._do_scan()

    def _on_clean_error(self, err: str):
        self._cleaning = False
        self._scan_btn.setEnabled(True)
        self._clean_btn.setEnabled(self._result is not None and len(self._result.items) > 0)
        self._quick_btn.setEnabled(
            self._result is not None and any(i.safety == "safe" for i in self._result.items)
        )
        self._progress.hide()
        self._status.setText(f"Clean error: {err}")

    def _do_quick_clean(self):
        """Select only safe items then clean."""
        if self._result is None:
            return
        for i in range(self._tree.topLevelItemCount()):
            tw = self._tree.topLevelItem(i)
            for j in range(tw.childCount()):
                child = tw.child(j)
                si = child.data(0, Qt.ItemDataRole.UserRole)
                if si is not None:
                    checked = Qt.CheckState.Checked if si.safety == "safe" else Qt.CheckState.Unchecked
                    child.setCheckState(0, checked)
        self._do_clean()

    # ── Selection helpers ──

    def _select_all(self):
        for i in range(self._tree.topLevelItemCount()):
            self._tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked)

    def _deselect_all(self):
        for i in range(self._tree.topLevelItemCount()):
            self._tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Unchecked)

    def _cancel_all(self, message: str = None) -> None:
        in_flight = self._scanning or self._cleaning
        self._watchdog.stop()
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self._scan_worker = None
        self._clean_worker = None
        if in_flight:
            self._reset_after_cancel(message)

    def _current_scanner_label(self) -> str:
        """The scanner the running sweep last reported starting, if any."""
        index = self._progress.value()
        if not (0 < index <= len(self._scan_fns)):
            return ""
        fn = self._scan_fns[index - 1]
        meta = self._scanners.get(fn)
        return meta[0] if meta else fn.__name__

    def _on_scan_watchdog(self) -> None:
        if not self._scanning:
            return
        label = self._current_scanner_label() or "an unknown scanner"
        logger.warning(
            "Cleanup scan timed out after %.0fs, stuck on %s (%d/%d)",
            self.SCAN_WATCHDOG_MS / 1000, label,
            self._progress.value(), len(self._scan_fns))
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        self._cancel_all(
            message=f"Scan timed out on “{label}” — click Scan to try again")

    def _reset_after_cancel(self, message: str = None) -> None:
        """Put the tab back in a state the user can act on.

        A cancelled Worker emits `cancelled` and never `result` or `error`
        (core/worker.py), so nothing else will ever resolve this scan. Without
        this, switching modules mid-scan left `_scanning` True forever: the
        progress bar stayed up, the status stayed on "Scanning...", and both
        `_do_scan` and `_do_clean` returned early on their in-flight guard —
        a tab that was dead for the life of the process.
        """
        self._scanning = False
        self._cleaning = False
        # Nothing was measured, so the tab has NOT been scanned: let
        # auto_scan() run again the next time it is activated.
        self._scanned = False
        self._pending_freed = 0
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        has_items = self._result is not None and len(self._result.items) > 0
        self._clean_btn.setEnabled(has_items)
        self._quick_btn.setEnabled(
            self._result is not None
            and any(i.safety == "safe" for i in self._result.items)
        )
        self._progress.hide()
        self._status.setText(
            message or "Scan cancelled — click Scan to run it again")

    def _ctx_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        si = item.data(0, Qt.ItemDataRole.UserRole)
        if not si:
            return
        menu = QMenu(self)
        open_act = menu.addAction("Open in Explorer")
        if menu.exec(self._tree.viewport().mapToGlobal(pos)) == open_act:
            target = si.path if si.is_dir else os.path.dirname(si.path)
            os.startfile(target)
