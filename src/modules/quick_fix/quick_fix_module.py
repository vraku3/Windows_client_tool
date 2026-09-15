from collections import OrderedDict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QFrame, QScrollArea, QGridLayout, QMessageBox, QLineEdit,
)
from PyQt6.QtCore import QThreadPool, pyqtSignal
from PyQt6.QtGui import QFont

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.windows_utils import is_reboot_pending
from core.long_op_pool import get_long_op_pool
from modules.quick_fix.fix_actions import ALL_ACTIONS, FixAction
import logging
logger = logging.getLogger(__name__)


class _FixCard(QFrame):
    _line = pyqtSignal(str)   # marshals output to main thread

    def __init__(self, action: FixAction, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._action = action
        self._running = False
        self._worker = None   # track for cancellation
        self._thread_pool = QThreadPool.globalInstance()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Title row
        title_row = QHBoxLayout()
        title_lbl = QLabel(self._action.title)
        font = title_lbl.font()
        font.setBold(True)
        title_lbl.setFont(font)
        title_row.addWidget(title_lbl)
        if self._action.reboot_required:
            badge = QLabel("⚠ Reboot required")
            badge.setStyleSheet("color: orange;")
            title_row.addStretch()
            title_row.addWidget(badge)
        layout.addLayout(title_row)

        # Description
        desc = QLabel(self._action.description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)

        # Run button
        self._run_btn = QPushButton("Run")
        self._run_btn.setFixedWidth(80)
        layout.addWidget(self._run_btn)

        # Status label
        self._status = QLabel("")
        self._status.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._status)

        # Output
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setMaximumHeight(150)
        mono_font = QFont("Consolas", 8)
        self._output.setFont(mono_font)
        self._output.hide()
        layout.addWidget(self._output)

        self._run_btn.clicked.connect(self._run)
        self._line.connect(self._output.appendPlainText)

    def _run(self):
        if self._running:
            return
        action = self._action

        if action.precondition is not None:
            msg = action.precondition()
            if msg is not None:
                self._status.setText(msg)
                return

        if action.confirm_text is not None:
            mb = QMessageBox(self)
            mb.setWindowTitle(action.title)
            mb.setIcon(QMessageBox.Icon.Warning)
            mb.setText(action.confirm_text)
            mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if mb.exec() != QMessageBox.StandardButton.Ok:
                return

        self._running = True
        self._run_btn.setEnabled(False)
        self._output.clear()
        self._output.show()
        self._status.setText("Running...")

        def append(line: str):
            self._line.emit(line)

        def do_work(_w):
            self._worker = _w
            action.fn(append)

        self._worker = Worker(do_work)
        self._worker.signals.result.connect(lambda _r: self._on_done())
        self._worker.signals.error.connect(self._on_error)
        pool = get_long_op_pool() if action.long_running else self._thread_pool
        pool.start(self._worker)

    def _on_done(self):
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._status.setText("")

    def _on_error(self, error_str: str):
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._status.setText("")
        self._output.appendPlainText(f"ERROR: {error_str}")

    def cancel(self) -> None:
        """Cancel the running worker if any."""
        if self._worker is not None and self._running:
            self._worker.cancel()
            self._running = False
            self._worker = None
            self._run_btn.setEnabled(True)
            self._output.appendPlainText("Cancelled.")


class QuickFixModule(BaseModule):
    name = "Quick Fix"
    icon = "🔧"
    description = "One-click system repair and maintenance tools"
    requires_admin = True
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._cards: list = []
        self._workers: list = []

    def create_widget(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Reboot banner (hidden by default)
        self._reboot_banner = QLabel("⚠ A system reboot is pending.")
        self._reboot_banner.setStyleSheet(
            "background: #FF8800; color: white; padding: 4px; font-weight: bold;"
        )
        self._reboot_banner.hide()
        outer_layout.addWidget(self._reboot_banner)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(12)
        content_layout.setContentsMargins(8, 8, 8, 8)

        # Group actions by category preserving order
        categories: OrderedDict = OrderedDict()
        for action in ALL_ACTIONS:
            categories.setdefault(action.category, []).append(action)

        self._cards.clear()
        for cat_name, actions in categories.items():
            hdr = QLabel(cat_name)
            hdr_font = hdr.font()
            hdr_font.setBold(True)
            _pt = hdr_font.pointSize()
            if _pt > 0:
                hdr_font.setPointSize(_pt + 1)
            hdr.setFont(hdr_font)
            content_layout.addWidget(hdr)

            grid = QGridLayout()
            grid.setSpacing(8)
            for i, action in enumerate(actions):
                card = _FixCard(action)
                self._cards.append(card)
                grid.addWidget(card, i // 2, i % 2)
            content_layout.addLayout(grid)

        content_layout.addStretch()
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        # Check reboot status
        try:
            if is_reboot_pending():
                self._reboot_banner.show()
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)

        return outer

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        for card in self._cards:
            card.cancel()
        self._workers.clear()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.on_deactivate()
        self.cancel_all_workers()
