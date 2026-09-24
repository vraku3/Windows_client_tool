"""The individual devices behind one grouped Driver Manager row.

The main table folds every device that runs the same driver into a single
"AMD Processor  x32" row; this is where the 32 are. Double-click one to open
its own details, the same dialog a single ungrouped row opens.
"""
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from modules.driver_manager.driver_grouping import DriverGroup
from modules.driver_manager.driver_reader import DriverInfo

_HEADERS = ["Device Name", "Device ID", "Hardware ID", "Flags"]


class DriverInstancesDialog(QDialog):
    """Every device in a `DriverGroup`, resizable, one row each."""

    def __init__(self, group: DriverGroup, parent=None):
        super().__init__(parent)
        rep = group.representative
        self._group = group
        self.setWindowTitle(f"{rep.device_name} — {group.count} devices")
        self.resize(900, 460)
        self.setMinimumSize(520, 260)

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"<b>{group.count} devices</b> use the same driver: "
            f"{rep.driver_class or 'unknown class'}, version "
            f"{rep.version or 'unknown'}"
            f"{', ' + rep.publisher if rep.publisher else ''}. "
            "Double-click a device for its details.")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.table = QTableWidget(group.count, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for row, member in enumerate(group.members):
            for col, text in enumerate((member.device_name, member.device_id,
                                        member.hardware_id, member.flags)):
                item = QTableWidgetItem(text or "")
                item.setToolTip(text or "")
                self.table.setItem(row, col, item)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        row_layout = QHBoxLayout()
        row_layout.addStretch()
        row_layout.addWidget(buttons)
        layout.addLayout(row_layout)

    def member_at(self, row: int) -> Optional[DriverInfo]:
        if 0 <= row < self._group.count:
            return self._group.members[row]
        return None

    def _on_double_click(self, row: int, _column: int) -> None:
        member = self.member_at(row)
        if member is None:
            return
        from modules.driver_manager.driver_detail_dialog import DriverDetailDialog
        DriverDetailDialog(member, parent=self).exec()
