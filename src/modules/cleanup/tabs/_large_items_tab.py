"""_LargeItemsTab — Large Items scan tab.

DISM Component Store Cleanup moved to System Health's Servicing tab
(`modules/system_health/system_health_module.py`) -- a servicing
operation, not a file to select-and-delete.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QLabel, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from modules.cleanup.tabs._disk_cleanup_panel import _DiskCleanupPanel
from modules.cleanup.tabs._driver_panel import _DriverStorePanel
from modules.cleanup.tabs._scan_tab import _ScanTab
from modules.cleanup import cleanup_scanner as cs


# Large-item scanners shared across overview and this tab
LARGE_SCANNERS = {
    cs.scan_windows_old:            ("Windows.old",            "caution"),
    cs.scan_installer_patch_cache:  ("Installer Patch Cache",  "danger"),
}


class _LargeItemsTab(QWidget):
    """Large Items scan tab, plus two removals Windows itself must perform.

    The scan tree sits on top; the driver-store and Disk Cleanup panels go in
    a scrollable "Removed by Windows tools" section below it, behind a
    vertical splitter, so neither squeezes the other at a small window size.
    """
    freed_bytes = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Main scan tab
        from modules.cleanup.cleanup_module import LARGE_EXTRA
        self._scan_tab = _ScanTab({**LARGE_SCANNERS, **LARGE_EXTRA})
        self._scan_tab.freed_bytes.connect(self.freed_bytes)

        tools = QFrame()
        tools.setObjectName("card")
        tools.setToolTip("Removals that Windows' own tools perform: the paths are "
                         "never deleted directly by this app.")
        tools_layout = QVBoxLayout(tools)
        tools_layout.setContentsMargins(8, 8, 8, 8)
        tools_layout.setSpacing(4)
        heading = QLabel("Removed by Windows tools")
        heading.setObjectName("heading")
        heading.setToolTip("These are not rows in the list above, on purpose: deleting "
                           "their folders directly would break Windows.")
        tools_layout.addWidget(heading)
        intro = QLabel("Superseded driver packages are removed with pnputil and Disk "
                       "Cleanup runs Windows' own cleanmgr: deleting their folders "
                       "directly would leave Windows believing they still exist.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        tools_layout.addWidget(intro)

        # Superseded driver packages get their own panel rather than a row
        # in the tree above: that tree is walked by delete_items, which
        # deletes PATHS, and removing a DriverStore\FileRepository folder
        # directly is how a machine loses a driver it still believes it
        # has. pnputil /delete-driver is the only correct removal.
        self._drivers = _DriverStorePanel()
        tools_layout.addWidget(self._drivers)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        tools_layout.addWidget(sep)

        # Windows' own Disk Cleanup, same reasoning as the driver panel
        # above: /sagerun deletes according to categories cleanmgr's own
        # dialog controls, which is not something a checkbox tree of
        # ScanItems can represent.
        self._disk_cleanup = _DiskCleanupPanel()
        tools_layout.addWidget(self._disk_cleanup)
        tools_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(tools)
        scroll.setAccessibleName("Removed by Windows tools")

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(self._scan_tab)
        self._splitter.addWidget(scroll)
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, True)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([520, 340])
        layout.addWidget(self._splitter, 1)

    def auto_scan(self):
        self._scan_tab.auto_scan()

    def _cancel_all(self) -> None:
        self._scan_tab._cancel_all()
        self._drivers._cancel_all()
        self._disk_cleanup._cancel_all()
