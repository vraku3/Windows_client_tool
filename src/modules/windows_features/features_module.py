import subprocess
import threading
from typing import Callable, List, Tuple, Optional

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTreeWidget, QTreeWidgetItem, QSplitter, QPlainTextEdit, QLabel,
    QLineEdit, QProgressBar, QHeaderView, QMessageBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.windows_utils import is_reboot_pending
import logging
logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

PINNED_FEATURES = {
    "Microsoft-Hyper-V-All", "VirtualMachinePlatform",
    "Microsoft-Windows-Subsystem-Linux", "Containers-DisposableClientVM",
    "IIS-WebServerRole", "TelnetClient", "TFTP",
    "ServicesForNFS-ClientOnly", "DirectPlay",
}


def _parse_features(output: str) -> List[Tuple[str, str]]:
    """Parse dism /get-features /format:table output. Returns [(name, state)]."""
    features = []
    for line in output.splitlines():
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2 and parts[0] and parts[0] != "Feature Name":
                features.append((parts[0], parts[1]))
    return features


def _get_feature_info(name: str) -> str:
    """Get detailed info for a feature."""
    result = subprocess.run(
        ["dism", "/online", "/get-featureinfo", f"/featurename:{name}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW, timeout=30,
    )
    return result.stdout + result.stderr


def _fetch_all_features() -> List[Tuple[str, str]]:
    """List the optional features, or say why the list could not be read.

    DISM needs elevation, and unelevated it exits **740** with "Elevated
    permissions are required to run DISM." on STDOUT. This ignored
    `returncode` entirely and handed that text to `_parse_features`, which
    keeps only lines containing "|", found none, and returned an empty list --
    rendered by the pane as the flat assertion "0 features - 0 enabled".

    An empty answer from a command that was refused is not an empty machine.
    """
    result = subprocess.run(
        ["dism", "/online", "/get-features", "/format:table"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW, timeout=120,
    )
    if result.returncode != 0:
        combined = (result.stdout or "") + (result.stderr or "")
        if "740" in combined or "elevated permissions" in combined.lower():
            raise PermissionError("Requires administrator")
        raise RuntimeError(
            f"DISM failed (exit {result.returncode})")
    return _parse_features(result.stdout)


def _run_dism_action(args: List[str], output_cb, is_cancelled: Callable[[], bool]) -> int:
    """Run a DISM enable/disable and make Worker.cancel() actually reach it.

    `Worker.cancel()` only flips a flag -- it never touches this function's
    own `proc`, so without this, cancelling the worker (e.g. the app closing
    while DISM is contended, a real, independently-measured 25s+ lock
    elsewhere in this codebase) left DISM running to completion regardless,
    with no way to stop it. A side thread polls `is_cancelled` and kills the
    whole process tree (matching `core/appx_service.py::_run_ps_bounded`'s
    established pattern) rather than restructuring the blocking stdout read.
    """
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )

    stop_watching = threading.Event()

    def _watch_for_cancel() -> None:
        while not stop_watching.wait(0.5):
            if is_cancelled():
                logger.warning("DISM action cancelled -- killing process tree (pid %s)", proc.pid)
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW,
                )
                return

    watcher = threading.Thread(target=_watch_for_cancel, daemon=True)
    watcher.start()
    try:
        for line in proc.stdout:
            output_cb(line.rstrip())
        proc.wait()
        return proc.returncode
    finally:
        stop_watching.set()


def _enable_feature(name: str, output_cb, is_cancelled: Callable[[], bool] = lambda: False) -> int:
    return _run_dism_action(
        ["dism", "/online", "/enable-feature", f"/featurename:{name}", "/norestart"],
        output_cb, is_cancelled,
    )


def _disable_feature(name: str, output_cb, is_cancelled: Callable[[], bool] = lambda: False) -> int:
    return _run_dism_action(
        ["dism", "/online", "/disable-feature", f"/featurename:{name}", "/norestart"],
        output_cb, is_cancelled,
    )


class WindowsFeaturesModule(BaseModule):
    name = "Windows Features"
    icon = "🧩"
    description = "Enable or disable Windows optional features"
    requires_admin = True
    group = ModuleGroup.MANAGE

    def __init__(self):
        super().__init__()
        self._workers: list = []

    def create_widget(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        # Reboot banner
        reboot_banner = QLabel("⚠ A system reboot is pending.")
        reboot_banner.setStyleSheet(
            "background:#FF8800;color:white;padding:4px;font-weight:bold;"
        )
        try:
            reboot_banner.setVisible(is_reboot_pending())
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)
            reboot_banner.hide()
        layout.addWidget(reboot_banner)

        # Toolbar
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        filter_edit = QLineEdit()
        filter_edit.setPlaceholderText("Filter features...")
        status_lbl = QLabel("Click Refresh to load features.")
        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(filter_edit, 1)
        toolbar.addStretch()
        toolbar.addWidget(status_lbl)
        layout.addLayout(toolbar)

        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setFixedHeight(4)
        progress.hide()
        layout.addWidget(progress)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # Left: feature tree
        tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Feature Name", "State"])
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(tree)

        # Right: detail panel
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        detail_lbl = QLabel("Select a feature to see details.")
        detail_lbl.setWordWrap(True)
        right_layout.addWidget(detail_lbl)

        action_row = QHBoxLayout()
        enable_btn = QPushButton("Enable")
        disable_btn = QPushButton("Disable")
        enable_btn.setEnabled(False)
        disable_btn.setEnabled(False)
        action_row.addWidget(enable_btn)
        action_row.addWidget(disable_btn)
        action_row.addStretch()
        right_layout.addLayout(action_row)

        output_view = QPlainTextEdit()
        output_view.setReadOnly(True)
        output_view.setFont(QFont("Consolas", 8))
        right_layout.addWidget(output_view, 1)
        splitter.addWidget(right)
        splitter.setSizes([400, 600])

        features_ref: list = [[]]   # [List[Tuple[str, str]]]
        selected_name_ref: list = [None]

        def _color_item(item: QTreeWidgetItem, state: str) -> None:
            color = QColor("#27AE60") if "Enable" in state else QColor("#888888")
            for col in range(2):
                item.setForeground(col, color)

        def populate(features: List[Tuple[str, str]], filter_text: str = "") -> None:
            ft = filter_text.lower()
            tree.clear()
            pinned = []
            rest = []
            for feat_name, state in features:
                if ft and ft not in feat_name.lower():
                    continue
                if feat_name in PINNED_FEATURES:
                    pinned.append((feat_name, state))
                else:
                    rest.append((feat_name, state))

            if pinned:
                pin_hdr = QTreeWidgetItem(tree, ["★ Common Features", ""])
                pin_hdr.setFlags(pin_hdr.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                for feat_name, state in pinned:
                    item = QTreeWidgetItem(pin_hdr, [feat_name, state])
                    _color_item(item, state)
                tree.expandItem(pin_hdr)

            for feat_name, state in rest:
                item = QTreeWidgetItem(tree, [feat_name, state])
                _color_item(item, state)

        def load_features() -> None:
            refresh_btn.setEnabled(False)
            progress.show()
            status_lbl.setText("Running DISM...")
            tree.clear()

            worker = Worker(lambda _w: _fetch_all_features())

            def on_result(features: List[Tuple[str, str]]) -> None:
                features_ref[0] = features
                refresh_btn.setEnabled(True)
                progress.hide()
                populate(features, filter_edit.text())
                enabled = sum(1 for _, s in features if "Enabled" in s)
                status_lbl.setText(f"{len(features)} features — {enabled} enabled")
                try:
                    reboot_banner.setVisible(is_reboot_pending())
                except Exception:
                    logger.warning("Ignored Exception", exc_info=True)

            def on_error(err: str) -> None:
                refresh_btn.setEnabled(True)
                progress.hide()
                # "Requires administrator" is the whole answer; prefixing it
                # with "Error:" reads as a malfunction rather than a refusal.
                status_lbl.setText(
                    err if "administrator" in err.lower() else f"Error: {err}")

            worker.signals.result.connect(on_result)
            worker.signals.error.connect(on_error)
            self._workers.append(worker)
            self.thread_pool.start(worker)

        def on_item_clicked(item: QTreeWidgetItem, col: int) -> None:
            feat_name = item.text(0)
            if feat_name.startswith("★"):
                return
            selected_name_ref[0] = feat_name
            enable_btn.setEnabled(True)
            disable_btn.setEnabled(True)
            detail_lbl.setText(f"Loading info for: {feat_name}...")
            output_view.clear()

            worker = Worker(lambda _w: _get_feature_info(feat_name))
            worker.signals.result.connect(lambda info: detail_lbl.setText(info[:500]))
            worker.signals.error.connect(lambda e: detail_lbl.setText(f"Error: {e}"))
            self._workers.append(worker)
            self.thread_pool.start(worker)

        def _run_feature_action(action_fn, action_name: str) -> None:
            feat_name = selected_name_ref[0]
            if not feat_name:
                return
            enable_btn.setEnabled(False)
            disable_btn.setEnabled(False)
            output_view.clear()
            status_lbl.setText(f"{action_name}: {feat_name}...")

            def run(_w):
                def safe_append(line: str):
                    _w.signals.log_line.emit(line)
                return action_fn(feat_name, safe_append, lambda: _w.is_cancelled)

            worker = Worker(run)
            worker.signals.log_line.connect(output_view.appendPlainText)

            def on_done(_) -> None:
                enable_btn.setEnabled(True)
                disable_btn.setEnabled(True)
                status_lbl.setText(f"{action_name} complete.")
                try:
                    reboot_banner.setVisible(is_reboot_pending())
                except Exception:
                    logger.warning("Ignored Exception", exc_info=True)
                load_features()

            def on_error(err: str) -> None:
                enable_btn.setEnabled(True)
                disable_btn.setEnabled(True)
                status_lbl.setText(f"Error: {err}")

            worker.signals.result.connect(on_done)
            worker.signals.error.connect(on_error)
            self._workers.append(worker)
            self.thread_pool.start(worker)

        # Wire up signals
        refresh_btn.clicked.connect(load_features)
        tree.itemClicked.connect(on_item_clicked)
        filter_edit.textChanged.connect(lambda txt: populate(features_ref[0], txt))

        def _confirm_enable():
            feat_name = selected_name_ref[0]
            if not feat_name:
                return
            reply = QMessageBox.warning(
                w, "Change Windows Feature",
                f"Enable '{feat_name}'?\n\nThis requires administrator privileges.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                _run_feature_action(_enable_feature, "Enable")

        def _confirm_disable():
            feat_name = selected_name_ref[0]
            if not feat_name:
                return
            reply = QMessageBox.warning(
                w, "Change Windows Feature",
                f"Disable '{feat_name}'?\n\nThis requires administrator privileges.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                _run_feature_action(_disable_feature, "Disable")

        enable_btn.clicked.connect(_confirm_enable)
        disable_btn.clicked.connect(_confirm_disable)

        return w

    def get_refresh_interval(self) -> Optional[int]:
        # Deliberately no auto-refresh: load_features() shells out to
        # `dism /online /get-features` with a 120s timeout, and this
        # codebase has already measured what a periodic image-servicing-tool
        # poll costs (see the Cleanup Module notes on scan_winsxs_cleanup --
        # 25s elevated, holding the Windows servicing lock). Refreshing this
        # tab is left to the user's own Refresh click.
        return None

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.cancel_all_workers()
