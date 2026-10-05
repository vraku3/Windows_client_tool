"""Drive list, scan overview bar, and status bar (spec 5.6, 5.7, 5.9).

All three are individually toggleable from the View tab, as in Pro, so each is
a self-contained widget that the shell shows or hides.
"""
import ctypes
import string
from ctypes import wintypes

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFontMetrics
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton,
    QTreeWidget, QTreeWidgetItem, QWidget,
)

from .directory_tree import ProportionBarDelegate
from .formatting import Unit, format_bytes, format_count
from .tree_model import BarFractionRole

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetLogicalDrives.restype = wintypes.DWORD
_kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
DRIVE_FIXED = 3
DRIVE_REMOVABLE = 2
DRIVE_REMOTE = 4


def drive_space(letter: str) -> tuple[int, int]:
    """(total, free) bytes for a drive, or (0, 0) if it cannot be queried."""
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    ok = _kernel32.GetDiskFreeSpaceExW(f"{letter}:\\", ctypes.byref(free),
                                       ctypes.byref(total), None)
    if not ok:
        return 0, 0
    return total.value, free.value


def list_drives() -> list[tuple[str, int, int]]:
    """(letter, total, free) for every ready fixed, removable or network drive.

    A drive with no media reports zero total; it is skipped rather than shown
    as a 0-byte volume, which is what Pro does.
    """
    mask = _kernel32.GetLogicalDrives()
    out = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not (mask >> i) & 1:
            continue
        if _kernel32.GetDriveTypeW(f"{letter}:\\") not in (
                DRIVE_FIXED, DRIVE_REMOVABLE, DRIVE_REMOTE):
            continue
        total, free = drive_space(letter)
        if total:
            out.append((letter, total, free))
    return out


class DriveList(QTreeWidget):
    """Bottom-left panel: Name, Total Size, Free, % Free with a bar."""

    drive_activated = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["Name", "Total Size", "Free", "% Free"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Name absorbs the slack; the three value columns take exactly what
        # their contents need. They used to be a fixed 100px each, which
        # overflowed any panel narrower than ~500px -- and the splitter gives
        # this one about 375-460px -- so the last column was cut and "93.6%"
        # rendered as "93.6'".
        # ...and the last section must NOT also stretch: that flag outranks
        # ResizeToContents and pinned `% Free` back to 100px, pushing the row
        # past the viewport again.
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        # The same delegate the tree uses, so a drive's free space reads the
        # way every other proportional value in the pane reads (spec 5.6).
        self.setItemDelegateForColumn(3, ProportionBarDelegate(self))
        self.itemDoubleClicked.connect(self._on_double_clicked)

    def refresh(self, drives=None) -> None:
        self.clear()
        for letter, total, free in (list_drives() if drives is None else drives):
            percent_free = (free * 100.0 / total) if total else 0.0
            item = QTreeWidgetItem([
                f"{letter}:",
                format_bytes(total),
                format_bytes(free),
                f"{percent_free:.1f}%",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, letter)
            # A drive that reports no total is one that would not answer --
            # a full bar would claim it is entirely free, which is a guess.
            item.setData(3, BarFractionRole,
                         (percent_free / 100.0) if total else 0.0)
            for column in (1, 2, 3):
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            self.addTopLevelItem(item)

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        letter = item.data(0, Qt.ItemDataRole.UserRole)
        if letter:
            self.drive_activated.emit(f"{letter}:\\")


class ScanOverview(QWidget):
    """The bar above the panes: facts about the current selection (spec 5.7)."""

    FIELDS = ("Size", "Allocated", "Files", "Folders",
              "Last Modified", "Last Accessed", "Owner")

    #: Right-click chooses between these two, as Pro does. Truncate is the
    #: default because the bar is one row high; wrapping it silently steals
    #: vertical space from the panes below.
    WRAP = "wrap"
    TRUNCATE = "truncate"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(16)
        self._labels: dict[str, QLabel] = {}
        self._full: dict[str, str] = {}
        self._display_mode = self.TRUNCATE
        for field in self.FIELDS:
            label = QLabel(f"{field}: —")
            label.setObjectName("scanOverviewField")
            self._labels[field] = label
            self._full[field] = f"{field}: —"
            layout.addWidget(label)
        layout.addStretch(1)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    # -- display mode ----------------------------------------------------

    def display_mode(self) -> str:
        return self._display_mode

    def set_display_mode(self, mode: str) -> None:
        if mode not in (self.WRAP, self.TRUNCATE):
            return
        self._display_mode = mode
        self._apply_display()

    def full_text(self, field: str) -> str:
        """The untruncated text. Elision is a display choice, and the value
        has to survive it -- otherwise switching back to Wrap shows the
        ellipsis the previous mode baked in."""
        return self._full.get(field, "")

    def build_context_menu(self) -> QMenu:
        menu = QMenu(self)
        for label, mode in (("Wrap", self.WRAP), ("Truncate", self.TRUNCATE)):
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(self._display_mode == mode)
            action.triggered.connect(
                lambda _checked=False, m=mode: self.set_display_mode(m))
            menu.addAction(action)
        return menu

    def contextMenuEvent(self, event) -> None:
        # Handled, so accepted rather than chained: QWidget's default
        # ignores the event, which would let it through to a parent that
        # would show a second menu.
        self.build_context_menu().exec(event.globalPos())
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._display_mode == self.TRUNCATE:
            self._apply_display()

    def _apply_display(self) -> None:
        wrapping = self._display_mode == self.WRAP
        for field, label in self._labels.items():
            label.setWordWrap(wrapping)
            text = self._full[field]
            if wrapping or label.width() <= 0:
                label.setText(text)
                continue
            metrics = QFontMetrics(label.font())
            label.setText(metrics.elidedText(
                text, Qt.TextElideMode.ElideRight, label.width()))

    def _set_field(self, field: str, text: str) -> None:
        self._full[field] = text
        self._labels[field].setText(text)

    def clear(self) -> None:
        for field in self._labels:
            self._set_field(field, f"{field}: —")
        self._apply_display()

    def show_node(self, store, node: int, unit: Unit = Unit.AUTO,
                  decimals: int = 1) -> None:
        if store is None or not (0 <= node < len(store)):
            self.clear()
            return
        values = {
            "Size": format_bytes(store.size[node], unit, decimals),
            "Allocated": format_bytes(store.alloc[node], unit, decimals),
            "Files": format_count(store.file_count[node]),
            "Folders": format_count(store.folder_count[node]),
            "Last Modified": format_filetime(store.mtime[node]),
            "Last Accessed": format_filetime(store.atime[node]),
            "Owner": store.owner(store.owner_id[node]) or "—",
        }
        for field, text in values.items():
            self._set_field(field, f"{field}: {text}")
        self._apply_display()


def format_filetime(value: int) -> str:
    """A Windows FILETIME as a local timestamp, or an em dash if unset."""
    if not value:
        return "—"
    import datetime
    seconds = value / 10_000_000 - 11_644_473_600
    try:
        return datetime.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "—"


class TreeSizeStatusBar(QWidget):
    """Free space, counts, cluster size, filters and scan errors (spec 5.9)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(20)
        self._free = QLabel()
        self._files = QLabel()
        self._excluded = QLabel()
        self._cluster = QLabel()
        self._notice = QLabel()
        self._notice.setObjectName("statusNotice")
        for widget in (self._free, self._files, self._excluded, self._cluster):
            layout.addWidget(widget)
        layout.addStretch(1)
        layout.addWidget(self._notice)
        self.clear()

    def clear(self) -> None:
        self._free.setText("Free Space: —")
        self._files.setText("0 Files")
        self._excluded.setText("0 Excluded")
        self._cluster.setText("—")
        self._notice.setText("")

    def show_notice(self, text: str) -> None:
        self._notice.setText(text)

    def show_result(self, result, drive_total: int = 0, drive_free: int = 0) -> None:
        if drive_total:
            self._free.setText(
                f"Free Space: {format_bytes(drive_free)} of {format_bytes(drive_total)}")
        self._files.setText(f"{format_count(result.node_count)} Files")
        self._excluded.setText(f"{format_count(result.excluded)} Excluded")
        if result.volume_info:
            self._cluster.setText(
                f"{result.volume_info.bytes_per_cluster:,} Bytes per Cluster (NTFS)")
        else:
            self._cluster.setText("—")
        self._notice.setText(self._notice_for(result))

    def _notice_for(self, result) -> str:
        """What Pro's status bar is for: say when the scan was degraded.

        An incomplete scan is the loudest thing here, because every total above
        it is then a lower bound rather than a measurement.
        """
        if not result.complete:
            first = result.errors[0][0] if result.errors else ""
            return (f"INCOMPLETE — {format_count(result.error_count)} location(s) "
                    f"unreadable{f': {first}' if first else ''}")
        if result.engine == "walk":
            return "Fast MFT scan unavailable — run elevated on an NTFS drive for exact allocated sizes"
        return ""


class ElevationBanner(QWidget):
    """Inline offer of the fast path when the session is not elevated (spec 9).

    The ribbon's "Start as administrator" button is on the Tools tab, which is
    not where anyone is looking while a slow walk scan grinds through a whole
    volume. The banner puts the same offer where the consequence is visible.

    An OFFER, not a warning: the module works perfectly well unelevated, it is
    only slower, so this carries the button that fixes it rather than telling
    the user what they could go and do themselves. Dismissal lasts the session.
    """

    elevation_requested = pyqtSignal()

    MESSAGE = ("Whole-drive scans are much faster when elevated — this session "
               "is using the folder-walk engine.")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("elevationBanner")
        self._dismissed = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 6, 3)
        layout.setSpacing(8)

        self._label = QLabel(self.MESSAGE, self)
        self._label.setObjectName("elevationBannerText")
        layout.addWidget(self._label)
        layout.addStretch(1)

        self.button = QPushButton("Start as administrator", self)
        self.button.setObjectName("elevationBannerButton")
        self.button.clicked.connect(self.elevation_requested)
        layout.addWidget(self.button)

        close = QPushButton("\u00d7", self)
        close.setObjectName("elevationBannerClose")
        close.setFixedWidth(22)
        close.setToolTip("Dismiss for this session")
        close.clicked.connect(self.dismiss)
        layout.addWidget(close)

        self.hide()

    def text(self) -> str:
        return self._label.text()

    def dismiss(self) -> None:
        self._dismissed = True
        self.hide()

    def set_elevated(self, elevated: bool) -> None:
        """Show the offer only when taking it up would change anything."""
        self.setVisible(not elevated and not self._dismissed)
