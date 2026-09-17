"""Pick a saved Group Policy report and see what has changed since.

The distinction this dialog exists to preserve: **a scope becoming visible is
not the machine changing.** Run the tool elevated and the computer half
appears with everything in it; a naive diff would announce hundreds of new
settings when nothing on the machine moved at all. `rsop_snapshot` labels that
case separately and this dialog shows it as its own line rather than as
findings.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.semantic_colors import semantic

from modules.gpresult.rsop_parser import RsopResult
from modules.gpresult.rsop_snapshot import (
    SnapshotMeta, delete_snapshot, diff_rsop, list_snapshots, load_snapshot,
)
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)


class SnapshotCompareDialog(QDialog):
    """Compare the report on screen against a saved one."""

    def __init__(self, current: Optional[RsopResult], parent=None,
                 directory: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare with a saved report")
        self.resize(900, 560)
        self._current = current
        self._directory = directory
        self._metas: List[SnapshotMeta] = []

        layout = QVBoxLayout(self)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        layout.addWidget(QLabel(
            "Pick a saved report to compare against the one on screen."))

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        left_layout.addWidget(self._list, 1)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setEnabled(False)
        left_layout.addWidget(self._delete_btn)
        splitter.addWidget(left)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Change", "Was", "Now"])
        self._tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive)
        self._tree.setColumnWidth(0, 380)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        splitter.addWidget(self._tree)
        splitter.setSizes([260, 640])
        layout.addWidget(splitter, 1)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close is not None:
            close.clicked.connect(self.reject)
        layout.addWidget(buttons)

        self._list.currentRowChanged.connect(self._on_selected)
        self._delete_btn.clicked.connect(self._delete_selected)
        self.reload()

    # ------------------------------------------------------------------

    def reload(self) -> None:
        self._list.clear()
        self._metas = list_snapshots(self._directory)
        for meta in self._metas:
            label = meta.label or meta.snapshot_id
            item = QListWidgetItem("%s\n%s" % (label, meta.snapshot_id))
            item.setToolTip(meta.path)
            if getattr(meta, "error", ""):
                item.setToolTip("%s\n%s" % (meta.path, meta.error))
                item.setForeground(_brush("error"))
            self._list.addItem(item)
        if not self._metas:
            self._summary.setText(
                "No saved reports yet. Use \"Snapshot\" on the Group Policy "
                "tab to save the current one.")

    def _on_selected(self, row: int) -> None:
        self._delete_btn.setEnabled(0 <= row < len(self._metas))
        self._tree.clear()
        if not (0 <= row < len(self._metas)):
            return
        if self._current is None:
            self._summary.setText(
                "There is no report on screen to compare against yet.")
            return

        meta = self._metas[row]
        snapshot = load_snapshot(meta.path)
        if snapshot.error:
            self._summary.setText("Could not read that snapshot: %s"
                                  % snapshot.error)
            self._summary.setStyleSheet("color: %s;" % semantic("error"))
            return

        self._summary.setStyleSheet("")
        diff = diff_rsop(snapshot.result, self._current)
        self._render(diff, meta)

    def _delete_selected(self) -> None:
        row = self._list.currentRow()
        if not (0 <= row < len(self._metas)):
            return
        meta = self._metas[row]
        confirm = QMessageBox.question(
            self, "Delete snapshot",
            "Delete the saved report %r?\n\nThis cannot be undone."
            % (meta.label or meta.snapshot_id),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if not delete_snapshot(meta.path):
            self._error_banner.set_error("That snapshot could not be deleted.")
        self.reload()

    # ------------------------------------------------------------------

    def _render(self, diff, meta: SnapshotMeta) -> None:
        total = 0
        for scope_diff in (diff.computer, diff.user):
            total += self._render_scope(scope_diff)

        when = meta.taken_at or meta.snapshot_id
        if total:
            self._summary.setText(
                "%d change(s) since %s." % (total, when))
        else:
            self._summary.setText("Nothing has changed since %s." % when)
        self._tree.expandAll()

    def _render_scope(self, scope_diff) -> int:
        name = "%s Configuration" % (scope_diff.scope or "?")
        root = QTreeWidgetItem(self._tree, [name])
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)

        counted = 0

        # A scope we simply could not see before is not a machine change.
        if getattr(scope_diff, "visibility_change", None):
            gained = scope_diff.visibility_change == "became_visible"
            note = QTreeWidgetItem(root, [
                "This scope %s since the snapshot"
                % ("became readable" if gained else "is no longer readable"),
                "",
                "%d setting(s) behind it"
                % scope_diff.settings_behind_visibility])
            note.setForeground(0, _brush("info"))
            root.setText(1, "visibility changed")
            return 0

        for change in scope_diff.settings_added:
            counted += 1
            self._change(root, "Added", _setting_label(change), "",
                         _setting_value(change), "success")
        for change in scope_diff.settings_removed:
            counted += 1
            self._change(root, "Removed", _setting_label(change),
                         _setting_value(change), "", "warning")
        for change in scope_diff.settings_changed:
            counted += 1
            self._change(root, "Changed", _setting_label(change),
                         getattr(change, "old_value", ""),
                         getattr(change, "new_value", ""), "warning")
        for change in scope_diff.gpos_added:
            counted += 1
            self._change(root, "GPO added", _gpo_label(change), "", "", "success")
        for change in scope_diff.gpos_removed:
            counted += 1
            self._change(root, "GPO removed", _gpo_label(change), "", "", "warning")
        for change in scope_diff.gpos_state_changed:
            counted += 1
            # A GPO that stopped applying is the single most consequential
            # change here, so it says which way it went and why.
            self._change(root, "GPO", _gpo_label(change),
                         _applied_word(change.old_applied, change.old_reason),
                         _applied_word(change.new_applied, change.new_reason),
                         "warning")
        for change in scope_diff.extension_changes:
            counted += 1
            self._change(root, "Extension", _ext_label(change),
                         getattr(change, "old_status", ""),
                         getattr(change, "new_status", ""), "info")

        root.setText(1, "%d change(s)" % counted if counted else "no changes")
        if not counted:
            QTreeWidgetItem(root, ["Nothing changed in this scope."])
        return counted

    @staticmethod
    def _change(parent, kind: str, label: str, was: str, now: str,
                meaning: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, ["%s: %s" % (kind, label), was, now])
        item.setForeground(0, _brush(meaning))
        return item


def _brush(meaning: str):
    from PyQt6.QtGui import QBrush, QColor
    return QBrush(QColor(semantic(meaning)))


def _setting_label(change) -> str:
    for attr in ("name", "setting", "key"):
        value = getattr(change, attr, "")
        if value:
            category = getattr(change, "category", "")
            return "%s / %s" % (category, value) if category else str(value)
    return str(change)


def _setting_value(change) -> str:
    for attr in ("value", "new_value", "old_value"):
        value = getattr(change, attr, "")
        if value:
            return str(value)
    return ""


def _applied_word(applied: bool, reason: str) -> str:
    if applied:
        return "applied"
    return "not applied (%s)" % reason if reason else "not applied"


def _gpo_label(change) -> str:
    return str(getattr(change, "name", None) or getattr(change, "gpo", change))


def _ext_label(change) -> str:
    return str(getattr(change, "name", change))
