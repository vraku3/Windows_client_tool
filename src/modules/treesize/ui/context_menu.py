"""Right-click menu for tree and table rows (spec 7.1, 7.2).

Pro's own set: open, open containing folder, copy path, exclude, and the
destructive operations. Built here once so the directory tree, Details and Top
Files all offer the same actions in the same order — a clone stops feeling like
one product the moment the same right-click does different things in two panes.

Destructive entries route through `actions.file_ops`, which preflights against
`guardrails` first. Nothing here deletes anything directly.
"""
import os
import subprocess

from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QMenu, QMessageBox,
)

from ..actions import file_ops, guardrails
from ..store.node_store import DIR
from .confirm_dialog import TypedConfirmDialog


def _reveal(path: str) -> None:
    """Show the item in Explorer, selected.

    Args are passed as a list, never a formatted string: a path is user data
    and there is no reason for it to reach a shell.
    """
    if os.path.isdir(path):
        subprocess.Popen(["explorer.exe", path])
    else:
        subprocess.Popen(["explorer.exe", "/select,", path])


def _open(path: str) -> None:
    os.startfile(path)          # noqa: S606 -- the shell "open" verb, by design


class RowActions:
    """Builds the shared context menu and carries out what it offers."""

    def __init__(self, shell) -> None:
        self._shell = shell

    # ---- menu ----------------------------------------------------------

    def menu_for(self, node: int, parent=None) -> QMenu | None:
        """Build the menu for one row, or None if the node is not real.

        The menu is parented to the shell when no parent is given: an unparented
        QMenu is garbage-collected as soon as the caller lets go of it, taking
        its QActions with it, and the next touch raises "wrapped C/C++ object
        has been deleted".
        """
        store = self._shell._store
        if store is None or not (0 <= node < len(store)):
            return None
        path = store.path(node)
        is_dir = bool(store.attrs[node] & DIR)
        menu = QMenu(parent if parent is not None else self._shell)

        menu.addAction("Open", lambda: self._open(path))
        menu.addAction("Open containing folder", lambda: self._reveal(path))
        menu.addSeparator()
        menu.addAction("Copy path", lambda: QApplication.clipboard().setText(path))
        menu.addAction("Copy name", lambda: QApplication.clipboard().setText(
            store.name(node)))
        menu.addSeparator()
        if is_dir:
            menu.addAction("Scan this folder",
                           lambda: self._shell.start_scan(path))
        menu.addAction("Exclude from scan",
                       lambda: self._exclude(store.name(node)))
        menu.addSeparator()

        recycle = menu.addAction("Delete to Recycle Bin",
                                 lambda: self._delete(node, recycle=True))
        permanent = menu.addAction("Delete permanently…",
                                   lambda: self._delete(node, recycle=False))
        move = menu.addAction("Move to…", lambda: self._move(node))
        erase = menu.addAction("Secure erase…",
                               lambda: self._secure_erase(node))
        # A refused path gets a disabled entry with the reason in the tooltip,
        # rather than an entry that looks available and then complains. A move
        # and an erase are exactly as destructive as a delete, so they are
        # gated with them rather than beside them.
        if not guardrails.is_allowed(path, override=True):
            for action in (recycle, permanent, move, erase):
                action.setEnabled(False)
                action.setToolTip("Protected location")
        menu.addSeparator()
        menu.addAction("Properties", lambda: self._properties(node))
        return menu

    # ---- handlers ------------------------------------------------------

    def _open(self, path: str) -> None:
        try:
            _open(path)
        except OSError as exc:
            self._warn("Could not open", f"{path}\n\n{exc}")

    def _reveal(self, path: str) -> None:
        try:
            _reveal(path)
        except OSError as exc:
            self._warn("Could not open Explorer", f"{path}\n\n{exc}")

    def _exclude(self, name: str) -> None:
        filters = self._shell._filters
        filters.exclude_globs = tuple(filters.exclude_globs) + (name,)
        self._shell.refresh_scan()

    def _properties(self, node: int) -> None:
        store = self._shell._store
        from .formatting import format_bytes, format_count
        from .panels import format_filetime
        lines = [
            store.path(node),
            "",
            f"Size:          {format_bytes(store.size[node])}",
            f"Allocated:     {format_bytes(store.alloc[node])}",
            f"Files:         {format_count(store.file_count[node])}",
            f"Folders:       {format_count(store.folder_count[node])}",
            f"Last Modified: {format_filetime(store.mtime[node])}",
            f"Last Accessed: {format_filetime(store.atime[node])}",
            f"Created:       {format_filetime(store.ctime[node])}",
            f"Owner:         {store.owner(store.owner_id[node]) or '—'}",
        ]
        QMessageBox.information(self._shell, store.name(node), "\n".join(lines))

    def _delete(self, node: int, *, recycle: bool) -> None:
        store = self._shell._store
        path = store.path(node)
        preflight = file_ops.plan(
            "Recycle" if recycle else "Delete permanently",
            [(path, store.size[node])], override=False)

        if not preflight.allowed and preflight.refusals:
            # Offer the override rather than a flat refusal, but make the user
            # read what they are overriding.
            box = QMessageBox(self._shell)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Protected location")
            box.setText("\n".join(preflight.refusals))
            override = QCheckBox("I understand — operate here anyway")
            box.setCheckBox(override)
            box.setStandardButtons(QMessageBox.StandardButton.Cancel
                                   | QMessageBox.StandardButton.Ok)
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Ok or not override.isChecked():
                return
            preflight = file_ops.plan(preflight.operation,
                                      [(path, store.size[node])], override=True)
            if not preflight.allowed:
                self._warn("Refused", "\n".join(preflight.refusals))
                return

        if recycle:
            confirm = QMessageBox(self._shell)
            confirm.setIcon(QMessageBox.Icon.Warning)
            confirm.setWindowTitle(preflight.operation)
            confirm.setText(preflight.summary())
            dry_run = QCheckBox(
                "Dry run — log what would happen, change nothing")
            confirm.setCheckBox(dry_run)
            confirm.setStandardButtons(QMessageBox.StandardButton.Cancel
                                       | QMessageBox.StandardButton.Ok)
            confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if confirm.exec() != QMessageBox.StandardButton.Ok:
                return
            wants_dry_run = dry_run.isChecked()
        else:
            # Spec 7.2 asks for a TYPED confirmation here, not a stronger
            # sentence in a box that Enter walks straight through. There is
            # nothing to undo a permanent delete with.
            dialog = TypedConfirmDialog(
                preflight.operation, preflight.summary(), phrase="DELETE",
                caveat="This cannot be undone. The Recycle Bin is not "
                       "involved.",
                parent=self._shell)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            wants_dry_run = dialog.dry_run.isChecked()

        # show_progress hands the job to the shell's own progress dialog --
        # the one Explorer shows, with a working Cancel. Not for a dry run,
        # which touches nothing and would flash an empty window.
        ok, message = file_ops.execute(preflight, recycle=recycle,
                                       dry_run=wants_dry_run,
                                       show_progress=not wants_dry_run)
        if not ok:
            self._warn(preflight.operation, message)
            return
        self._shell.scan_state.setText(message)
        if not wants_dry_run:
            self._shell.refresh_scan()

    def _warn(self, title: str, message: str) -> None:
        banner = getattr(self._shell, "error_banner", None)
        if banner is not None:
            banner.set_error(f"{title}: {message}")
        else:
            QMessageBox.warning(self._shell, title, message)

    # ---- move and secure erase (spec 7.1) ------------------------------

    def _move(self, node: int) -> None:
        """Move to a folder the user picks. Plain confirmation: a move is
        undoable by moving it back, which is what separates it from the two
        operations that get a typed gate."""
        store = self._shell._store
        path = store.path(node)
        destination = QFileDialog.getExistingDirectory(
            self._shell, "Move to which folder?", os.path.dirname(path))
        if not destination:
            return
        preflight = self._planned("Move", node, path)
        if preflight is None:
            return
        confirm = QMessageBox(self._shell)
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setWindowTitle("Move")
        confirm.setText(preflight.summary())
        confirm.setInformativeText(f"Destination: {destination}")
        dry_run = QCheckBox("Dry run — log what would happen, change nothing")
        confirm.setCheckBox(dry_run)
        confirm.setStandardButtons(QMessageBox.StandardButton.Cancel
                                   | QMessageBox.StandardButton.Ok)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Ok:
            return
        ok, message = file_ops.move(preflight, destination,
                                    dry_run=dry_run.isChecked(),
                                    show_progress=not dry_run.isChecked())
        self._report(ok, "Move", message, refresh=not dry_run.isChecked())

    def _secure_erase(self, node: int) -> None:
        """Overwrite then delete, behind a typed gate and the SSD caveat."""
        store = self._shell._store
        path = store.path(node)
        preflight = self._planned("Secure erase", node, path)
        if preflight is None:
            return
        dialog = TypedConfirmDialog(
            "Secure erase", preflight.summary(), phrase="ERASE",
            caveat=file_ops.SSD_CAVEAT, parent=self._shell)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        ok, message = file_ops.secure_erase(
            preflight, dry_run=dialog.dry_run.isChecked())
        self._report(ok, "Secure erase", message,
                     refresh=not dialog.dry_run.isChecked())

    def _planned(self, operation: str, node: int, path: str):
        """Preflight, offering the override once if the path is guarded."""
        store = self._shell._store
        preflight = file_ops.plan(operation, [(path, store.size[node])],
                                  override=False)
        if preflight.allowed or not preflight.refusals:
            return preflight
        box = QMessageBox(self._shell)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Protected location")
        box.setText("\n".join(preflight.refusals))
        override = QCheckBox("I understand — operate here anyway")
        box.setCheckBox(override)
        box.setStandardButtons(QMessageBox.StandardButton.Cancel
                               | QMessageBox.StandardButton.Ok)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok or not override.isChecked():
            return None
        retried = file_ops.plan(operation, [(path, store.size[node])],
                                override=True)
        if not retried.allowed:
            self._warn("Refused", "\n".join(retried.refusals))
            return None
        return retried

    def _report(self, ok: bool, title: str, message: str,
                refresh: bool) -> None:
        if not ok:
            self._warn(title, message)
            return
        self._shell.scan_state.setText(message)
        if refresh:
            # The store is a snapshot and the disk has just moved; anything
            # else leaves the tree showing what is no longer there.
            self._shell.refresh_scan()
