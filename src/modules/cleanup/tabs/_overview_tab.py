"""_OverviewTab — summary of all cleanup categories, scans all in parallel."""
import logging
from typing import Optional

from PyQt6.QtCore import Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QProgressBar,
    QHeaderView,
)

from core.table_ui import centered_item, center_header
from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.cleanup_scanner import scan_cache
from modules.cleanup import browser_scanner as bs
from modules.cleanup.cleanup_scanner import _OV_GROUPS


logger = logging.getLogger(__name__)

# A worker's result can land after its tab is gone. Qt auto-disconnects a
# BOUND METHOD when the receiving QObject is destroyed, but these tabs
# connect closures, and nothing tells Qt what a closure's receiver is — so
# the connection outlives the widget and the callback reaches into a
# deleted C++ object. Found by a hard crash, not an exception.
#
# `from PyQt6 import sip`, not `import sip`: the bare form does not exist
# under PyQt6, so a guard written that way silently falls back to
# "always valid" and protects nothing.
_alive = widget_is_valid



#: Rows that are scaffolding rather than a reading — see
#: _scan_tab.MUTED, which this deliberately mirrors.
MUTED = _scan_tab_muted = "#888888"

_OV_COLS = ["Group", "Total Size", "Safe Size", "Items", "Status"]


class _OverviewTab(QWidget):
    """Summary of all cleanup categories — scans all in parallel on first activation."""
    freed_bytes = pyqtSignal(int)

    #: See _ScanTab.SCAN_WATCHDOG_MS. This sweep measures 6.1s on a real
    #: machine, so the margin here is larger still.
    SCAN_WATCHDOG_MS = 300_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: dict = {}    # group_name -> (total_size, safe_size, item_count)
        self._pending  = 0
        self._scanning = False
        self._scanned  = False
        self._scan_workers: list = []
        self._worker: Optional[Worker] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_scan_watchdog)
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        tb = QHBoxLayout()
        self._scan_btn  = QPushButton("Scan All")
        self._stop_btn  = QPushButton("Stop")
        self._stop_btn.setToolTip("Stop the scan and keep whatever was measured")
        self._stop_btn.setEnabled(False)
        self._clean_btn = QPushButton("Clean All Safe")
        self._status    = QLabel("")
        self._clean_btn.setEnabled(False)
        tb.addWidget(self._scan_btn)
        tb.addWidget(self._stop_btn)
        tb.addWidget(self._clean_btn)
        tb.addStretch()
        tb.addWidget(self._status)
        lay.addLayout(tb)

        self._prog = QProgressBar()
        self._prog.setFixedHeight(4)
        self._prog.setTextVisible(False)
        self._prog.hide()
        lay.addWidget(self._prog)

        # Table
        self._table = QTableWidget(0, len(_OV_COLS))
        self._table.setHorizontalHeaderLabels(_OV_COLS)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        lay.addWidget(self._table, 1)

        self._scan_btn.clicked.connect(self._do_scan_all)
        self._stop_btn.clicked.connect(self._stop_scan)
        self._clean_btn.clicked.connect(self._do_clean_safe)

    def _build_table(self):
        self._table.setRowCount(0)
        for name, _ in _OV_GROUPS:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, centered_item(name))
            self._table.setItem(row, 1, centered_item("—"))
            self._table.setItem(row, 2, centered_item("—"))
            self._table.setItem(row, 3, centered_item("—"))
            status = centered_item("Pending…")
            status.setForeground(QColor(MUTED))
            self._table.setItem(row, 4, status)

    def _update_row(self, group_name: str, total: int, safe: int, count: int):
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is None:
                continue
            if item.text() == group_name:
                sz = QTableWidgetItem(cs.format_size(total))
                sz.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                sz.setForeground(QColor("#5cb85c" if total == 0 else "#cccccc"))
                self._table.setItem(row, 1, sz)

                sf = QTableWidgetItem(cs.format_size(safe))
                sf.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, 2, sf)

                ct = QTableWidgetItem(str(count))
                ct.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, 3, ct)

                status_text = "✓ Clean" if total == 0 else f"{cs.format_size(total)} found"
                st = centered_item(status_text)
                st.setForeground(QColor("#5cb85c" if total == 0 else "#cccccc"))
                self._table.setItem(row, 4, st)
                break

    def auto_scan(self):
        if not self._scanned:
            self._build_table()
            self._do_scan_all()

    def _do_scan_all(self):
        if self._scanning:
            return
        self._scanning = True
        self._scanned  = True
        self._results.clear()
        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._clean_btn.setEnabled(False)
        self._prog.setRange(0, len(_OV_GROUPS))
        self._prog.setValue(0)
        self._prog.show()
        self._status.setText(f"Scanning 0/{len(_OV_GROUPS)} categories…")

        # Reset status column
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 4)
            if item:
                item.setText("Scanning…")
                item.setForeground(QColor("#f0ad4e"))

        self._pending = len(_OV_GROUPS)
        self._watchdog.start(self.SCAN_WATCHDOG_MS)

        for group_name, scanners in _OV_GROUPS:
            def _make_cb(gname=group_name, fns=scanners):
                def _run(_w):
                    total = safe = count = 0
                    if fns is None:
                        # Browser caches — use detect_browsers()
                        try:
                            browsers = bs.detect_browsers()
                            for b in browsers:
                                total += b.total_bytes
                                safe  += b.total_bytes   # browsers are always "safe"
                                count += sum(len(p.categories) for p in b.profiles)
                        except Exception as e:
                            logger.warning(f"Browser scan failed: {e}")
                    else:
                        # Collected first, then deduplicated: two scanners
                        # in one group can find the same directory (%TEMP%
                        # and %LOCALAPPDATA%\Temp are the same path), and
                        # summing their totals reported twice the bytes
                        # that are really there.
                        found = []
                        for fn in fns:
                            try:
                                r = scan_cache.cached_scan(fn, 0)
                                found.extend(r.items)
                            except Exception as e:
                                logger.warning(f"Scan {fn.__name__} failed: {e}")
                        unique = cs.dedupe_items(found)
                        total += sum(i.size for i in unique)
                        safe  += sum(i.size for i in unique if i.safety == "safe")
                        count += len(unique)
                    return gname, total, safe, count

                def _res(data):
                    if not _alive(self):
                        return
                    gn, tot, sf, cnt = data
                    self._results[gn] = (tot, sf, cnt)
                    self._pending -= 1
                    self._note_progress()
                    self._update_row(gn, tot, sf, cnt)
                    if self._pending == 0:
                        self._scan_done()

                def _err(_e):
                    if not _alive(self):
                        return
                    self._pending -= 1
                    self._note_progress()
                    self._update_row(gname, 0, 0, 0)
                    if self._pending == 0:
                        self._scan_done()

                return _run, _res, _err

            run_fn, res_fn, err_fn = _make_cb()
            w = Worker(run_fn)
            w.signals.result.connect(res_fn)
            w.signals.error.connect(err_fn)
            self._scan_workers.append(w)
            self._thread_pool.start(w)

    def _note_progress(self) -> None:
        """How many category groups have reported in."""
        total = len(_OV_GROUPS)
        done = total - max(self._pending, 0)
        self._prog.setValue(done)
        if self._scanning:
            self._status.setText(f"Scanning {done}/{total} categories…")

    def _stop_scan(self) -> None:
        """Stop the sweep, keeping whatever the finished groups measured."""
        self._cancel_all()

    def _on_scan_watchdog(self) -> None:
        if not self._scanning:
            return
        outstanding = [name for name, _ in _OV_GROUPS
                       if name not in self._results]
        logger.warning(
            "Cleanup overview scan timed out after %.0fs; no result from: %s",
            self.SCAN_WATCHDOG_MS / 1000, ", ".join(outstanding) or "(none)")
        named = ", ".join(outstanding[:3])
        if len(outstanding) > 3:
            named += f" and {len(outstanding) - 3} more"
        self._cancel_all(
            message=f"Scan timed out — no result from {named}")

    def _scan_done(self):
        self._watchdog.stop()
        self._scanning = False
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._prog.hide()
        total = sum(t for t, _, _ in self._results.values())
        safe  = sum(s for _, s, _ in self._results.values())
        self._status.setText(
            f"Total: {cs.format_size(total)} found — "
            f"{cs.format_size(safe)} in Safe categories"
        )
        self._clean_btn.setEnabled(safe > 0)

    def _do_clean_safe(self):
        """Clean all Safe-tagged items across all scanned categories (non-browser)."""
        all_items = []
        needs_wu = False
        total = 0

        for group_name, scanners in _OV_GROUPS:
            if scanners is None:
                continue  # skip browser (handled by its own tab)
            for fn in scanners:
                try:
                    r = scan_cache.cached_scan(fn, 0)
                    for item in r.items:
                        if item.safety == "safe":
                            item.selected = True
                            all_items.append(item)
                            total += item.size
                    if fn == cs.scan_wu_cache:
                        needs_wu = True
                except Exception as e:
                    logger.warning(f"Clean scan {fn.__name__} failed: {e}")

        if not all_items:
            return
        if not self._confirm_large(total):
            return

        self._clean_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._prog.setRange(0, 0)
        self._prog.show()
        self._status.setText("Cleaning Safe categories…")

        def _run(_w):
            return cs.delete_items(all_items, stop_wuauserv=needs_wu)

        def _done(result):
            if not _alive(self):
                return
            deleted, errors = result
            self._prog.hide()
            self._scan_btn.setEnabled(True)
            self._status.setText(
                f"Cleaned {deleted} item(s)"
                + (f" — {errors} error(s)" if errors else "")
            )
            self.freed_bytes.emit(total)
            # Re-scan overview
            self._scanned = False
            self._build_table()
            self._do_scan_all()

        def _err(e: str):
            if not _alive(self):
                return
            self._prog.hide()
            self._scan_btn.setEnabled(True)
            self._clean_btn.setEnabled(True)
            self._status.setText(f"Error: {e}")

        self._worker = Worker(_run)
        self._worker.signals.result.connect(_done)
        self._worker.signals.error.connect(_err)
        self._thread_pool.start(self._worker)

    def _confirm_large(self, nbytes: int) -> bool:
        from modules.cleanup.tabs._scan_tab import _confirm_large as _cf
        return _cf(self, nbytes)

    def _cancel_all(self, message: str = None) -> None:
        in_flight = self._scanning or self._worker is not None
        self._watchdog.stop()
        for w in self._scan_workers:
            w.cancel()
        self._scan_workers.clear()
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
        if in_flight:
            self._reset_after_cancel(message)

    def _reset_after_cancel(self, message: str = None) -> None:
        """Put the tab back in a state the user can act on.

        A cancelled Worker emits `cancelled` and never `result` or `error`
        (core/worker.py), so `_pending` would never reach zero and
        `_scan_done` would never run. Switching modules mid-scan therefore
        left this tab spinning on "Scanning all categories..." for the life
        of the process, with `_do_scan_all` returning early on `_scanning`.
        """
        self._scanning = False
        self._pending = 0
        # Nothing was measured, so the tab has NOT been scanned: let
        # auto_scan() run again the next time the module is activated.
        self._scanned = False
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._clean_btn.setEnabled(False)
        self._prog.hide()
        self._status.setText(
            message or "Scan cancelled — click Scan All to run it again")
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 4)
            if item is not None and item.text() == "Scanning…":
                item.setText("Cancelled")
                item.setForeground(QColor(MUTED))
