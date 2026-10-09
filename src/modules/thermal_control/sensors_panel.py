"""The Sensors tab: every reading, filtered, searched, sorted -- and updated in place.

Rows are kept by sensor id and their cells rewritten each tick, never
rebuilt: the first version cleared the tree every second, which threw away
the sort, the scroll position and any selected row while you were reading it.
"""
import logging
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QLineEdit, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from ui.chips import make_chips, set_chip_counts

from .engine import view
from .engine.model import CONTROL, TEMPERATURE, Sensor

logger = logging.getLogger(__name__)

_SORT = Qt.ItemDataRole.UserRole
COLUMNS = ("Sensor", "Hardware", "Now", "Min", "Max", "Avg", "Limits")
C_NAME, C_HW, C_NOW, C_MIN, C_MAX, C_AVG, C_LIM = range(len(COLUMNS))


class _Item(QTreeWidgetItem):
    """Sorts numeric columns on the number (so 9 RPM is not above 1,200), the
    rest case-insensitively. A reading we do not have sorts first."""

    def __lt__(self, other) -> bool:
        tree = self.treeWidget()
        col = tree.sortColumn() if tree is not None else 0
        a, b = self.data(col, _SORT), other.data(col, _SORT)
        if isinstance(a, (int, float)) or isinstance(b, (int, float)):
            return (a if isinstance(a, (int, float)) else float("-inf")) < \
                   (b if isinstance(b, (int, float)) else float("-inf"))
        return self.text(col).casefold() < other.text(col).casefold()


class SensorsPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sensors: List[Sensor] = []
        self._stats: Dict[str, Tuple[float, float, float, int]] = {}   # id -> min, max, sum, n
        self._rows: Dict[str, _Item] = {}
        self._groups: Dict[str, _Item] = {}
        self._filter = "all"
        self._build()

    # ---- layout ---------------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        chips_row, self._chips = make_chips(self, view.SENSOR_FILTERS, self._pick)
        layout.addLayout(chips_row)
        bar = QHBoxLayout()
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search sensors or hardware…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self._apply_visibility())
        self._grouped = QCheckBox("Group by chip", self)
        self._grouped.setChecked(True)
        self._grouped.toggled.connect(lambda _on: self._rebuild())
        reset = QPushButton("Reset min/max", self)
        reset.clicked.connect(self._reset_stats)
        export = QPushButton("Export CSV…", self)
        export.clicked.connect(self._export)
        bar.addWidget(self._search, 1)
        bar.addWidget(self._grouped)
        bar.addWidget(reset)
        bar.addWidget(export)
        layout.addLayout(bar)
        tree = QTreeWidget(self)
        tree.setHeaderLabels(list(COLUMNS))
        tree.header().setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        tree.setSortingEnabled(True)
        tree.sortByColumn(C_NAME, Qt.SortOrder.AscendingOrder)
        tree.setColumnHidden(C_HW, True)
        self._tree = tree
        layout.addWidget(tree, 1)

    # ---- data -------------------------------------------------------------------------

    def update_sensors(self, sensors: List[Sensor]) -> None:
        self._sensors = sensors
        for s in sensors:
            if s.value is None:
                continue
            lo, hi, total, n = self._stats.get(s.id, (s.value, s.value, 0.0, 0))
            self._stats[s.id] = (min(lo, s.value), max(hi, s.value), total + s.value, n + 1)
        self._populate()
        set_chip_counts(self._chips, view.sensor_counts(sensors))

    def visible_rows(self) -> List[List[str]]:
        """What is on screen, in on-screen order: [hardware, sensor, now, min, max, avg]."""
        out = []

        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if child.isHidden():
                    continue
                if child.childCount():
                    walk(child)
                else:
                    out.append([child.text(C_HW) or item.text(C_NAME), child.text(C_NAME), child.text(C_NOW),
                                child.text(C_MIN), child.text(C_MAX), child.text(C_AVG)])
        walk(self._tree.invisibleRootItem())
        return out

    def _export(self) -> None:
        import csv
        import os
        from datetime import datetime
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export sensors", f"sensors-{datetime.now():%Y%m%d-%H%M%S}.csv", "CSV (*.csv)")
        if not path:
            return
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Hardware", "Sensor", "Now", "Min", "Max", "Avg"])
                writer.writerows(self.visible_rows())
            os.replace(tmp, path)
        except OSError as e:
            logger.warning("sensor export failed: %s", e)
            self._search.setPlaceholderText(f"Export failed: {e}")

    def _reset_stats(self) -> None:
        self._stats.clear()
        self.update_sensors(self._sensors)

    def _pick(self, key: str) -> None:
        self._filter = key
        self._apply_visibility()

    def _rebuild(self) -> None:
        self._tree.clear()
        self._rows.clear()
        self._groups.clear()
        self._tree.setColumnHidden(C_HW, self._grouped.isChecked())
        if not self._grouped.isChecked():
            self._tree.setColumnWidth(C_HW, 230)     # "Samsung SSD 990 PRO 2TB" fits
        self._tree.setRootIsDecorated(self._grouped.isChecked())
        self._populate()

    def _populate(self) -> None:
        limits = view.limits_by_hardware(self._sensors)
        sorting = self._tree.isSortingEnabled()
        self._tree.setSortingEnabled(False)          # no re-sort per cell while filling
        for s in self._sensors:
            if view.is_limit(s):
                continue
            item = self._rows.get(s.id) or self._new_row(s, limits)
            self._fill_row(item, s, limits.get(s.hardware, {}))
        self._tree.setSortingEnabled(sorting)
        self._apply_visibility()

    def _new_row(self, s: Sensor, limits) -> _Item:
        if self._grouped.isChecked():
            group = self._groups.get(s.hardware)
            if group is None:
                group = _Item(self._tree)
                lim = limits.get(s.hardware, {})
                group.setText(C_NAME, s.hardware)
                group.setText(C_LIM, ", ".join(f"{k} {v:.0f} °C" for k, v in lim.items()))
                group.setExpanded(True)
                self._groups[s.hardware] = group
            item = _Item(group)
        else:
            item = _Item(self._tree)
        self._rows[s.id] = item
        return item

    def _fill_row(self, item: _Item, s: Sensor, lim: Dict[str, float]) -> None:
        lo, hi, total, n = self._stats.get(s.id, (None, None, 0.0, 0))
        avg = total / n if n else None
        item.setText(C_NAME, f"{s.name} (duty)" if s.kind == CONTROL else s.name)
        item.setText(C_HW, s.hardware)
        item.setData(C_HW, _SORT, None)
        for col, value in ((C_NOW, s.value), (C_MIN, lo), (C_MAX, hi), (C_AVG, avg)):
            item.setText(col, _fmt(s, value) if col != C_NOW else s.display())
            item.setData(col, _SORT, value)
        item.setText(C_LIM, ", ".join(f"{k} {v:.0f} °C" for k, v in lim.items())
                     if s.kind == TEMPERATURE and not self._grouped.isChecked() else "")
        colour = _colour(s, lim)
        item.setForeground(C_NOW, colour if colour is not None else self.palette().text().color())

    def _apply_visibility(self) -> None:
        limits = view.limits_by_hardware(self._sensors)
        text = self._search.text()
        by_id = {s.id: s for s in self._sensors}
        for sid, item in self._rows.items():
            s = by_id.get(sid)
            item.setHidden(s is None or not view.sensor_visible(s, self._filter, text, limits))
        for group in self._groups.values():
            visible = sum(1 for i in range(group.childCount()) if not group.child(i).isHidden())
            group.setHidden(visible == 0)
            group.setText(C_NOW, f"{visible} shown")


def _fmt(s: Sensor, value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.1f}" if s.kind == TEMPERATURE else f"{value:,.0f}"


def _colour(s: Sensor, lim: Dict[str, float]) -> Optional[QColor]:
    if s.kind != TEMPERATURE or s.value is None:
        return None
    if "crit" in lim and s.value >= lim["crit"]:
        return QColor(semantic("error"))
    if s.value >= lim.get("warn", view.HOT_C):
        return QColor(semantic("warning"))
    return None
