r"""Hosts File Editor — manage C:\Windows\System32\drivers\etc\hosts entries.

Saves are lossless (comments and headers survive), backed up first, verified by
reading the file back, and rolled back if the read-back disagrees. The model
lives in `hosts_analysis.py`.
"""
import logging
import os
from typing import Dict, List, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import centered_item, fit_table, set_role
from core.windows_utils import system32
from modules.hosts_editor import hosts_analysis as ha

logger = logging.getLogger(__name__)

HOSTS_PATH = os.path.join(system32(), "drivers", "etc", "hosts")


def backup_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "WindowsTweaker", "hosts_backups")


# Pre-built telemetry blocklist
TELEMETRY_BLOCKLIST = [
    ("0.0.0.0", "vortex.data.microsoft.com", "Microsoft telemetry"),
    ("0.0.0.0", "settings-win.data.microsoft.com", "Microsoft settings sync"),
    ("0.0.0.0", "watson.telemetry.microsoft.com", "Microsoft Watson"),
    ("0.0.0.0", "telemetry.microsoft.com", "Microsoft telemetry"),
    ("0.0.0.0", "cdn-settings-win.data.microsoft.com", "Microsoft CDN"),
    ("0.0.0.0", "purchase-prod.adobe.com", "Adobe telemetry"),
    ("0.0.0.0", "ims-na1.adobelogin.com", "Adobe login"),
    ("0.0.0.0", "analytics.datoclick.com", "General analytics"),
    ("0.0.0.0", "stats.g.doubleclick.net", "Google Analytics"),
    ("0.0.0.0", "www.google-analytics.com", "Google Analytics"),
    ("0.0.0.0", "collector.google.com", "Google telemetry"),
    ("0.0.0.0", "display.ads.linkedin.com", "LinkedIn ads"),
    ("0.0.0.0", "pixel.facebook.com", "Facebook pixel"),
]


class HostsEditorModule(BaseModule):
    name = "Hosts Editor"
    icon = "🌐"
    description = "Edit the Windows hosts file to block telemetry and manage DNS"
    group = ModuleGroup.TOOLS
    requires_admin = True

    def __init__(self):
        super().__init__()
        self._widget: QWidget = None
        self._orig_lines: List[ha.HostLine] = []
        self._modified = False
        self._loaded = False
        self._filling = False
        self._current_findings: list = []

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        for text, tip, slot in (
            ("Save", "Back up, write, then read the file back to verify", self._save),
            ("Backup", "Copy the current hosts file to the backup folder", self._backup),
            ("Restore...", "Restore the hosts file from a backup", self._restore),
            ("Reload", "Discard edits and re-read the file", self._reload),
            ("Add Entry", "", self._add_entry),
            ("Delete Selected", "", self._delete_selected),
            ("Import Blocklist", "Import telemetry blocklist", self._import_blocklist),
        ):
            b = QPushButton(text)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(slot)
            toolbar.addWidget(b)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Enabled", "IP Address", "Hostname(s)", "Comment"])
        fit_table(self._table, stretch=[2, 3], content=[0, 1])
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, 1)

        self._findings = QListWidget()
        self._findings.setMaximumHeight(130)
        self._findings.itemSelectionChanged.connect(self._select_finding_rows)
        layout.addWidget(self._findings)

        self._status = QLabel("")
        set_role(self._status, "muted")
        layout.addWidget(self._status)
        return self._widget

    def on_start(self, app) -> None:
        self.app = app

    def on_activate(self) -> None:
        # BaseModule.refresh_data falls back to on_activate, so the host's
        # auto-refresh timer reaches here — possibly before this tab has ever
        # been opened and its widget built.
        if self._widget is None:
            return
        if not self._loaded:
            self._loaded = True
            self._load()

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_status_info(self) -> str:
        rows = self._rows() if self._widget is not None else []
        enabled = sum(1 for r in rows if r["enabled"])
        return f"Hosts Editor — {enabled}/{len(rows)} entries active"

    # ── loading ─────────────────────────────────────────────────────────────

    def _read_file(self) -> Tuple[str, str]:
        """(text, error). A refusal is an error, never an empty file."""
        if not os.path.exists(HOSTS_PATH):
            return "", "Hosts file not found"
        try:
            with open(HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
                return f.read(), ""
        except PermissionError:
            return "", "Permission denied — run as Administrator"
        except OSError as exc:
            logger.warning("Cannot read hosts file: %s", exc)
            return "", "Could not read the hosts file: %s" % exc

    def _load(self) -> None:
        text, err = self._read_file()
        self._filling = True
        self._table.setRowCount(0)
        self._filling = False
        self._orig_lines = []
        if err:
            self._status.setText(err)
            self._findings.clear()
            return
        self._orig_lines = ha.parse_hosts(text)
        self._filling = True
        for ln in ha.entries(self._orig_lines):
            self._add_row(ln.enabled, ln.ip, " ".join(ln.hosts), ln.comment, ln.index)
        self._filling = False
        self._modified = False
        self._status.setText("%d entries; %d other line(s) (comments) are preserved on save."
                             % (len(ha.entries(self._orig_lines)),
                                len(self._orig_lines) - len(ha.entries(self._orig_lines))))
        self._update_findings()

    def _reload(self) -> None:
        if self._modified and not confirm_destructive(
                self._widget, "Discard changes", "Discard unsaved edits and re-read the hosts file?",
                irreversible=False):
            return
        self._load()

    # ── table <-> model ─────────────────────────────────────────────────────

    def _add_row(self, enabled: bool, ip: str, hosts: str, comment: str, origin: int) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        cb = QCheckBox()
        cb.setChecked(enabled)
        cb.stateChanged.connect(self._on_edited)
        self._table.setCellWidget(row, 0, cb)
        ip_item = centered_item(ip)
        ip_item.setData(Qt.ItemDataRole.UserRole, origin)
        self._table.setItem(row, 1, ip_item)
        self._table.setItem(row, 2, centered_item(hosts))
        self._table.setItem(row, 3, centered_item(comment))

    def _rows(self) -> List[Dict]:
        rows = []
        for i in range(self._table.rowCount()):
            cb = self._table.cellWidget(i, 0)
            ip_item = self._table.item(i, 1)
            text = lambda c: self._table.item(i, c).text().strip() if self._table.item(i, c) else ""  # noqa: E731
            origin = ip_item.data(Qt.ItemDataRole.UserRole) if ip_item else None
            hosts = text(2).split()
            if not hosts:
                continue
            rows.append({"origin": -1 if origin is None else int(origin),
                         "enabled": cb.isChecked() if cb else True,
                         "ip": text(1), "hosts": hosts, "comment": text(3), "row": i})
        return rows

    def _on_item_changed(self, _item) -> None:
        self._on_edited()

    def _on_edited(self, *_a) -> None:
        if self._filling:
            return
        self._modified = True
        self._update_findings()

    def _update_findings(self) -> None:
        lines = [ha.HostLine("", True, r["enabled"], r["ip"], r["hosts"], r["comment"], r["row"])
                 for r in self._rows()]
        self._current_findings = ha.analyse(lines)
        self._findings.clear()
        for f in self._current_findings:
            item = QListWidgetItem("[%s] %s" % (f.severity.upper(), f.title))
            item.setToolTip(f.detail)
            item.setData(Qt.ItemDataRole.UserRole, f)
            self._findings.addItem(item)
        if not self._current_findings:
            self._findings.addItem("No problems found.")

    def _select_finding_rows(self) -> None:
        items = self._findings.selectedItems()
        f = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        self._table.clearSelection()
        if f is None:
            return
        for r in f.lines:
            if 0 <= r < self._table.rowCount():
                self._table.selectRow(r)
                self._table.item(r, 1).setForeground(self._colour(f.severity))
        self._status.setText("%s — %s" % (f.title, f.detail))

    @staticmethod
    def _colour(sev: str):
        from PyQt6.QtGui import QColor
        return QColor(semantic("error" if sev == ha.SEV_HIGH else "warning"))

    # ── actions ─────────────────────────────────────────────────────────────

    def _save(self) -> None:
        rows = self._rows()
        blocking = [f for f in self._current_findings if f.key in ("bad_ip", "bad_host")]
        if blocking:
            QMessageBox.warning(self._widget, "Cannot save",
                                "Fix these first:\n" + "\n".join(f.title for f in blocking))
            return
        text = ha.serialize(self._orig_lines, rows)
        warn = [f.title for f in self._current_findings
                if f.severity in (ha.SEV_HIGH, ha.SEV_MEDIUM)]
        if not confirm_destructive(
                self._widget, "Save hosts file",
                "Write %d entries to the hosts file? A backup is taken first." % len(rows),
                detail=("Warnings:\n" + "\n".join(warn[:10])) if warn else "",
                irreversible=False):
            return
        try:
            bak = ha.backup_hosts(HOSTS_PATH, backup_dir())
        except OSError as exc:
            logger.warning("Hosts backup failed: %s", exc)
            QMessageBox.critical(self._widget, "Backup failed",
                                 "Nothing was written, because the backup failed:\n%s" % exc)
            return
        err = ha.write_and_verify(HOSTS_PATH, text)
        if err:
            with open(bak, encoding="utf-8", errors="replace") as fh:
                rolled = ha.write_and_verify(HOSTS_PATH, fh.read())
            QMessageBox.critical(
                self._widget, "Save failed",
                "The hosts file was not saved: %s\n%s" % (
                    err, "It was restored from the backup." if not rolled
                    else "Restoring the backup ALSO failed (%s); copy it back by hand:\n%s" % (rolled, bak)))
            return
        self._load()
        self._status.setText("Saved and verified. Backup: %s" % bak)

    def _backup(self) -> None:
        try:
            bak = ha.backup_hosts(HOSTS_PATH, backup_dir())
        except OSError as exc:
            logger.warning("Hosts backup failed: %s", exc)
            QMessageBox.warning(self._widget, "Backup Failed", str(exc))
            return
        self._status.setText("Backed up to %s" % bak)

    def _restore(self) -> None:
        start = backup_dir() if os.path.isdir(backup_dir()) else ""
        path, _ = QFileDialog.getOpenFileName(self._widget, "Restore hosts file from...", start,
                                              "Hosts backups (hosts_*.txt);;All files (*)")
        if not path:
            return
        if not confirm_destructive(
                self._widget, "Restore hosts file",
                "Replace the current hosts file with:\n%s\n\nThe current file is backed up first." % path,
                irreversible=False):
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            ha.backup_hosts(HOSTS_PATH, backup_dir())
        except OSError as exc:
            logger.warning("Hosts restore failed before write: %s", exc)
            QMessageBox.critical(self._widget, "Restore failed", str(exc))
            return
        err = ha.write_and_verify(HOSTS_PATH, text)
        if err:
            QMessageBox.critical(self._widget, "Restore failed", err)
            return
        self._load()
        self._status.setText("Restored from %s (read back and verified)." % path)

    def _add_entry(self) -> None:
        self._filling = True
        self._add_row(True, "0.0.0.0", "example.com", "", -1)
        self._filling = False
        self._on_edited()

    def _delete_selected(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedItems()}, reverse=True)
        if not rows:
            return
        if not confirm_destructive(self._widget, "Delete entries",
                                   "Remove %d entr%s from the list? Nothing is written until you Save."
                                   % (len(rows), "y" if len(rows) == 1 else "ies"),
                                   irreversible=False):
            return
        self._filling = True
        for r in rows:
            self._table.removeRow(r)
        self._filling = False
        self._on_edited()

    def _import_blocklist(self) -> None:
        if not confirm_destructive(
                self._widget, "Import Blocklist",
                "Add the telemetry blocklist entries that are not already present?",
                irreversible=False):
            return
        have = {h.lower() for r in self._rows() for h in r["hosts"]}
        added = 0
        self._filling = True
        for ip, hostname, comment in TELEMETRY_BLOCKLIST:
            if hostname.lower() in have:
                continue
            self._add_row(True, ip, hostname, comment, -1)
            added += 1
        self._filling = False
        self._on_edited()
        self._status.setText("Added %d entries (%d already present). Save to apply."
                             % (added, len(TELEMETRY_BLOCKLIST) - added))
