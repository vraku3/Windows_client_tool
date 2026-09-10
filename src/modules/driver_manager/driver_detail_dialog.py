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

Final-review finding I4: `driver_store_size()` walks a real folder tree on
disk and can be GB-scale (see its own docstring), so it must not run
synchronously in `__init__` -- the dialog now shows "Calculating..." for
that one row and fills it in from a background `Worker` once the walk
finishes, guarded by `widget_is_valid` so a result landing after the
dialog is closed (Close button / Esc / window X) is a no-op.
"""
from typing import List, Optional

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QDialogButtonBox,
    QTextEdit,
)

from core.types import LogEntry
from core.widget_life import widget_is_valid
from core.worker import Worker
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
        self._workers: list = []

        for label, value in [
            ("Device Name", driver.device_name),
            ("Class", driver.driver_class),
            ("Version", driver.version or "Unknown"),
            ("Date", driver.date or "Unknown"),
            ("Publisher", driver.publisher or "Unknown"),
            ("Signed", "Yes" if driver.signed else "No"),
            ("WHQL Certified",
             "N/A" if not driver.inf_name
             else ("Yes" if driver.whql_certified else "No")),
            ("Error Code", str(driver.error_code) if driver.error_code else "None"),
            ("Hardware ID", driver.hardware_id or "Unknown"),
            ("INF Name", driver.inf_name or "Unknown"),
        ]:
            layout.addWidget(_row(label, value))

        # driver_store_size() walks a real folder on disk -- for a
        # GB-scale package (see its own docstring) that hangs the dialog
        # open if run synchronously. Built inline rather than via _row()
        # so the value QLabel can be reached back out of the background
        # callback -- matches _row()'s own visual construction exactly.
        size_row = QWidget()
        size_h = QHBoxLayout(size_row)
        size_h.setContentsMargins(0, 0, 0, 0)
        size_label = QLabel("<b>Driver Store Size:</b>")
        size_label.setFixedWidth(140)
        self._size_lbl = QLabel("Calculating…")
        self._size_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._size_lbl.setWordWrap(True)
        size_h.addWidget(size_label)
        size_h.addWidget(self._size_lbl, 1)
        layout.addWidget(size_row)

        layout.addWidget(_row("Flags", driver.flags or "None"))

        size_worker = Worker(lambda _w: driver_store_size(driver))
        size_worker.signals.result.connect(self._on_size_computed)
        size_worker.signals.error.connect(self._on_size_error)
        self._workers.append(size_worker)
        QThreadPool.globalInstance().start(size_worker)

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

    def _on_size_computed(self, size: Optional[int]) -> None:
        # The dialog (Close button, Esc, or the window's own X) can be
        # gone before the store walk finishes -- a stale result must not
        # touch a destroyed QLabel.
        if not widget_is_valid(self):
            return
        self._size_lbl.setText(f"{size:,} bytes" if size is not None else "Unknown")

    def _on_size_error(self, _err_str: str) -> None:
        # Without this, an exception in driver_store_size() left the row
        # reading "Calculating…" forever with no way to notice anything
        # went wrong -- see _on_size_computed's guard for why the row can
        # legitimately be gone by the time this fires too.
        if not widget_is_valid(self):
            return
        self._size_lbl.setText("Unknown")

    def reject(self) -> None:
        # Cancelling here is about the `widget_is_valid` guard above
        # skipping a result that arrives after this point, not about
        # interrupting the filesystem walk itself -- driver_store_size()
        # is a single blocking call with no loop to check is_cancelled
        # inside.
        for worker in self._workers:
            worker.cancel()
        super().reject()
