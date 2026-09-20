"""_LargeItemsTab — Large Items scan tab.

DISM Component Store Cleanup moved to System Health's Servicing tab
(`modules/system_health/system_health_module.py`) -- a servicing
operation, not a file to select-and-delete.
"""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFrame,
)

from modules.cleanup.tabs._disk_cleanup_panel import _DiskCleanupPanel
from modules.cleanup.tabs._driver_panel import _DriverStorePanel
from modules.cleanup.tabs._scan_tab import _ScanTab
from modules.cleanup import cleanup_scanner as cs


# Large-item scanners shared across overview and this tab
LARGE_SCANNERS = {
    cs.scan_windows_old:            ("Windows.old",            "caution"),
    cs.scan_recycle_bin:            ("Recycle Bin",            "safe"),
    cs.scan_installer_patch_cache:  ("Installer Patch Cache",  "danger"),
}


class _LargeItemsTab(QWidget):
    """Large Items scan tab."""
    freed_bytes = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Main scan tab
        from modules.cleanup.cleanup_module import LARGE_EXTRA
        self._scan_tab = _ScanTab({**LARGE_SCANNERS, **LARGE_EXTRA})
        self._scan_tab.freed_bytes.connect(self.freed_bytes)
        layout.addWidget(self._scan_tab, 1)

        # Superseded driver packages get their own panel rather than a row
        # in the tree above: that tree is walked by delete_items, which
        # deletes PATHS, and removing a DriverStore\FileRepository folder
        # directly is how a machine loses a driver it still believes it
        # has. pnputil /delete-driver is the only correct removal.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #444;")
        layout.addWidget(sep)
        self._drivers = _DriverStorePanel()
        layout.addWidget(self._drivers)

        # Windows' own Disk Cleanup, same reasoning as the driver panel
        # above: /sagerun deletes according to categories cleanmgr's own
        # dialog controls, which is not something a checkbox tree of
        # ScanItems can represent.
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #444;")
        layout.addWidget(sep2)
        self._disk_cleanup = _DiskCleanupPanel()
        layout.addWidget(self._disk_cleanup)

    def auto_scan(self):
        self._scan_tab.auto_scan()

    def _cancel_all(self) -> None:
        self._scan_tab._cancel_all()
        self._drivers._cancel_all()
        self._disk_cleanup._cancel_all()
