"""Qt model adapter and paint-time semantic badges for Cleanup row facts."""

from dataclasses import replace

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from core.semantic_colors import chrome, semantic
from modules.cleanup.components.item_rows import (
    BADGES,
    FACTS_ROLE,
    SAFETY_ROLE,
    SHARE_ROLE,
    SIZE_ROLE,
    RowFacts,
)

HEADERS = ("Name", "Size", "Share", "Safety", "Verdict", "Note")
SAFETY_MEANINGS = {"safe": "success", "caution": "warning", "danger": "error"}


class ItemRowModel(QAbstractTableModel):
    """Flat adapter for presenters; no scanner/deleter calls or item mutation.

    Size uses SIZE_ROLE for proxies, and sort(1) compares actual bytes. Hosts
    retain control over selection policy and expansion of synthetic rows.
    """

    def __init__(self, rows=(), parent=None):
        super().__init__(parent)
        self.rows = list(rows)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section] if 0 <= section < len(HEADERS) else None
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        column = index.column()
        if role == FACTS_ROLE:
            return row
        if role == SIZE_ROLE:
            return row.size_bytes if column == 1 else self.data(index, Qt.ItemDataRole.DisplayRole)
        if role == SAFETY_ROLE:
            return row.safety
        if role == SHARE_ROLE:
            return row.share_percent
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.tooltip
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{row.label}, {row.size_text}, {row.badge_text}, {row.verdict}"
        if role == Qt.ItemDataRole.CheckStateRole and column == 0 and row.kind == "item":
            return Qt.CheckState.Checked if row.selected else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (1, 2):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                row.label,
                row.size_text,
                f"{row.share_percent:.1f}%",
                row.badge_text,
                row.verdict,
                " · ".join(row.chips),
            )[column]
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0 and self.rows[index.row()].kind == "item":
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if (
            not index.isValid()
            or index.column() != 0
            or role != Qt.ItemDataRole.CheckStateRole
            or self.rows[index.row()].kind != "item"
        ):
            return False
        selected = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        self.rows[index.row()] = replace(self.rows[index.row()], selected=selected)
        self.dataChanged.emit(index, index, [int(Qt.ItemDataRole.CheckStateRole), FACTS_ROLE])
        return True

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if not 0 <= column < len(HEADERS):
            return
        keys = (
            lambda r: r.label.casefold(),
            lambda r: r.size_bytes,
            lambda r: r.share_percent,
            lambda r: {"safe": 0, "caution": 1, "danger": 2}.get(r.safety, 2),
            lambda r: r.verdict.casefold(),
            lambda r: r.chips,
        )
        # Remap persistent indexes so selections keep referring to the same facts.
        self.layoutAboutToBeChanged.emit()
        old_indexes = self.persistentIndexList()
        order_rows = sorted(
            range(len(self.rows)),
            key=lambda i: keys[column](self.rows[i]),
            reverse=order == Qt.SortOrder.DescendingOrder,
        )
        positions = {old: new for new, old in enumerate(order_rows)}
        self.rows = [self.rows[i] for i in order_rows]
        self.changePersistentIndexList(
            old_indexes, [self.index(positions[i.row()], i.column()) for i in old_indexes]
        )
        self.layoutChanged.emit()


class SafetyDelegate(QStyledItemDelegate):
    """Badge and share painter usable on tables or trees with the shared roles."""

    def __init__(self, parent=None, *, safety_column=3, share_column=2):
        super().__init__(parent)
        self.safety_column = safety_column
        self.share_column = share_column

    def paint(self, painter, option, index):
        row = index.data(FACTS_ROLE)
        if index.column() == 0 and isinstance(row, RowFacts):
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            style = opt.widget.style() if opt.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
            rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
            painter.save()
            painter.setClipRect(rect)
            painter.setFont(opt.font)
            painter.setPen(
                opt.palette.highlightedText().color()
                if opt.state & QStyle.StateFlag.State_Selected
                else opt.palette.text().color()
            )
            title = opt.fontMetrics.elidedText(
                row.label, Qt.TextElideMode.ElideMiddle, int(max(0, rect.width()))
            )
            title_rect = QRect(rect)
            if row.subtitle:
                title_rect.setHeight(int(rect.height() // 2))
            painter.drawText(title_rect, int(Qt.AlignmentFlag.AlignVCenter), title)
            if row.subtitle:
                subtitle_rect = QRect(
                    int(rect.x()),
                    int(rect.y() + title_rect.height()),
                    int(rect.width()),
                    int(rect.height() - title_rect.height()),
                )
                painter.setPen(
                    opt.palette.highlightedText().color()
                    if opt.state & QStyle.StateFlag.State_Selected
                    else QColor(chrome("text_muted"))
                )
                subtitle = opt.fontMetrics.elidedText(
                    row.subtitle, Qt.TextElideMode.ElideRight, int(max(0, rect.width()))
                )
                painter.drawText(subtitle_rect, int(Qt.AlignmentFlag.AlignVCenter), subtitle)
            painter.restore()
            return
        if index.column() not in (self.safety_column, self.share_column):
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setClipRect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(
            option.rect, option.palette.highlight() if selected else QColor(chrome("surface"))
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        safety = index.data(SAFETY_ROLE) or "danger"
        color = QColor(semantic(SAFETY_MEANINGS.get(safety, "error")))
        rect = option.rect.adjusted(5, 4, -5, -4)
        if index.column() == self.safety_column:
            text = BADGES.get(safety, BADGES["danger"])
            width = min(rect.width(), option.fontMetrics.horizontalAdvance(text) + 18)
            height = min(rect.height(), option.fontMetrics.height() + 8)
            pill = QRect(
                int(rect.x()), int(rect.y() + (rect.height() - height) // 2),
                int(max(0, width)), int(max(0, height))
            )
            tint = QColor(color)
            tint.setAlpha(25)
            painter.setBrush(tint)
            painter.setPen(QPen(color, 1))
            painter.drawRoundedRect(pill, 6, 6)
            painter.setFont(option.font)
            painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), text)
        else:
            share = max(0.0, min(100.0, float(index.data(SHARE_ROLE) or 0)))
            text = f"{share:.1f}%"
            text_width = option.fontMetrics.horizontalAdvance(text) + 8
            track = QRect(
                int(rect.x()), int(rect.center().y() - 3), int(max(0, rect.width() - text_width)), 6
            )
            painter.fillRect(track, QColor(chrome("outline")))
            painter.fillRect(
                QRect(int(track.x()), int(track.y()), int(track.width() * share / 100), 6), color
            )
            painter.setPen(
                option.palette.highlightedText().color() if selected else QColor(chrome("text"))
            )
            painter.drawText(
                rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), text
            )
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        row = index.data(FACTS_ROLE)
        lines = 2 if isinstance(row, RowFacts) and row.subtitle else 1
        return QSize(
            int(size.width()), int(max(size.height(), option.fontMetrics.height() * lines + 12))
        )
