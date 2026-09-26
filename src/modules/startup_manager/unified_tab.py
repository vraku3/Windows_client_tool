"""The "Everything at startup" tab: one list, chips, search, detail panel.

All logic lives in `persistence.py`; this only renders. The scan runs on a
COMWorker (scheduled tasks and shortcut resolution use COM).
"""
import logging
import os
from datetime import datetime
from typing import List, Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QFileDialog, QMessageBox,
)
from PyQt6 import sip

from core.confirm import confirm_destructive
from core.semantic_colors import semantic
from core.table_ui import center_header, centered_item, set_role
from core.worker import COMWorker
from modules.startup_manager import persistence as P

logger = logging.getLogger(__name__)

COLUMNS = ["Name", "State", "Source", "Scope", "Publisher", "Signature",
           "Boot impact", "First seen", "Findings"]


class _NumItem(QTableWidgetItem):
    """A cell that sorts on a number but shows text."""

    def __init__(self, text: str, value: float) -> None:
        super().__init__(text)
        self._value = value
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def __lt__(self, other) -> bool:
        if isinstance(other, _NumItem):
            return self._value < other._value
        return super().__lt__(other)


def _alive(widget) -> bool:
    return widget is not None and not sip.isdeleted(widget)


def detail_text(item: P.Item) -> str:
    """The detail panel's content -- plain, so it is testable and copyable."""
    lines = [item.name, "", f"Source:      {item.source} ({item.scope})",
             f"Location:    {item.location}", f"Command:     {item.command}",
             f"Executable:  {item.exe or '(could not be worked out)'}"
             + ("" if item.exists is not False else "   [MISSING]"),
             f"State:       {P.state_text(item)}", f"Signature:   {P.trust_text(item)}"
             + (f" - {item.trust_reason}" if item.trust_reason else ""),
             f"Publisher:   {item.publisher or 'not stated in the file'}"]
    if item.extra:
        lines.append(f"Details:     {item.extra}")
    if item.first_seen:
        stamp = "at this tool's first scan (baseline)" if item.baseline else f"{item.first_seen:%Y-%m-%d %H:%M}"
        lines.append(f"First seen:  {stamp}")
    if item.impact_ms:
        lines.append(f"Boot impact: up to {item.impact_ms / 1000:.1f} s in {item.impact_count} slow-boot "
                     "event(s) recorded by Windows (Diagnostics-Performance 101/103)")
    lines.append("")
    if item.notes:
        lines.append("Findings:")
        lines += [f"  [{n.severity}] {n.text}" for n in item.notes]
    else:
        lines.append("Findings: none.")
    reason = P.toggle_block_reason(item)
    lines += ["", "Can be switched here." if reason is None else reason]
    return "\n".join(lines)


class _HistoryDialog(QDialog):
    def __init__(self, entries: List[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Startup change history")
        self.resize(640, 360)
        layout = QVBoxLayout(self)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText("\n".join(
            f"{e.get('when', '?')}  {e.get('action', '?')}  {e.get('name', '?')} "
            f"({e.get('source', '')})  -> {e.get('result', '')}" for e in entries)
            or "No changes have been made from this tool yet.")
        layout.addWidget(box)


class UnifiedStartupTab(QWidget):
    def __init__(self, history_path: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self._history_path = history_path
        self._inventory = P.Inventory()
        self._chip = "All"
        self._worker: Optional[COMWorker] = None
        self._loaded = False
        self._visible: List[P.Item] = []
        self._build()

    # ---- layout --------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.addLayout(self._toolbar())
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search name, command, path, publisher, source...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._render)
        layout.addWidget(self._search)
        self._chip_row = QHBoxLayout()
        self._chip_group = QButtonGroup(self)
        self._chips = {}
        for chip in P.CHIPS:
            button = QPushButton(chip)
            button.setCheckable(True)
            button.setChecked(chip == "All")
            self._chip_group.addButton(button)
            self._chip_row.addWidget(button)
            self._chips[chip] = button
            button.clicked.connect(lambda _c=False, c=chip: self._set_chip(c))
        self._chip_row.addStretch()
        layout.addLayout(self._chip_row)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self._make_table())
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._detail.setFont(mono)
        split.addWidget(self._detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        self._problems = QLabel("")
        self._problems.setWordWrap(True)
        set_role(self._problems, "muted")
        layout.addWidget(self._problems)

    def _toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._enable_btn = QPushButton("Enable")
        self._disable_btn = QPushButton("Disable")
        self._open_btn = QPushButton("Open Location")
        copy_btn = QPushButton("Copy Summary")
        csv_btn = QPushButton("Export CSV")
        history_btn = QPushButton("Change History")
        for widget in (self._refresh_btn, self._enable_btn, self._disable_btn, self._open_btn,
                       copy_btn, csv_btn, history_btn):
            bar.addWidget(widget)
        bar.addStretch()
        self._status = QLabel("")
        bar.addWidget(self._status)
        self._enable_btn.setEnabled(False)
        self._disable_btn.setEnabled(False)
        self._open_btn.setEnabled(False)
        self._refresh_btn.clicked.connect(self._load)
        self._enable_btn.clicked.connect(lambda: self._toggle(True))
        self._disable_btn.clicked.connect(lambda: self._toggle(False))
        self._open_btn.clicked.connect(self._open_location)
        copy_btn.clicked.connect(self._copy_summary)
        csv_btn.clicked.connect(self._export_csv)
        history_btn.clicked.connect(self._show_history)
        return bar

    def _make_table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMNS))
        table.setHorizontalHeaderLabels(COLUMNS)
        header = table.horizontalHeader()
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(0, 220)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        center_header(table)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSortingEnabled(True)
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)   # keep the flagged-first order until a header is clicked
        table.itemSelectionChanged.connect(self._selection_changed)
        table.itemDoubleClicked.connect(lambda _i: self._open_location())
        self._table = table
        return table

    # ---- loading -------------------------------------------------------

    def auto_scan(self) -> None:
        if not self._loaded:
            self._load()

    def _load(self) -> None:
        if self._worker is not None:
            return
        self._loaded = True
        self._refresh_btn.setEnabled(False)
        self._status.setText("Scanning...")
        self._progress.show()
        path = self._history_path

        def scan(_worker):
            from modules.boot_analyzer import boot_history
            facts = boot_history.read_boot_facts(boots=1, slow_events=300)
            inv = P.collect(path, facts.slow)
            inv.problems.extend(facts.problems)
            return inv

        worker = COMWorker(scan)
        worker.signals.result.connect(lambda inv: self._on_result(inv, worker))
        worker.signals.error.connect(lambda err: self._on_error(err, worker))
        self._worker = worker
        from PyQt6.QtCore import QThreadPool
        QThreadPool.globalInstance().start(worker)

    def _finish(self, worker) -> bool:
        if self._worker is not worker or not _alive(self):
            return False
        self._worker = None
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        return True

    def _on_result(self, inv, worker) -> None:
        if not self._finish(worker):
            return
        self._inventory = inv
        self._render()
        self._problems.setText("Not read: " + "; ".join(inv.problems) if inv.problems else "")

    def _on_error(self, err, worker) -> None:
        if not self._finish(worker):
            return
        logger.warning("Startup scan failed: %s", err)
        self._status.setText(f"Scan failed: {err}")

    def _cancel_all(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
            # A cancelled worker emits neither result nor error, so put the
            # controls back here or Refresh stays disabled for good.
            self._loaded = False
            self._refresh_btn.setEnabled(True)
            self._progress.hide()

    # ---- rendering -----------------------------------------------------

    def _set_chip(self, chip: str) -> None:
        self._chip = chip
        self._render()

    def _render(self, *_args) -> None:
        items = self._inventory.items
        counts = P.chip_counts(items)
        for chip, button in self._chips.items():
            button.setText(f"{chip} ({counts[chip]})" if items else chip)
        text = self._search.text().strip()
        self._visible = sorted(
            (i for i in items if P.matches_chip(i, self._chip) and P.matches_search(i, text)),
            key=lambda i: (not i.flagged, -(i.impact_ms or 0), i.name.lower()))
        table = self._table
        table.setSortingEnabled(False)
        table.setRowCount(len(self._visible))
        for row, item in enumerate(self._visible):
            self._fill_row(row, item)
        table.setSortingEnabled(True)
        self._status.setText(f"{len(self._visible)} of {len(items)} entries")
        self._selection_changed()

    def _fill_row(self, row: int, item: P.Item) -> None:
        table = self._table
        name = centered_item(item.name)
        name.setData(Qt.ItemDataRole.UserRole, id(item))
        state = centered_item(P.state_text(item))
        if item.enabled is False:
            state.setForeground(QBrush(QColor(semantic("error"))))
        impact = _NumItem(f"{item.impact_ms / 1000:.1f} s" if item.impact_ms else "", item.impact_ms or 0)
        seen = ""
        if item.first_seen:
            seen = "first scan" if item.baseline else f"{item.first_seen:%Y-%m-%d}"
        findings = centered_item("; ".join(n.code for n in item.notes) if item.notes else "")
        findings.setToolTip("\n".join(n.text for n in item.notes))
        if item.flagged:
            findings.setForeground(QBrush(QColor(semantic("warning"))))
        for column, cell in enumerate((name, state, centered_item(item.source), centered_item(item.scope),
                                       centered_item(item.publisher or ""), centered_item(P.trust_text(item)),
                                       impact, centered_item(seen), findings)):
            table.setItem(row, column, cell)

    def _selected(self) -> Optional[P.Item]:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        cell = self._table.item(rows[0].row(), 0)
        ident = cell.data(Qt.ItemDataRole.UserRole) if cell else None
        return next((i for i in self._visible if id(i) == ident), None)

    def _selection_changed(self) -> None:
        item = self._selected()
        self._detail.setPlainText(detail_text(item) if item else "Select an entry to see where it comes from, "
                                  "whether its file is signed, and why it was flagged.")
        can = item is not None and P.toggle_block_reason(item) is None
        self._enable_btn.setEnabled(bool(can and item.enabled is False))
        self._disable_btn.setEnabled(bool(can and item.enabled is not False))
        self._open_btn.setEnabled(bool(item and item.exe))

    # ---- actions -------------------------------------------------------

    def _toggle(self, enable: bool) -> None:
        item = self._selected()
        if item is None:
            return
        verb = "Enable" if enable else "Disable"
        if not confirm_destructive(self, f"{verb} startup item",
                                   f"{verb} '{item.name}'?",
                                   detail=f"{item.command}\n\nThis flips the same switch Task Manager uses. "
                                          "You can reverse it here at any time.",
                                   irreversible=False):
            return
        result = P.set_enabled(item, enable)
        P.record_change(self._history_path, {
            "when": datetime.now().isoformat(timespec="seconds"), "action": verb.lower(),
            "name": item.name, "source": f"{item.source} ({item.scope})",
            "result": ("ok" if result.ok else "FAILED") + f": {result.message}"})
        if result.enabled_after is not None:
            item.enabled = result.enabled_after
        self._render()
        self._status.setText(f"{verb} {item.name}: {result.message}")
        if not result.ok:
            QMessageBox.warning(self, f"{verb} failed", result.message)

    def _open_location(self) -> None:
        item = self._selected()
        if item is None or not item.exe:
            return
        folder = item.exe if os.path.isdir(item.exe) else os.path.dirname(item.exe)
        if os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            self._status.setText(f"Folder does not exist: {folder}")

    def _copy_summary(self) -> None:
        QGuiApplication.clipboard().setText(P.summary(self._inventory, self._visible))
        self._status.setText("Summary copied.")

    def _export_csv(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "Export startup entries", "startup.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                handle.write(P.to_csv(self._visible))
        except OSError as error:
            logger.warning("CSV export failed: %s", error)
            QMessageBox.warning(self, "Export failed", str(error))
            return
        self._status.setText(f"Exported {len(self._visible)} rows.")

    def _show_history(self) -> None:
        _HistoryDialog(P.read_changes(self._history_path), self).exec()
