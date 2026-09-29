# src/modules/registry_explorer/registry_model.py
import datetime
import logging
import winreg
from typing import Any, List, Optional, Tuple

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt

from modules.registry_explorer.registry_scan import filetime_to_datetime

logger = logging.getLogger(__name__)

_HIVES = {
    "HKEY_LOCAL_MACHINE":  winreg.HKEY_LOCAL_MACHINE,
    "HKEY_CURRENT_USER":   winreg.HKEY_CURRENT_USER,
    "HKEY_CLASSES_ROOT":   winreg.HKEY_CLASSES_ROOT,
    "HKEY_USERS":          winreg.HKEY_USERS,
    "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
}


class _Node:
    """Tree node representing one registry key."""

    def __init__(self, name: str, hive, path: str, parent: Optional["_Node"] = None):
        self.name = name
        self.hive = hive
        self.path = path           # full path from hive root, e.g. "SOFTWARE\Microsoft"
        self.parent = parent
        self._children: Optional[List["_Node"]] = None
        self._loaded = False

    def children(self) -> List["_Node"]:
        if self._children is None:
            self._children = []
            try:
                with winreg.OpenKey(self.hive, self.path,
                                    access=winreg.KEY_READ | winreg.KEY_ENUMERATE_SUB_KEYS) as k:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(k, i)
                            child_path = f"{self.path}\\{sub}" if self.path else sub
                            self._children.append(_Node(sub, self.hive, child_path, self))
                            i += 1
                        except OSError:
                            # OSError here is how winreg says the enumeration is
                            # finished. Control flow, not a failure.
                            logger.debug("children: registry enumeration finished", exc_info=True)
                            break
            except (OSError, PermissionError):
                logger.debug("Ignored (OSError, PermissionError)", exc_info=True)
        return self._children

    def has_children(self) -> bool:
        # Peek without full load: query key info
        try:
            with winreg.OpenKey(self.hive, self.path,
                                access=winreg.KEY_READ | winreg.KEY_ENUMERATE_SUB_KEYS) as k:
                count, _, _ = winreg.QueryInfoKey(k)
                return count > 0
        except (OSError, PermissionError):
            return False

    def row(self) -> int:
        if self.parent:
            return self.parent.children().index(self)
        return 0

    def last_write(self) -> Tuple[Optional[datetime.datetime], str]:
        """(when, "") in UTC, or (None, reason) if the key can't be opened.

        RegQueryInfoKey's lpftLastWriteTime is a real per-key fact regedit
        itself never shows while browsing -- only exposed here today inside
        registry_scan's search ("modified in the last N days"). Reusing it
        for the currently selected key answers "when was this last touched"
        without having to run a scan first.
        """
        try:
            with winreg.OpenKey(self.hive, self.path, access=winreg.KEY_READ) as k:
                _, _, ft = winreg.QueryInfoKey(k)
            return filetime_to_datetime(ft), ""
        except (OSError, PermissionError) as e:
            return None, str(e)


class RegistryTreeModel(QAbstractItemModel):
    """Lazy-loading read-only model for the 5 standard registry hives."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._roots: List[_Node] = [
            _Node(name, hive, "", None) for name, hive in _HIVES.items()
        ]

    def index(self, row: int, col: int, parent: QModelIndex = None) -> QModelIndex:
        if parent is None:
            parent = QModelIndex()
        if not self.hasIndex(row, col, parent):
            return QModelIndex()
        if not parent.isValid():
            node = self._roots[row]
        else:
            p_node: _Node = parent.internalPointer()
            kids = p_node.children()
            if row >= len(kids):
                return QModelIndex()
            node = kids[row]
        return self.createIndex(row, col, node)

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        node: _Node = index.internalPointer()
        if node.parent is None:
            return QModelIndex()
        p = node.parent
        if p.parent is None:
            row = self._roots.index(p)
        else:
            row = p.row()
        return self.createIndex(row, 0, p)

    def rowCount(self, parent: QModelIndex = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            return len(self._roots)
        node: _Node = parent.internalPointer()
        return len(node.children())

    def columnCount(self, parent: QModelIndex = None) -> int:
        if parent is None:
            parent = QModelIndex()
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        node: _Node = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return node.name
        if role == Qt.ItemDataRole.UserRole:
            return node  # expose node for value-panel loading
        return None

    def hasChildren(self, parent: QModelIndex = None) -> bool:
        if parent is None:
            parent = QModelIndex()
        if not parent.isValid():
            return bool(self._roots)
        node: _Node = parent.internalPointer()
        return node.has_children()

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return "Registry Key"
        return None

    def key_path(self, index: QModelIndex) -> str:
        """Return hive_name\\path string for copy/export."""
        if not index.isValid():
            return ""
        node: _Node = index.internalPointer()
        hive_name = next((n for n, h in _HIVES.items() if h == node.hive), "")
        return f"{hive_name}\\{node.path}" if node.path else hive_name

    def last_write_for(self, index: QModelIndex) -> Tuple[Optional[datetime.datetime], str]:
        """(when, "") in UTC, or (None, reason) -- see _Node.last_write."""
        if not index.isValid():
            return None, ""
        node: _Node = index.internalPointer()
        return node.last_write()

    def values_for(self, index: QModelIndex) -> List[tuple]:
        """Return list of (name, type_str, data) for the selected key."""
        if not index.isValid():
            return []
        node: _Node = index.internalPointer()
        results = []
        try:
            with winreg.OpenKey(node.hive, node.path, access=winreg.KEY_READ) as k:
                i = 0
                while True:
                    try:
                        name, data, kind = winreg.EnumValue(k, i)
                        results.append((name or "(Default)", _kind_str(kind), _fmt_data(data, kind)))
                        i += 1
                    except OSError:
                        # OSError here is how winreg says the enumeration is
                        # finished. Control flow, not a failure.
                        logger.debug("values_for: registry enumeration finished", exc_info=True)
                        break
        except (OSError, PermissionError):
            logger.debug("Ignored (OSError, PermissionError)", exc_info=True)
        return results


def _kind_str(kind: int) -> str:
    return {
        winreg.REG_SZ: "REG_SZ",
        winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ",
        winreg.REG_BINARY: "REG_BINARY",
        winreg.REG_DWORD: "REG_DWORD",
        winreg.REG_QWORD: "REG_QWORD",
        winreg.REG_MULTI_SZ: "REG_MULTI_SZ",
        winreg.REG_NONE: "REG_NONE",
    }.get(kind, f"REG_{kind}")


def _fmt_data(data: Any, kind: int) -> str:
    if kind == winreg.REG_BINARY:
        if isinstance(data, (bytes, bytearray)):
            return data.hex(" ").upper()
    if kind == winreg.REG_MULTI_SZ:
        if isinstance(data, list):
            return "\n".join(data)
    return str(data)
