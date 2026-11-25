"""Enterprise UI components for the Quick Fix module overhaul.

Provides interactive Category Filtering Chips, Pre-Flight Health Banner,
Triage Playbook Ribbon/Cards, and slide-out / collapsible Console Drawer.
"""

from typing import Dict, List, Optional
import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.table_ui import set_role
from modules.quick_fix.quick_fix_playbooks import Playbook, PLAYBOOKS
from modules.quick_fix.quick_fix_scanner import QuickFixHealthReport

logger = logging.getLogger(__name__)


class _CategoryChipsBar(QWidget):
    """Horizontal category filter chips bar with checkable buttons."""

    category_changed = pyqtSignal(str)

    CATEGORIES = [
        ("all", "All"),
        ("system", "System Repairs"),
        ("network", "Network"),
        ("wu", "Windows Update"),
        ("cache", "Cache & UI"),
        ("print", "Print"),
        ("cleanup", "Cleanup"),
        ("attention", "⚠️ Needs Attention"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: Dict[str, QPushButton] = {}
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        for cat_id, cat_name in self.CATEGORIES:
            btn = QPushButton(cat_name)
            btn.setCheckable(True)
            if cat_id == "all":
                btn.setChecked(True)
            btn.clicked.connect(lambda _c, cid=cat_id: self._on_chip_clicked(cid))
            self._button_group.addButton(btn)
            self._buttons[cat_id] = btn
            layout.addWidget(btn)

        layout.addStretch()

    def _on_chip_clicked(self, cat_id: str) -> None:
        self.category_changed.emit(cat_id)

    def select_category(self, cat_id: str) -> None:
        btn = self._buttons.get(cat_id)
        if btn and not btn.isChecked():
            btn.setChecked(True)
            self.category_changed.emit(cat_id)

    def set_needs_attention_count(self, count: int) -> None:
        btn = self._buttons.get("attention")
        if btn:
            suffix = f" ({count})" if count > 0 else ""
            btn.setText(f"⚠️ Needs Attention{suffix}")


class _HealthScanBanner(QFrame):
    """Top-level pre-flight diagnostic status banner."""

    scan_requested = pyqtSignal()
    show_recommended_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        title_lbl = QLabel("🔍 Pre-Flight System Health & Triage")
        font = title_lbl.font()
        font.setBold(True)
        title_lbl.setFont(font)
        header_row.addWidget(title_lbl)
        header_row.addStretch()

        self._show_recommended_btn = QPushButton("★ Show Recommended (0)")
        self._show_recommended_btn.setToolTip("Filter cards to only actions recommended by the pre-flight scan.")
        self._show_recommended_btn.clicked.connect(self.show_recommended_requested.emit)
        self._show_recommended_btn.hide()
        header_row.addWidget(self._show_recommended_btn)

        self._scan_btn = QPushButton("Scan Machine")
        self._scan_btn.setToolTip("Run a fast, non-destructive check of WMI, VSS, Spooler, WinRE, and update services.")
        self._scan_btn.clicked.connect(self.scan_requested.emit)
        header_row.addWidget(self._scan_btn)
        layout.addLayout(header_row)

        self._status_lbl = QLabel("No pre-flight scan run yet. Run a check to identify system issues and highlight repairs.")
        set_role(self._status_lbl, "muted")
        layout.addWidget(self._status_lbl)

        self._details_lbl = QLabel("")
        self._details_lbl.setWordWrap(True)
        self._details_lbl.hide()
        layout.addWidget(self._details_lbl)

    def set_scanning(self, is_scanning: bool) -> None:
        self._scan_btn.setEnabled(not is_scanning)
        if is_scanning:
            self._status_lbl.setText("Scanning system components (WMI, VSS, Spooler, WinRE, BITS/WU)...")
            set_role(self._status_lbl, "muted")
        else:
            self._scan_btn.setText("Re-Scan Machine")

    def set_report(self, report: QuickFixHealthReport) -> None:
        count = report.issue_count
        if count == 0:
            self._status_lbl.setText("✔ Pre-flight check clean: no critical service, WMI, or VSS errors detected.")
            set_role(self._status_lbl, "statusSuccess")
            self._show_recommended_btn.hide()
            self._details_lbl.hide()
        else:
            self._status_lbl.setText(f"⚠ {count} actionable issue(s) detected on this system.")
            set_role(self._status_lbl, "statusWarning")
            self._show_recommended_btn.setText(f"★ Show Recommended ({len(report.recommended_keys)})")
            self._show_recommended_btn.show()

            detail_lines = [f"• {f.title}: {f.description}" for f in report.findings[:4]]
            if len(report.findings) > 4:
                detail_lines.append(f"...and {len(report.findings)-4} more findings.")
            self._details_lbl.setText("\n".join(detail_lines))
            self._details_lbl.show()


class _PlaybookCard(QFrame):
    """Compact card representing an executable multi-step Playbook."""

    run_clicked = pyqtSignal(object)

    def __init__(self, playbook: Playbook, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._playbook = playbook
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        title_lbl = QLabel(f"{self._playbook.icon} {self._playbook.title}")
        font = title_lbl.font()
        font.setBold(True)
        title_lbl.setFont(font)
        top_row.addWidget(title_lbl)
        top_row.addStretch()

        duration_lbl = QLabel(f"{len(self._playbook.action_keys)} steps • {self._playbook.estimated_duration}")
        set_role(duration_lbl, "muted")
        top_row.addWidget(duration_lbl)
        layout.addLayout(top_row)

        desc = QLabel(self._playbook.description)
        desc.setWordWrap(True)
        set_role(desc, "muted")
        layout.addWidget(desc)

        btn_row = QHBoxLayout()
        if self._playbook.reboot_required:
            reboot_badge = QLabel("⚠ Reboot required")
            set_role(reboot_badge, "statusWarning")
            btn_row.addWidget(reboot_badge)
        btn_row.addStretch()

        self._run_btn = QPushButton("Run Playbook")
        self._run_btn.setToolTip(f"Execute recipe: {self._playbook.title}")
        self._run_btn.clicked.connect(lambda: self.run_clicked.emit(self._playbook))
        btn_row.addWidget(self._run_btn)
        layout.addLayout(btn_row)

    def set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running)


class _PlaybookSection(QFrame):
    """Collapsible container displaying the 4 Triage Playbooks."""

    playbook_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._cards: List[_PlaybookCard] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header_row = QHBoxLayout()
        title_lbl = QLabel("🚀 One-Click Triage Playbooks (Multi-Step Recipes)")
        font = title_lbl.font()
        font.setBold(True)
        title_lbl.setFont(font)
        header_row.addWidget(title_lbl)
        header_row.addStretch()

        self._toggle_btn = QPushButton("Hide Playbooks")
        self._toggle_btn.clicked.connect(self._toggle_collapse)
        header_row.addWidget(self._toggle_btn)
        layout.addLayout(header_row)

        self._cards_container = QWidget()
        cards_layout = QVBoxLayout(self._cards_container)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(6)

        for pb in PLAYBOOKS:
            card = _PlaybookCard(pb)
            card.run_clicked.connect(self.playbook_requested.emit)
            self._cards.append(card)
            cards_layout.addWidget(card)

        layout.addWidget(self._cards_container)

    def _toggle_collapse(self) -> None:
        visible = not self._cards_container.isVisible()
        self._cards_container.setVisible(visible)
        self._toggle_btn.setText("Hide Playbooks" if visible else "Show Playbooks")

    def set_playbook_running(self, running: bool) -> None:
        for card in self._cards:
            card.set_running(running)


class _ConsoleDrawer(QFrame):
    """Slide-out / collapsible bottom drawer with timestamped output and timer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        title_lbl = QLabel("📟 Live Console Output")
        font = title_lbl.font()
        font.setBold(True)
        title_lbl.setFont(font)
        header_row.addWidget(title_lbl)

        self._timer_lbl = QLabel("")
        set_role(self._timer_lbl, "muted")
        header_row.addWidget(self._timer_lbl)
        header_row.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.setToolTip("Clear the console text.")
        clear_btn.clicked.connect(self.clear)
        header_row.addWidget(clear_btn)

        copy_btn = QPushButton("Copy")
        copy_btn.setToolTip("Copy console output to clipboard.")
        copy_btn.clicked.connect(self._copy_output)
        header_row.addWidget(copy_btn)

        self._toggle_btn = QPushButton("Hide Console")
        self._toggle_btn.clicked.connect(self._toggle_console)
        header_row.addWidget(self._toggle_btn)
        layout.addLayout(header_row)

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setMaximumHeight(160)
        mono_font = QFont("Consolas", 8)
        self._output.setFont(mono_font)
        layout.addWidget(self._output)

    def append_line(self, line: str) -> None:
        self._output.appendPlainText(line)
        # Ensure console is visible when new output arrives
        if not self._output.isVisible():
            self._output.show()
            self._toggle_btn.setText("Hide Console")

    def set_timer_text(self, text: str) -> None:
        self._timer_lbl.setText(text)

    def clear(self) -> None:
        self._output.clear()
        self._timer_lbl.setText("")

    def _copy_output(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(self._output.toPlainText())

    def _toggle_console(self) -> None:
        visible = not self._output.isVisible()
        self._output.setVisible(visible)
        self._toggle_btn.setText("Hide Console" if visible else "Show Console")
