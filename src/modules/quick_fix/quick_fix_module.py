from collections import OrderedDict
from typing import Dict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QFrame, QScrollArea, QGridLayout, QMessageBox, QLineEdit, QDialog,
    QListWidget,
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
        if self._worker is None:
            # cancel() already put this card back to its resting state and
            # recorded "cancelled" -- a result arriving after that is the
            # cancelled command finishing late in the background, not a
            # second real outcome to report.
            return
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._status.setText("")
        from modules.quick_fix import quick_fix_history
        quick_fix_history.record(self._action.title, "ok")

    def _on_error(self, error_str: str):
        if self._worker is None:
            # Same race as _on_done: cancel() already reset this card and
            # recorded "cancelled", so a late error from the abandoned
            # worker must not overwrite it with a second, contradictory
            # history entry.
            return
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._status.setText("")
        self._output.appendPlainText(f"ERROR: {error_str}")
        from modules.quick_fix import quick_fix_history
        quick_fix_history.record(self._action.title, "error")

    def cancel(self) -> None:
        """Cancel the running worker if any."""
        if self._worker is not None and self._running:
            self._worker.cancel()
            self._running = False
            self._worker = None
            self._run_btn.setEnabled(True)
            self._output.appendPlainText("Cancelled.")
            from modules.quick_fix import quick_fix_history
            quick_fix_history.record(self._action.title, "cancelled")


class QuickFixHistoryDialog(QDialog):
    """Read-only browser over the local Quick Fix run history."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Fix History")
        self.resize(520, 420)
        root = QVBoxLayout(self)

        self._list = QListWidget()
        from modules.quick_fix import quick_fix_history
        entries = quick_fix_history.recent(limit=20)
        if entries:
            for entry in entries:
                self._list.addItem(
                    f"{entry['at']} — {entry['action']}: {entry['outcome']}")
        else:
            self._list.addItem("No actions run yet.")
        root.addWidget(self._list)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)


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
        self._category_headers: Dict[str, QLabel] = {}

    def create_widget(self) -> QWidget:
        outer = QWidget()
        self._widget = outer
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # View History row
        history_row = QHBoxLayout()
        history_row.addStretch()
        history_btn = QPushButton("View History")
        history_btn.clicked.connect(self._show_history)
        history_row.addWidget(history_btn)
        outer_layout.addLayout(history_row)

        # Reboot banner (hidden by default)
        self._reboot_banner = QLabel("⚠ A system reboot is pending.")
        self._reboot_banner.setStyleSheet(
            "background: #FF8800; color: white; padding: 4px; font-weight: bold;"
        )
        self._reboot_banner.hide()
        outer_layout.addWidget(self._reboot_banner)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search repair actions…")
        self._search.textChanged.connect(self._apply_filter)
        outer_layout.addWidget(self._search)

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
        self._category_headers.clear()
        for cat_name, actions in categories.items():
            hdr = QLabel(cat_name)
            hdr_font = hdr.font()
            hdr_font.setBold(True)
            _pt = hdr_font.pointSize()
            if _pt > 0:
                hdr_font.setPointSize(_pt + 1)
            hdr.setFont(hdr_font)
            content_layout.addWidget(hdr)
            self._category_headers[cat_name] = hdr

            grid = QGridLayout()
            grid.setSpacing(8)
            for i, action in enumerate(actions):
                card = _FixCard(action)
                card._category = cat_name
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

    def _show_history(self) -> None:
        QuickFixHistoryDialog(self._widget).exec()

    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        visible_by_category: Dict[str, bool] = {name: False for name in self._category_headers}
        for card in self._cards:
            action = card._action
            matches = (not query
                      or query in action.title.lower()
                      or query in action.description.lower())
            card.setVisible(matches)
            if matches:
                visible_by_category[card._category] = True
        for cat_name, hdr in self._category_headers.items():
            hdr.setVisible(visible_by_category[cat_name])

    def on_deactivate(self) -> None:
        for card in self._cards:
            card.cancel()
        self._workers.clear()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.on_deactivate()
        self.cancel_all_workers()
