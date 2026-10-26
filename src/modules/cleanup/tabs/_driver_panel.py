r"""Superseded driver packages, on the Large Items tab.

Deliberately NOT part of the checkbox tree the rest of this tab uses.
`delete_items` deletes paths, and deleting a `DriverStore\FileRepository`
folder out from under Windows is how a machine loses a driver it still
believes it has. The only correct removal is
`pnputil /delete-driver <published>`, so it gets its own panel and its own
confirmed action — the same separation the DISM section beside it already
has.

What it reports is measured, not estimated: see
`cleanup_scanner/driver_store.py` for why an unelevated enumeration is a
refusal rather than an empty store, and why a package whose folder cannot
be found is reported as unknown rather than as zero bytes.
"""
import logging

from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from core.long_op_pool import get_long_op_pool
from core.widget_life import widget_is_valid
from core.worker import Worker, retire
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.cleanup_scanner import driver_store

logger = logging.getLogger(__name__)


class _DriverStorePanel(QWidget):
    """Analyse the driver store, and remove superseded packages on request."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._report = None
        self._worker = None
        self._pool = get_long_op_pool()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)

        row = QHBoxLayout()
        title = QLabel("<b>Superseded Drivers</b>")
        self._analyze_btn = QPushButton("Analyze Driver Store")
        self._analyze_btn.setToolTip(
            "Runs: pnputil /enum-drivers\n"
            "Lists third-party driver packages that a newer version of the "
            "same driver has replaced.\nRequires admin — unelevated, pnputil "
            "prints its usage banner and reports nothing.")
        self._remove_btn = QPushButton("Remove Superseded…")
        self._remove_btn.setToolTip(
            "Runs: pnputil /delete-driver <package> for each one listed.\n"
            "Never deletes from the driver store directory directly.")
        self._remove_btn.setEnabled(False)
        row.addWidget(title)
        row.addStretch()
        row.addWidget(self._analyze_btn)
        row.addWidget(self._remove_btn)
        layout.addLayout(row)

        description = QLabel(
            "Every driver Windows has ever installed stays in the driver "
            "store so it can be rolled back to. When a newer version of the "
            "same driver arrives, the older package is superseded and can be "
            "removed. Only packages a newer version clearly replaces are "
            "listed — where the version and the date disagree about which is "
            "newer, neither is offered.")
        description.setWordWrap(True)
        # The role, not a colour: an inline sheet beats the theme's and never
        # changes again, so `color: #888` survives a switch to the light
        # theme as a 1.9:1 smear. QLabel#muted is what dark.qss defines for
        # exactly this. (The DISM section above still writes it inline; not
        # changed here as a drive-by.)
        description.setObjectName("muted")
        layout.addWidget(description)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFixedHeight(110)
        self._output.hide()
        layout.addWidget(self._output)

        self._analyze_btn.clicked.connect(self._analyze)
        self._remove_btn.clicked.connect(self._remove)

    # ── Analyse ──

    def _analyze(self) -> None:
        self._analyze_btn.setEnabled(False)
        self._remove_btn.setEnabled(False)
        self._output.clear()
        self._output.show()
        self._output.append("Enumerating the driver store…\n")

        def _run(_worker):
            return driver_store.superseded_report()

        def _err(message: str):
            if not widget_is_valid(self):
                return
            self._analyze_btn.setEnabled(True)
            self._output.append(f"Error: {message}")

        self._worker = Worker(_run)
        self._worker.signals.result.connect(self._on_report)
        self._worker.signals.error.connect(_err)
        self._pool.start(self._worker)

    def _on_report(self, report) -> None:
        if not widget_is_valid(self):
            return
        self._report = report
        self._analyze_btn.setEnabled(True)
        self._output.clear()
        self._output.show()

        if not report.available:
            self._output.append(report.reason)
            self._remove_btn.setEnabled(False)
            return

        if not report.packages:
            self._output.append(
                f"{report.reason}\nNothing to remove.")
            self._remove_btn.setEnabled(False)
            return

        self._output.append(report.reason)
        self._output.append(
            f"Reclaimable: {cs.format_size(report.total_bytes)}"
            + (f"  (plus {len(report.unsized)} package(s) of unknown size)"
               if report.unsized else ""))
        self._output.append("")
        for package in report.packages:
            size = driver_store.package_size(package)
            shown = cs.format_size(size) if size is not None else "unknown size"
            self._output.append(
                f"  {package.published}  {package.original}  "
                f"{package.version_text}  — {shown}")
        self._remove_btn.setEnabled(True)

    # ── Remove ──

    def _remove(self) -> None:
        report = self._report
        if report is None or not report.packages:
            return

        listing = "\n".join(
            f"  {p.published}   {p.original}   {p.version_text}"
            for p in report.packages)
        box = QMessageBox(self)
        box.setWindowTitle("Remove superseded driver packages")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            f"About to remove {len(report.packages)} superseded driver "
            f"package(s) with <b>pnputil /delete-driver</b>.<br><br>"
            "Each one has been replaced by a newer version of the same "
            "driver. Removing them means those older versions can no longer "
            "be rolled back to.")
        box.setDetailedText(listing)
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return

        packages = list(report.packages)
        self._remove_btn.setEnabled(False)
        self._analyze_btn.setEnabled(False)
        self._output.append("\nRemoving…")

        def _run(worker):
            import subprocess

            removed, failed = [], []
            for package in packages:
                if worker.is_cancelled:
                    break
                command = driver_store.removal_command(package)
                try:
                    proc = subprocess.run(
                        command, capture_output=True, text=True,
                        errors="replace", timeout=120,
                        creationflags=driver_store.CREATE_NO_WINDOW)
                except (OSError, subprocess.SubprocessError) as exc:
                    failed.append((package.published, str(exc)))
                    continue
                # pnputil reports failure in its output as well as its code,
                # so both are consulted rather than trusting rc alone.
                output = (proc.stdout or "") + (proc.stderr or "")
                if proc.returncode == 0 and "failed" not in output.lower():
                    removed.append(package.published)
                else:
                    failed.append((package.published, output.strip()
                                   or f"exit {proc.returncode}"))
            return removed, failed

        def _done(result):
            if not widget_is_valid(self):
                return
            removed, failed = result
            self._analyze_btn.setEnabled(True)
            self._output.append(f"Removed {len(removed)} package(s).")
            for name, why in failed:
                self._output.append(f"  {name}: {why}")
            logger.info("Driver store prune: %d removed, %d failed",
                        len(removed), len(failed))

        def _err(message: str):
            if not widget_is_valid(self):
                return
            self._analyze_btn.setEnabled(True)
            self._output.append(f"Error: {message}")

        self._worker = Worker(_run)
        self._worker.signals.result.connect(_done)
        self._worker.signals.error.connect(_err)
        self._pool.start(self._worker)

    def _cancel_all(self) -> None:
        if self._worker is not None:
            retire([self._worker])  # held until its finished is delivered
            self._worker = None
