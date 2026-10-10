"""The app list: one model behind both layouts, and the card painter.

Cards and Details show the same rows and the same checkboxes, so they share
one model; switching layout only swaps the view. Filtering and sorting are
the engine's (`engine.views.arrange`) -- the model is handed the result.
The checked set is keyed by app key, so it survives a refilter, a sort and a
rescan, as the manual says selections persist across Smart Views.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Sequence, Set

from PyQt6.QtCore import QAbstractTableModel, QFileInfo, QModelIndex, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QFileIconProvider, QStyle, QStyledItemDelegate,
                             QStyleOptionButton, QStyleOptionViewItem)

from core.semantic_colors import chrome, semantic

from .engine import model as m
from .engine import views

logger = logging.getLogger(__name__)

RECORD_ROLE = Qt.ItemDataRole.UserRole + 1
SORT_ROLE = Qt.ItemDataRole.UserRole + 2

_BADGE_MEANING = {m.REMOVE: "error", m.OPTIONAL: "warning", m.KEEP: "success"}


class IconCache:
    """App icons, loaded on first paint. A Windows app's logo PNG, a desktop
    app's DisplayIcon program, else the shell's icon for its folder."""

    def __init__(self) -> None:
        self._icons: Dict[str, QIcon] = {}
        self._provider: Optional[QFileIconProvider] = None

    def get(self, rec: m.AppRecord) -> QIcon:
        hit = self._icons.get(rec.key)
        if hit is not None:
            return hit
        icon = QIcon()
        path = rec.icon_path
        if path and path.lower().endswith((".png", ".jpg", ".ico")):
            icon = QIcon(path)
        elif path or rec.install_location:
            if self._provider is None:
                self._provider = QFileIconProvider()
            target = path or rec.install_location
            if os.path.exists(target):
                icon = self._provider.icon(QFileInfo(target))
        if icon.isNull():
            style = QApplication.style()
            pixmap = (QStyle.StandardPixmap.SP_DirIcon if rec.type == m.ORPHANED
                      else QStyle.StandardPixmap.SP_MessageBoxWarning if rec.type == m.DEFECT
                      else QStyle.StandardPixmap.SP_ComputerIcon if rec.type in (m.SYSTEM, m.FRAMEWORK)
                      else QStyle.StandardPixmap.SP_DesktopIcon)
            icon = style.standardIcon(pixmap) if style is not None else QIcon()
        self._icons[rec.key] = icon
        return icon

    def clear(self) -> None:
        self._icons.clear()


class AppModel(QAbstractTableModel):
    """Column 0 is Name (checkable); the rest are the chosen app-information fields."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: List[m.AppRecord] = []
        self.fields: List[str] = [k for k, _l, on in views.FIELDS if on]
        self.checked: Set[str] = set()
        self.icons = IconCache()
        self.on_check = None            # callback(key, checked)

    # ---- data in ---------------------------------------------------------------------------
    def set_rows(self, rows: Sequence[m.AppRecord]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def set_fields(self, fields: Sequence[str]) -> None:
        self.beginResetModel()
        self.fields = list(fields)
        self.endResetModel()

    def refresh_values(self) -> None:
        """Storage / updates arrived: repaint without a reset (keeps the view)."""
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, self.columnCount() - 1))

    def record(self, row: int) -> Optional[m.AppRecord]:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def row_of(self, key: str) -> int:
        return next((i for i, r in enumerate(self.rows) if r.key == key), -1)

    # ---- Qt --------------------------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:                       # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:                    # noqa: N802
        return 0 if parent.isValid() else 1 + len(self.fields)

    def column_field(self, column: int) -> str:
        return "name" if column == 0 else self.fields[column - 1]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            key = self.column_field(section)
            return "Name" if key == "name" else dict((k, label) for k, label, _o in views.FIELDS)[key]
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        rec = self.record(index.row())
        if index.column() == 0 and rec is not None and views.selectable(rec):
            base |= Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        rec = self.record(index.row())
        if rec is None:
            return None
        key = self.column_field(index.column())
        if role == Qt.ItemDataRole.DisplayRole:
            return views.field_text(rec, key)
        if role == RECORD_ROLE:
            return rec
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0 and views.selectable(rec):
            return Qt.CheckState.Checked if rec.key in self.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            return self.icons.get(rec)
        if role == Qt.ItemDataRole.ToolTipRole:
            if key == "recommendation":
                return m.RECOMMENDATION_TIP.get(rec.recommendation, "") + " " + rec.extra.get("why", "")
            if rec.type in (m.ORPHANED, m.DEFECT):
                return rec.reason
            return rec.description or rec.name
        if role == Qt.ItemDataRole.ForegroundRole and key == "recommendation":
            return QColor(semantic(_BADGE_MEANING.get(rec.recommendation, "info")))
        if role == Qt.ItemDataRole.ForegroundRole and key == "update" and rec.update:
            return QColor(semantic("info"))
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):         # noqa: N802
        rec = self.record(index.row())
        if rec is None or role != Qt.ItemDataRole.CheckStateRole:
            return False
        on = Qt.CheckState(value) == Qt.CheckState.Checked
        self.set_checked(rec.key, on)
        return True

    def set_checked(self, key: str, on: bool) -> None:
        (self.checked.add if on else self.checked.discard)(key)
        row = self.row_of(key)
        if row >= 0:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.CheckStateRole])
        if self.on_check:
            self.on_check(key, on)

    def set_checked_many(self, keys, on: bool) -> None:
        if on:
            self.checked |= set(keys)
        else:
            self.checked -= set(keys)
        self.refresh_values()
        if self.on_check:
            self.on_check("", on)


class CardDelegate(QStyledItemDelegate):
    """One wide row per app: checkbox, icon, name, the chosen facts beneath,
    and a recommendation badge on the right."""

    HEIGHT = 54

    def __init__(self, model: AppModel, parent=None) -> None:
        super().__init__(parent)
        self._model = model

    def sizeHint(self, option, index) -> QSize:                             # noqa: N802
        return QSize(max(option.rect.width(), 400), self.HEIGHT)

    def _check_rect(self, rect: QRect) -> QRect:
        return QRect(rect.left() + 10, rect.top() + (rect.height() - 18) // 2, 18, 18)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        rec = index.data(RECORD_ROLE)
        if rec is None:
            return
        painter.save()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(rect, QColor(chrome("surface_selected" if selected else "surface")))
        painter.setPen(QPen(QColor(chrome("outline"))))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        if views.selectable(rec):
            box = QStyleOptionButton()
            box.rect = self._check_rect(rect)
            box.state = QStyle.StateFlag.State_Enabled | (
                QStyle.StateFlag.State_On if rec.key in self._model.checked else QStyle.StateFlag.State_Off)
            style = QApplication.style()
            if style is not None:
                style.drawControl(QStyle.ControlElement.CE_CheckBox, box, painter)
        icon_rect = QRect(rect.left() + 38, rect.top() + (rect.height() - 32) // 2, 32, 32)
        self._model.icons.get(rec).paint(painter, icon_rect)
        badge_w = 92
        text_left = icon_rect.right() + 12
        text_w = rect.right() - badge_w - 16 - text_left
        name_font = QFont(option.font)
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(QColor(chrome("text")))
        title = rec.name + ("" if rec.status in (m.INSTALLED,) else f"   ({rec.status})")
        painter.drawText(QRect(text_left, rect.top() + 7, text_w, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, text_w))
        painter.setFont(option.font)
        painter.setPen(QColor(chrome("text_muted")))
        sub = views.subline(rec, self._model.fields) or rec.type
        if rec.type in (m.ORPHANED, m.DEFECT):
            sub = f"{rec.type} · {rec.purpose or rec.reason}"
        painter.drawText(QRect(text_left, rect.top() + 28, text_w, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, text_w))
        if "recommendation" in self._model.fields:
            colour = QColor(semantic(_BADGE_MEANING.get(rec.recommendation, "info")))
            badge = QRect(rect.right() - badge_w - 8, rect.top() + (rect.height() - 22) // 2, badge_w, 22)
            painter.setPen(QPen(colour, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.drawRoundedRect(badge, 11, 11)
            painter.setPen(colour)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, rec.recommendation)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:             # noqa: N802
        """A click on the checkbox toggles it; anywhere else selects the row."""
        from PyQt6.QtCore import QEvent
        rec = index.data(RECORD_ROLE)
        if rec is None or not views.selectable(rec):
            return False
        if event.type() == QEvent.Type.MouseButtonRelease and \
                self._check_rect(option.rect).adjusted(-4, -4, 4, 4).contains(event.position().toPoint()):
            self._model.set_checked(rec.key, rec.key not in self._model.checked)
            return True
        return False
