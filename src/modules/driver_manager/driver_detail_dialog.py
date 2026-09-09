"""Per-device detail view for Driver Manager -- Task 10.

Reuses `process_explorer/properties_dialog.py`'s `_row(label, value)`
pattern (a bold fixed-width label plus a selectable value), the
established convention for a details view in this codebase. That method
is defined on `ProcessPropertiesDialog` rather than at module level, so
it is not imported directly; `_row` below is a byte-for-byte copy of its
body.

Does NOT consume `driver_reader.list_restore_points` -- restore points
are a system-wide, not a per-device, concept; they get their own
toolbar action and dialog in Task 12, not a slot in this per-device
view.
"""
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QDialogButtonBox,
    QTextEdit,
)

from core.types import LogEntry
from modules.driver_manager.driver_diagnostics import crashes_for, suggested_action
from modules.driver_manager.driver_reader import DriverInfo, driver_store_size


def _row(label: str, value: str) -> QWidget:
    # Matches process_explorer/properties_dialog.py's _row helper exactly
    # -- a bold fixed-width label plus a selectable value, the established
    # convention for a details view in this codebase.
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(f"<b>{label}:</b>")
    lbl.setFixedWidth(140)
    val = QLabel(value)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    val.setWordWrap(True)
    h.addWidget(lbl)
    h.addWidget(val, 1)
    return w


class DriverDetailDialog(QDialog):
    def __init__(self, driver: DriverInfo,
                reliability_records: Optional[List[LogEntry]] = None,
                parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Driver Details — {driver.device_name}")
        self.resize(560, 480)
        layout = QVBoxLayout(self)

        # driver_store_size() walks a real folder on disk -- called here,
        # lazily, only when this dialog actually opens, never for every
        # row on every refresh.
        size = driver_store_size(driver)
        size_text = f"{size:,} bytes" if size is not None else "Unknown"

        for label, value in [
            ("Device Name", driver.device_name),
            ("Class", driver.driver_class),
            ("Version", driver.version or "Unknown"),
            ("Date", driver.date or "Unknown"),
            ("Publisher", driver.publisher or "Unknown"),
            ("Signed", "Yes" if driver.signed else "No"),
            ("WHQL Certified", "Yes" if driver.whql_certified else "No"),
            ("Error Code", str(driver.error_code) if driver.error_code else "None"),
            ("Hardware ID", driver.hardware_id or "Unknown"),
            ("INF Name", driver.inf_name or "Unknown"),
            ("Driver Store Size", size_text),
            ("Flags", driver.flags or "None"),
        ]:
            layout.addWidget(_row(label, value))

        layout.addWidget(QLabel("<b>Recent Reliability Monitor entries "
                                "(approximate match by device name):</b>"))
        crashes_view = QTextEdit()
        crashes_view.setReadOnly(True)
        if reliability_records is None:
            # Nobody ever fetched Reliability Monitor data for this dialog
            # to search -- driver_module.py's call site always passes this
            # today (Task 10's own documented scope decision, see the
            # module docstring above). Rendering that as "No matching
            # entries found." reads as a completed, negative search when
            # no search ran at all.
            crashes_view.setPlainText(
                "Reliability Monitor data not loaded — open Diagnose ▸ "
                "Reliability.")
        else:
            crashes = crashes_for(driver.device_name, reliability_records)
            if crashes:
                crashes_view.setPlainText("\n".join(
                    f"{getattr(c, 'timestamp', '')}: {getattr(c, 'message', '')}"
                    for c in crashes))
            else:
                crashes_view.setPlainText("No matching entries found.")
        crashes_view.setMaximumHeight(100)
        layout.addWidget(crashes_view)

        if driver.error_code:
            layout.addWidget(_row("Suggested action", suggested_action(driver.error_code)))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
