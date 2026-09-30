"""Renders `boot_history.BootFacts`: recent boots and the items that slowed them."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.semantic_colors import semantic
from core.table_ui import center_header, centered_item, set_role
from modules.boot_analyzer import boot_history as bh

BOOT_COLUMNS = ["Boot logged", "Type", "Boot time", "Main path", "Post-boot", "Startup apps", "Degraded"]
SLOW_COLUMNS = ["Kind", "Name", "Publisher", "Events", "Worst", "Average", "Last seen", "Path"]
FALLBACK_COLUMNS = ["Boot logged", "Time to ready (approx.)", "Prior shutdown"]


class _Num(QTableWidgetItem):
    def __init__(self, text: str, value: float) -> None:
        super().__init__(text)
        self._value = value
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def __lt__(self, other) -> bool:
        return self._value < other._value if isinstance(other, _Num) else super().__lt__(other)


def _table(columns, height: int) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    center_header(table)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSortingEnabled(True)
    header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)   # rows arrive newest / worst first
    table.setFixedHeight(height)
    return table


class BootHistoryPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._facts = bh.BootFacts()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Recent boots (from Windows' own boot traces)")
        set_role(title, "heading")
        layout.addWidget(title)
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        self._boots = _table(BOOT_COLUMNS, 240)
        layout.addWidget(self._boots)
        title2 = QLabel("What slowed those boots (apps, drivers and services Windows flagged)")
        set_role(title2, "heading")
        layout.addWidget(title2)
        self._slow = _table(SLOW_COLUMNS, 260)
        layout.addWidget(self._slow)

        # Only shown when the Performance log above was refused and the
        # System-log approximation (boot_history.read_fallback_boot_history)
        # found something instead -- hidden otherwise, never both at once.
        self._fallback_title = QLabel(
            "Approximate boot times (System log -- the detailed log above could not be read)"
        )
        set_role(self._fallback_title, "heading")
        self._fallback_title.hide()
        layout.addWidget(self._fallback_title)
        self._fallback_summary = QLabel("")
        self._fallback_summary.setWordWrap(True)
        self._fallback_summary.hide()
        layout.addWidget(self._fallback_summary)
        self._fallback = _table(FALLBACK_COLUMNS, 200)
        self._fallback.hide()
        layout.addWidget(self._fallback)
        row = QHBoxLayout()
        copy = QPushButton("Copy Boot Summary")
        copy.clicked.connect(self._copy)
        row.addWidget(copy)
        row.addStretch()
        layout.addLayout(row)
        self._problems = QLabel("")
        self._problems.setWordWrap(True)
        set_role(self._problems, "muted")
        layout.addWidget(self._problems)

    def set_facts(self, facts: bh.BootFacts) -> None:
        self._facts = facts
        self._summary.setText(bh.trend_note(facts.boots) + "\n" + bh.uptime_note(facts))
        self._fill_boots(facts)
        self._fill_slow(facts)
        self._fill_fallback(facts)
        self._problems.setText("Not read: " + "; ".join(facts.problems) if facts.problems else "")

    def _fill_boots(self, facts: bh.BootFacts) -> None:
        table = self._boots
        table.setSortingEnabled(False)
        table.setRowCount(len(facts.boots))
        slowest = max((b.boot_ms for b in facts.boots), default=0)
        for row, b in enumerate(facts.boots):
            cells = [_Num(f"{b.when:%Y-%m-%d %H:%M}", b.when.timestamp()),
                     centered_item(b.boot_type or "unknown"),
                     _Num(bh.fmt_ms(b.boot_ms), b.boot_ms), _Num(bh.fmt_ms(b.main_path_ms), b.main_path_ms),
                     _Num(bh.fmt_ms(b.post_boot_ms), b.post_boot_ms), _Num(str(b.startup_apps), b.startup_apps),
                     centered_item(f"yes (+{bh.fmt_ms(b.degradation_delta_ms)})" if b.degraded else "no")]
            if b.boot_ms == slowest:
                cells[2].setForeground(QBrush(QColor(semantic("warning"))))
            if b.degraded:
                cells[6].setForeground(QBrush(QColor(semantic("error"))))
            for column, cell in enumerate(cells):
                table.setItem(row, column, cell)
        table.setSortingEnabled(True)

    def _fill_fallback(self, facts: bh.BootFacts) -> None:
        records = facts.fallback_boots
        visible = bool(records)
        self._fallback_title.setVisible(visible)
        self._fallback_summary.setVisible(visible)
        self._fallback.setVisible(visible)
        if not visible:
            self._fallback.setRowCount(0)
            return
        self._fallback_summary.setText(bh.fallback_trend_note(records))
        table = self._fallback
        table.setSortingEnabled(False)
        table.setRowCount(len(records))
        for row, r in enumerate(records):
            duration_text = f"{r.duration_seconds:.0f}s" if r.duration_seconds is not None else "Unknown"
            duration_value = r.duration_seconds if r.duration_seconds is not None else -1.0
            if r.prior_shutdown_clean is None:
                shutdown_text = "Unknown"
            elif r.prior_shutdown_clean:
                shutdown_text = "Clean shutdown"
            else:
                shutdown_text = "Unexpected shutdown"
            cells = [
                _Num(f"{r.when:%Y-%m-%d %H:%M}", r.when.timestamp()),
                _Num(duration_text, duration_value),
                centered_item(shutdown_text),
            ]
            if r.prior_shutdown_clean is False:
                cells[2].setForeground(QBrush(QColor(semantic("warning"))))
            for column, cell in enumerate(cells):
                table.setItem(row, column, cell)
        table.setSortingEnabled(True)

    def _fill_slow(self, facts: bh.BootFacts) -> None:
        table = self._slow
        rows = bh.summarise_slow(facts.slow)
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row, s in enumerate(rows):
            cells = [centered_item(s.kind), centered_item(s.name), centered_item(s.company),
                     _Num(str(s.count), s.count), _Num(bh.fmt_ms(s.worst_ms), s.worst_ms),
                     _Num(bh.fmt_ms(s.avg_ms), s.avg_ms), _Num(f"{s.last_seen:%Y-%m-%d}", s.last_seen.timestamp()),
                     centered_item(s.path)]
            for column, cell in enumerate(cells):
                table.setItem(row, column, cell)
        table.setSortingEnabled(True)

    def _copy(self) -> None:
        lines = [bh.trend_note(self._facts.boots), bh.uptime_note(self._facts)]
        for s in bh.summarise_slow(self._facts.slow)[:10]:
            lines.append(f"- {s.kind} {s.name}: worst {bh.fmt_ms(s.worst_ms)}, {s.count} event(s)")
        QGuiApplication.clipboard().setText("\n".join(lines))
