from __future__ import annotations
import logging
from typing import Dict, List

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt

from core.procengine.mitigations import TREE_COLUMNS, tree_cells
from modules.process_explorer.process_node import ProcessNode, link_children
from modules.process_explorer.color_scheme import (describe, get_row_color,
                                                   get_row_text_color)

logger = logging.getLogger(__name__)

# Column indices
COL_NAME  = 0
COL_PID   = 1
COL_CPU   = 2
COL_RAM   = 3
COL_DISK_R = 4
COL_DISK_W = 5
COL_NET_IN = 6
COL_NET_OUT = 7
COL_GPU   = 8
COL_USER  = 9
COL_PATH  = 10
# Security columns (hidden until chosen from the header's menu).
COL_PROTECTION = 11
COL_DEP   = 12
COL_ASLR  = 13
COL_CFG   = 14

COLUMNS = ["Name", "PID", "CPU%", "RAM", "Disk R", "Disk W",
           "Net In", "Net Out", "GPU%", "User", "Path"] + [h for _k, h in TREE_COLUMNS]

#: Column index -> key into `tree_cells`.
SECURITY_COLUMNS = {COL_PROTECTION: "protection", COL_DEP: "dep", COL_ASLR: "aslr", COL_CFG: "cfg"}

#: Sorts a security column: refused last, then by the short text.
def _security_key(node: ProcessNode, key: str) -> tuple:
    text = tree_cells(node.mitigations)[key][0]
    return (text in ("", "—"), text.lower())


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024**2:
        return f"{n/1024:.1f}K"
    if n < 1024**3:
        return f"{n/1024**2:.1f}M"
    return f"{n/1024**3:.1f}G"


def matches(node: ProcessNode, text: str) -> bool:
    """Whether `node` answers to the search `text` (already lower-cased).

    Name, pid, image path, user and command line -- the five things someone
    types when hunting a process. A path or command line we were refused
    is "" on the node, so it simply cannot match; it is never guessed.
    """
    if not text:
        return True
    if text.isdigit() and str(node.pid) == text:
        return True
    return any(text in (field or "").lower() for field in
               (node.name, node.exe, node.user, node.cmdline))


class ProcessTreeModel(QAbstractItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshot: Dict[int, ProcessNode] = {}
        self._roots: List[ProcessNode] = []
        self._flat_mode = False
        #: The toolbar's search text, lower-cased. Non-empty, the model
        #: lists the matching processes flat -- a match three levels down
        #: under a collapsed parent is a match nobody sees.
        self._filter = ""
        #: The rows shown when flat or filtered, kept rather than rebuilt
        #: from the dict on every `index()` call (Qt calls it per cell).
        self._flat: List[ProcessNode] = []

    # ── Public API ────────────────────────────────────────────────────

    def load_snapshot(self, snapshot: Dict[int, ProcessNode]):
        self.beginResetModel()
        self._snapshot = snapshot
        # Relinked every time: the children lists a node arrived with
        # describe the tick it was built on, not this one.
        self._roots = link_children(snapshot)
        self._rebuild_flat()
        self.endResetModel()

    def set_flat_mode(self, flat: bool):
        self.beginResetModel()
        self._flat_mode = flat
        self._rebuild_flat()
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        """Show only processes whose name, pid, path, user or command line
        contains `text` (case-insensitive). Empty restores the view."""
        text = (text or "").strip().lower()
        if text == self._filter:
            return
        self.beginResetModel()
        self._filter = text
        self._rebuild_flat()
        self.endResetModel()

    def visible_count(self) -> int:
        """How many processes the current filter lets through."""
        return len(self._flat) if self._is_flat() else len(self._snapshot)

    def _is_flat(self) -> bool:
        return self._flat_mode or bool(self._filter)

    def expandable_pids(self) -> List[int]:
        """Pids of every row that has children (and so can be expanded)."""
        if self._is_flat():
            return []
        return [pid for pid, node in self._snapshot.items() if node.children]

    def index_for_pid(self, pid: int) -> QModelIndex:
        """The column-0 index of `pid`'s row, or invalid if not shown."""
        node = self._snapshot.get(pid)
        if node is None:
            return QModelIndex()
        if self._is_flat():
            siblings = self._flat
        else:
            parent = self._snapshot.get(node.parent_pid)
            siblings = (parent.children if parent is not None
                        and parent is not node else self._roots)
        for row, candidate in enumerate(siblings):
            if candidate is node:
                return self.createIndex(row, 0, node)
        return QModelIndex()

    def _membership_moved(self) -> bool:
        shown = {id(node) for node in self._flat}
        wanted = {id(node) for node in self._snapshot.values()
                  if matches(node, self._filter)}
        return shown != wanted

    def _rebuild_flat(self) -> None:
        if not self._is_flat():
            self._flat = []
            return
        self._flat = [node for node in self._snapshot.values()
                      if matches(node, self._filter)]

    def update_nodes(self, changed: Dict[int, ProcessNode]):
        """Update metrics for changed pids and emit dataChanged."""
        for pid, new_node in changed.items():
            if pid not in self._snapshot:
                continue
            old = self._snapshot[pid]
            old.cpu_percent    = new_node.cpu_percent
            old.memory_rss     = new_node.memory_rss
            old.memory_vms     = new_node.memory_vms
            old.disk_read_bps  = new_node.disk_read_bps
            old.disk_write_bps = new_node.disk_write_bps
            old.net_send_bps   = new_node.net_send_bps
            old.net_recv_bps   = new_node.net_recv_bps
            old.gpu_percent    = new_node.gpu_percent
            old.status         = new_node.status
            # The row's colour comes from these, so they have to travel
            # with the metrics. is_new and is_deleted in particular are
            # the only fields here that CHANGE over a process's life --
            # left out, a green row never fades and a dead one never
            # reddens, which is the whole of the difference highlight.
            old.is_new         = new_node.is_new
            old.is_deleted     = new_node.is_deleted
            old.is_suspended   = new_node.is_suspended
            old.is_immersive   = new_node.is_immersive
            old.is_dotnet      = new_node.is_dotnet
            old.is_packed      = new_node.is_packed
            old.packed_entropy = new_node.packed_entropy
            old.is_own         = new_node.is_own
            old.is_service     = new_node.is_service
            # The cold details arrive over several ticks now (the pane
            # resolves 60 processes a tick), so a row's path, user and
            # command line are filled in LATER than the row itself.
            old.exe            = new_node.exe or old.exe
            old.user           = new_node.user or old.user
            old.cmdline        = new_node.cmdline or old.cmdline
            if new_node.appcontainer is not None:
                old.appcontainer = new_node.appcontainer

        if changed and self._filter and self._membership_moved():
            # A path or user that arrived this tick (the cold details fill
            # in over several) can make a row match, or stop matching.
            self.beginResetModel()
            self._rebuild_flat()
            self.endResetModel()
            return

        if changed:
            if self._is_flat():
                count = len(self._flat)
                if count > 0:
                    top_left = self.index(0, 0)
                    bot_right = self.index(count - 1, len(COLUMNS) - 1)
                    self.dataChanged.emit(top_left, bot_right)
            else:
                # Emit per-node so child rows are included, not just roots
                for pid in changed:
                    node = self._snapshot.get(pid)
                    if node is None:
                        continue
                    parent_node = self._snapshot.get(node.parent_pid)
                    siblings = (parent_node.children
                                if parent_node and parent_node.pid != node.pid
                                else self._roots)
                    try:
                        row = siblings.index(node)
                    except ValueError:
                        logger.debug("update_nodes: skipping an item that could not be read", exc_info=True)
                        continue
                    tl = self.createIndex(row, 0, node)
                    br = self.createIndex(row, len(COLUMNS) - 1, node)
                    self.dataChanged.emit(tl, br)

    # ── QAbstractItemModel required overrides ─────────────────────────

    def rowCount(self, parent: QModelIndex = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if self._is_flat():
            if not parent.isValid():
                return len(self._flat)
            return 0
        if not parent.isValid():
            return len(self._roots)
        node: ProcessNode = parent.internalPointer()
        return len(node.children)

    def columnCount(self, parent: QModelIndex = None) -> int:
        if parent is None:
            parent = QModelIndex()
        return len(COLUMNS)

    def index(self, row: int, col: int, parent: QModelIndex = None) -> QModelIndex:
        if parent is None:
            parent = QModelIndex()
        if self._is_flat():
            if not parent.isValid() and 0 <= row < len(self._flat):
                return self.createIndex(row, col, self._flat[row])
            return QModelIndex()

        if not parent.isValid():
            if 0 <= row < len(self._roots):
                return self.createIndex(row, col, self._roots[row])
        else:
            p_node: ProcessNode = parent.internalPointer()
            if 0 <= row < len(p_node.children):
                return self.createIndex(row, col, p_node.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid() or self._is_flat():
            return QModelIndex()
        node: ProcessNode = index.internalPointer()
        parent_node = self._snapshot.get(node.parent_pid)
        if parent_node is None or parent_node is node:
            return QModelIndex()
        grandparent = self._snapshot.get(parent_node.parent_pid)
        siblings = grandparent.children if grandparent else self._roots
        try:
            row = siblings.index(parent_node)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, 0, parent_node)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node: ProcessNode = index.internalPointer()
        col = index.column()

        if col in SECURITY_COLUMNS:
            cell = tree_cells(node.mitigations)[SECURITY_COLUMNS[col]]
            if role == Qt.ItemDataRole.DisplayRole:
                return cell[0]
            if role == Qt.ItemDataRole.ToolTipRole:
                return cell[1] or None
            if role == Qt.ItemDataRole.UserRole:
                return _security_key(node, SECURITY_COLUMNS[col])

        if role == Qt.ItemDataRole.DisplayRole:
            return [
                node.name, str(node.pid),
                f"{node.cpu_percent:.1f}", _fmt_bytes(node.memory_rss),
                _fmt_bytes(int(node.disk_read_bps)), _fmt_bytes(int(node.disk_write_bps)),
                _fmt_bytes(int(node.net_recv_bps)), _fmt_bytes(int(node.net_send_bps)),
                f"{node.gpu_percent:.1f}", node.user, node.exe,
            ][col]

        if role == Qt.ItemDataRole.ToolTipRole:
            # Every category the row qualifies for, not only the one its
            # colour shows. A row can be four things at once, and a tint
            # is a poor way to learn a fact you cannot look up.
            #
            # The Name column ALSO keeps the full path it showed before --
            # there was a second ToolTipRole branch further down doing
            # that, which a bare `return` here would have shadowed into
            # dead code.
            parts = [describe(node)]
            if col == COL_NAME and node.exe:
                parts.insert(0, node.exe)
            joined = "\n".join(part for part in parts if part)
            return joined or None

        if role == Qt.ItemDataRole.BackgroundRole:
            color = get_row_color(node)
            if color.alpha() > 0:
                return color
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            tcolor = get_row_text_color(node)
            if tcolor.alpha() > 0:
                return tcolor
            return None

        if role == Qt.ItemDataRole.UserRole:
            return [
                node.name.lower(),
                node.pid,
                node.cpu_percent,
                node.memory_rss,
                int(node.disk_read_bps),
                int(node.disk_write_bps),
                int(node.net_recv_bps),
                int(node.net_send_bps),
                node.gpu_percent,
                (node.user or "").lower(),
                (node.exe or "").lower(),
            ][col]

        if role == Qt.ItemDataRole.ToolTipRole and col == COL_NAME:
            return node.exe

        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self.layoutAboutToBeChanged.emit()
        reverse = order == Qt.SortOrder.DescendingOrder
        key_fns = {
            COL_NAME:    lambda n: n.name.lower(),
            COL_PID:     lambda n: n.pid,
            COL_CPU:     lambda n: n.cpu_percent,
            COL_RAM:     lambda n: n.memory_rss,
            COL_DISK_R:  lambda n: n.disk_read_bps,
            COL_DISK_W:  lambda n: n.disk_write_bps,
            COL_NET_IN:  lambda n: n.net_recv_bps,
            COL_NET_OUT: lambda n: n.net_send_bps,
            COL_GPU:     lambda n: n.gpu_percent,
            COL_USER:    lambda n: (n.user or "").lower(),
            COL_PATH:    lambda n: (n.exe or "").lower(),
        }
        for col, key in SECURITY_COLUMNS.items():
            key_fns[col] = lambda n, k=key: _security_key(n, k)
        key_fn = key_fns.get(column, lambda n: n.name.lower())

        def _sort_recursive(nodes: list) -> None:
            nodes.sort(key=key_fn, reverse=reverse)
            for node in nodes:
                if node.children:
                    _sort_recursive(node.children)

        _sort_recursive(self._roots)
        self.layoutChanged.emit()

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None
