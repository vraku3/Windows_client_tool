"""Global auto-size & interactive resize enhancer for tables, trees, and scroll areas.

Ensures every table and tree across all modules, tabs, subtabs, and dialogs:
1. Allows the user to resize every column horizontally (`Interactive` mode)
   and every row vertically (`Interactive` vertical header), instead of locking
   sections with `Fixed` or `Stretch`-only modes.
2. Auto-fits columns and multi-line rows on first data load so values are
   readable without clipping, while preserving user-dragged widths afterwards.
3. Ensures `QScrollArea` containers have `widgetResizable=True` and smooth
   per-pixel scrolling in both directions.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QScrollArea,
    QTabWidget,
    QTableView,
    QTreeView,
)

from core.widget_life import widget_is_valid

logger = logging.getLogger(__name__)

_GLOBAL_RESIZER: Optional["_GlobalTableResizer"] = None
_MAX_AUTO_COL_WIDTH = 420
_MIN_AUTO_COL_WIDTH = 48


def _make_columns_interactive(header: QHeaderView, view: QAbstractItemView) -> None:
    """Convert non-interactive columns to Interactive so users can drag any divider."""
    count = header.count()
    if count <= 0:
        return
    for col in range(count):
        mode = header.sectionResizeMode(col)
        # Keep tiny fixed icon/checkbox columns (<= 36px) fixed; unlock everything else.
        if mode == QHeaderView.ResizeMode.Fixed and header.sectionSize(col) <= 36:
            continue
        if mode != QHeaderView.ResizeMode.Interactive:
            current_w = header.sectionSize(col)
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            if current_w >= _MIN_AUTO_COL_WIDTH:
                header.resizeSection(col, current_w)
    if header.minimumSectionSize() < 24:
        header.setMinimumSectionSize(24)
    if not getattr(view, "_no_stretch_last", False):
        header.setStretchLastSection(True)


def _auto_fit_view_once(view: QAbstractItemView) -> None:
    """Fit columns (and multi-line rows) to content once after rows are populated."""
    if not widget_is_valid(view):
        return
    model = view.model()
    if model is None or model.rowCount() <= 0:
        return
    if getattr(view, "_auto_fitted_once", False):
        return
    view._auto_fitted_once = True  # type: ignore[attr-defined]

    if isinstance(view, QTableView):
        header = view.horizontalHeader()
        # Only auto-size columns that haven't been explicitly sized wide already
        # (preserves custom width logic such as FirewallManagerModule / LogTableWidget).
        if not getattr(view, "_custom_column_fit", False):
            saved_modes = [header.sectionResizeMode(c) for c in range(header.count())]
            view.resizeColumnsToContents()
            for col in range(header.count()):
                w = header.sectionSize(col)
                if saved_modes[col] == QHeaderView.ResizeMode.Fixed and w <= 36:
                    continue
                clamped = max(_MIN_AUTO_COL_WIDTH, min(w + 14, _MAX_AUTO_COL_WIDTH))
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
                header.resizeSection(col, clamped)
        _make_columns_interactive(header, view)

        vertical = view.verticalHeader()
        if vertical is not None:
            vertical.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            if vertical.minimumSectionSize() < 22:
                vertical.setMinimumSectionSize(22)
            if view.wordWrap():
                view.resizeRowsToContents()

    elif isinstance(view, QTreeView):
        header = view.header()
        if not getattr(view, "_custom_column_fit", False):
            for col in range(header.count()):
                if header.sectionResizeMode(col) == QHeaderView.ResizeMode.Fixed and header.sectionSize(col) <= 36:
                    continue
                view.resizeColumnToContents(col)
                w = header.sectionSize(col)
                clamped = max(_MIN_AUTO_COL_WIDTH, min(w + 14, _MAX_AUTO_COL_WIDTH))
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
                header.resizeSection(col, clamped)
        _make_columns_interactive(header, view)


def enhance_table_or_tree(view: QAbstractItemView) -> None:
    """Make a QTableView/QTableWidget or QTreeView/QTreeWidget auto-sized and user-resizable."""
    if not widget_is_valid(view):
        return

    view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    if isinstance(view, QTableView):
        header = view.horizontalHeader()
        _make_columns_interactive(header, view)
        vertical = view.verticalHeader()
        if vertical is not None:
            if not getattr(view, "_keep_hidden_vertical_header", False):
                vertical.setVisible(True)
            if vertical.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed or vertical.count() == 0:
                vertical.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            if vertical.minimumSectionSize() < 22:
                vertical.setMinimumSectionSize(22)
            if vertical.defaultSectionSize() < 28:
                vertical.setDefaultSectionSize(28)
    elif isinstance(view, QTreeView):
        _make_columns_interactive(view.header(), view)

    if not getattr(view, "_auto_resize_hooked", False):
        view._auto_resize_hooked = True  # type: ignore[attr-defined]
        model = view.model()
        if model is not None:
            def _schedule_fit(*_args) -> None:
                if widget_is_valid(view) and not getattr(view, "_auto_fitted_once", False):
                    QTimer.singleShot(0, lambda: _auto_fit_view_once(view))

            model.rowsInserted.connect(_schedule_fit)
            model.modelReset.connect(_schedule_fit)

        if model is not None and model.rowCount() > 0:
            QTimer.singleShot(0, lambda: _auto_fit_view_once(view))


class _GlobalTableResizer(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show:
            if isinstance(obj, (QTableView, QTreeView)):
                enhance_table_or_tree(obj)
            elif isinstance(obj, QScrollArea):
                if not obj.widgetResizable():
                    obj.setWidgetResizable(True)
            elif isinstance(obj, QTabWidget):
                obj.setUsesScrollButtons(True)
        return super().eventFilter(obj, event)


def install_table_resizer(app: Optional[QApplication] = None) -> None:
    """Install the global table/tree/scroll resizer on the QApplication."""
    global _GLOBAL_RESIZER
    if app is None:
        app = QApplication.instance()
    if app is None:
        return
    if _GLOBAL_RESIZER is None:
        _GLOBAL_RESIZER = _GlobalTableResizer(app)
        app.installEventFilter(_GLOBAL_RESIZER)


def uninstall_table_resizer(app: Optional[QApplication] = None) -> None:
    """Remove the global table/tree/scroll resizer."""
    global _GLOBAL_RESIZER
    if app is None:
        app = QApplication.instance()
    if app is not None and _GLOBAL_RESIZER is not None:
        try:
            app.removeEventFilter(_GLOBAL_RESIZER)
        except Exception as e:
            logger.debug("Failed removing event filter in uninstall_table_resizer: %s", e)
    _GLOBAL_RESIZER = None
