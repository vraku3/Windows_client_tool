"""Global dialog enhancer for QMessageBox and warning/error popups.

Provides two key capabilities for any popup dialog in the app:
1. All text labels are made selectable with mouse/keyboard, so users can highlight
   and copy any specific piece of text.
2. A prominent "📋 Copy text" button is injected at the bottom-left of the dialog,
   allowing one-click copying of the full message (title, main text, informative
   text, detailed text) with visual feedback ("✓ Copied!"), without closing or
   affecting dialog buttons / return codes.
3. Ctrl+C copies the full message text when no specific text selection is active.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QWidget,
)

from core.widget_life import widget_is_valid

logger = logging.getLogger(__name__)

_GLOBAL_ENHANCER: Optional[_MessageBoxCopyEnhancer] = None


def _is_widget_valid(w: QWidget) -> bool:
    return widget_is_valid(w)


def extract_box_text(box: QMessageBox) -> str:
    """Extract all human-readable text from a QMessageBox."""
    parts = []
    title = (box.windowTitle() or "").strip()
    if title:
        parts.append(f"[{title}]")
    text = (box.text() or "").strip()
    if text:
        parts.append(text)
    info = (box.informativeText() or "").strip()
    if info:
        parts.append(info)
    detail = (box.detailedText() or "").strip()
    if detail:
        parts.append(detail)

    if not text and not info and not detail:
        for lbl in box.findChildren(QLabel):
            t = (lbl.text() or "").strip()
            if t and t not in parts:
                parts.append(t)

    return "\n\n".join(parts)


class _MessageBoxCopyEnhancer(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if isinstance(obj, QMessageBox):
            if event.type() == QEvent.Type.Show:
                self._enhance_box(obj)
            elif event.type() == QEvent.Type.KeyPress:
                if self._handle_key_press(obj, event):
                    return True
        return super().eventFilter(obj, event)

    def _enhance_box(self, box: QMessageBox) -> None:
        if getattr(box, "_copy_enhanced", False):
            return
        box._copy_enhanced = True

        # 1. Make all labels selectable
        for lbl in box.findChildren(QLabel):
            lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
                | Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            if lbl.text():
                lbl.setCursor(Qt.CursorShape.IBeamCursor)

        # 2. Add copy button if not already present
        layout = box.layout()
        if not isinstance(layout, QGridLayout):
            return
        bb = box.findChild(QDialogButtonBox)
        if bb is None:
            return

        # Don't add duplicate copy button if one exists
        for btn in box.findChildren(QPushButton):
            if "copy" in (btn.text() or "").lower():
                return

        copy_btn = QPushButton("📋 Copy text", box)
        copy_btn.setObjectName("dialogCopyButton")
        copy_btn.setToolTip("Copy entire message to clipboard (Ctrl+C)")
        copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        def do_copy():
            full_text = extract_box_text(box)
            app = QApplication.instance()
            if app and full_text:
                app.clipboard().setText(full_text)
            copy_btn.setText("✓ Copied!")
            QTimer.singleShot(2000, lambda: self._reset_btn(copy_btn))

        copy_btn.clicked.connect(do_copy)
        box._dialog_do_copy = do_copy  # type: ignore[attr-defined]

        # Insert copy_btn into bottom row next to button box
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item and item.widget() == bb:
                r, c, rs, cs = layout.getItemPosition(i)
                layout.removeWidget(bb)
                layout.addWidget(copy_btn, r, 0, 1, 1, Qt.AlignmentFlag.AlignLeft)
                layout.addWidget(bb, r, 1, 1, max(1, cs - 1), Qt.AlignmentFlag.AlignRight)
                break

    def _reset_btn(self, btn: QPushButton) -> None:
        if not _is_widget_valid(btn):
            return
        try:
            if not btn.isHidden():
                btn.setText("📋 Copy text")
        except RuntimeError as exc:
            logger.debug("Button reset skipped: %s", exc)

    def _handle_key_press(self, box: QMessageBox, event: QEvent) -> bool:
        from PyQt6.QtGui import QKeyEvent
        if not isinstance(event, QKeyEvent):
            return False
        if event.key() == Qt.Key.Key_C and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            # Check if any input/label widget has an active selection
            for lbl in box.findChildren(QLabel):
                if lbl.hasSelectedText():
                    return False  # Let native copy happen
            for ed in box.findChildren((QLineEdit, QTextEdit)):
                if ed.textCursor().hasSelection():
                    return False

            # Copy full dialog text
            do_copy = getattr(box, "_dialog_do_copy", None)
            if callable(do_copy):
                do_copy()
                return True
            full_text = extract_box_text(box)
            app = QApplication.instance()
            if app and full_text:
                app.clipboard().setText(full_text)
                return True
        return False


def install_dialog_enhancer(app: Optional[QApplication] = None) -> None:
    """Install the global message box enhancer on the QApplication instance."""
    global _GLOBAL_ENHANCER
    if _GLOBAL_ENHANCER is not None:
        return
    qapp = app or QApplication.instance()
    if qapp is None:
        logger.warning("Cannot install dialog enhancer: QApplication instance is None")
        return
    _GLOBAL_ENHANCER = _MessageBoxCopyEnhancer()
    qapp.installEventFilter(_GLOBAL_ENHANCER)
    logger.debug("Dialog enhancer installed")


def uninstall_dialog_enhancer(app: Optional[QApplication] = None) -> None:
    """Safely uninstall the global message box enhancer before application shutdown."""
    global _GLOBAL_ENHANCER
    if _GLOBAL_ENHANCER is None:
        return
    qapp = app or QApplication.instance()
    if qapp is not None:
        try:
            qapp.removeEventFilter(_GLOBAL_ENHANCER)
        except Exception as e:
            logger.debug("Failed removing event filter in uninstall_dialog_enhancer: %s", e)
    _GLOBAL_ENHANCER = None
    logger.debug("Dialog enhancer uninstalled")
