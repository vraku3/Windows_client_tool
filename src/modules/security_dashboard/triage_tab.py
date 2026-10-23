"""Security Dashboard's Incident Triage tab -- the Qt side of `incident_triage`.

Findings, worst first, each with its count, first/last time and evidence;
the detail pane says what it means and where to fix it. Reads run on a
Worker (~0.6 s here elevated); a log that could not be read is an Unknown
finding the engine produced, never an empty tree.
"""
from __future__ import annotations

from ui.help.debloat_widgets import explain
from . import help_text as help_info

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QProgressBar, QPushButton, QSplitter, QTextBrowser, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from core.table_ui import set_role
from core.widget_life import widget_is_valid
from core.worker import Worker

from . import incident_triage as it

logger = logging.getLogger(__name__)

_SEVERITY_LABEL = {it.HIGH: "High", it.MEDIUM: "Medium", it.UNKNOWN: "Unknown", it.INFO: "Info"}
_SEVERITY_MEANING = {it.HIGH: "error", it.MEDIUM: "warning", it.UNKNOWN: "info", it.INFO: "success"}
_MAX_EVIDENCE_ROWS = 200
_DAYS = ((1, "Last 24 hours"), (7, "Last 7 days"), (30, "Last 30 days"), (90, "Last 90 days"))
_FINDING_ROLE = Qt.ItemDataRole.UserRole


class TriageTab(QWidget):
    """Incident triage: did anyone get in, and what changed?"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._workers: List[Worker] = []
        self._result: Optional[it.Triage] = None
        self.loaded = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(self._build_bar())
        self._progress = QProgressBar(self)
        self._progress.setFixedHeight(3)
        self._progress.setRange(0, 0)
        self._progress.hide()
        lay.addWidget(self._progress)
        self._summary = QLabel("Reads the Security, System, Defender, PowerShell and Task "
                               "Scheduler logs for signs of intrusion and change.", self)
        self._summary.setWordWrap(True)
        set_role(self._summary, "statusInfo")
        lay.addWidget(self._summary)
        split = QSplitter(Qt.Orientation.Vertical, self)
        self._tree = QTreeWidget(self)
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["Severity", "Finding", "Count", "Last seen"])
        header = self._tree.header()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setAlternatingRowColors(True)
        self._tree.currentItemChanged.connect(lambda *_: self._show_detail())
        self._detail = QTextBrowser(self)
        self._detail.setPlaceholderText("Select a finding to see what it means and where to fix it.")
        split.addWidget(self._tree)
        split.addWidget(self._detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)
        explain(self, help_info.CONTROLS, help_info.HEADERS, intro=help_info.PANES["Incident Triage"])

    def _build_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._days = QComboBox(self)
        for days, label in _DAYS:
            self._days.addItem(label, days)
        self._days.setCurrentIndex(2)
        self._days.currentIndexChanged.connect(lambda _i: self.load())
        refresh = QPushButton("Refresh", self)
        refresh.clicked.connect(self.load)
        self._copy = QPushButton("Copy report", self)
        self._copy.setEnabled(False)
        self._copy.clicked.connect(self._copy_report)
        bar.addWidget(QLabel("Window:", self))
        bar.addWidget(self._days)
        bar.addWidget(refresh)
        bar.addWidget(self._copy)
        bar.addStretch(1)
        return bar

    # ---- loading ------------------------------------------------------------------------------
    def load(self) -> None:
        if not widget_is_valid(self._progress):
            return
        self.loaded = True
        self._cancel_all()
        days = int(self._days.currentData() or 30)
        self._progress.show()
        self._summary.setText(f"Reading the logs for the last {days} days…")
        w = Worker(lambda _w: it.triage(days))
        w.signals.result.connect(self._loaded)
        w.signals.error.connect(self._failed)
        w.signals.cancelled.connect(lambda: widget_is_valid(self._progress) and self._progress.hide())
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _failed(self, err) -> None:
        if not widget_is_valid(self._summary):
            return
        self._progress.hide()
        message = str(err[1]) if isinstance(err, tuple) and len(err) > 1 else str(err)
        logger.error("incident triage failed: %s", message)
        self._summary.setText(f"Triage could not run: {message}")

    def _loaded(self, result: it.Triage) -> None:
        if not widget_is_valid(self._tree):
            return
        self._progress.hide()
        self._result = result
        self._copy.setEnabled(True)
        self._summary.setText(it.summary(result))
        self._tree.clear()
        for finding in result.findings:
            self._tree.addTopLevelItem(self._item(finding))
        if self._tree.topLevelItemCount():
            self._tree.setCurrentItem(self._tree.topLevelItem(0))

    def _item(self, f: it.Finding) -> QTreeWidgetItem:
        last = f.last.astimezone().strftime("%Y-%m-%d %H:%M") if f.last else ""
        item = QTreeWidgetItem([_SEVERITY_LABEL.get(f.severity, f.severity), f.title,
                                str(f.count) if f.count else "", last])
        item.setForeground(0, QColor(semantic(_SEVERITY_MEANING.get(f.severity, "info"))))
        item.setData(0, _FINDING_ROLE, f)
        for column in range(4):
            item.setToolTip(column, f"{f.title}: {f.detail}\n{f.fix}")
        for line in f.evidence[:_MAX_EVIDENCE_ROWS]:
            child = QTreeWidgetItem(["", line, "", ""])
            child.setToolTip(1, line)
            child.setData(0, _FINDING_ROLE, f)
            item.addChild(child)
        if len(f.evidence) > _MAX_EVIDENCE_ROWS:
            item.addChild(QTreeWidgetItem(["", f"(+{len(f.evidence) - _MAX_EVIDENCE_ROWS} more -- "
                                               "Copy report has all of them)", "", ""]))
        return item

    def _show_detail(self) -> None:
        item = self._tree.currentItem()
        f = item.data(0, _FINDING_ROLE) if item is not None else None
        if f is None:
            self._detail.clear()
            return
        from html import escape
        when = ""
        if f.first and f.last:
            when = (f"<p><i>{f.count} event(s), first {f.first.astimezone():%Y-%m-%d %H:%M}, "
                    f"last {f.last.astimezone():%Y-%m-%d %H:%M} -- {escape(f.source)}</i></p>")
        fix = f"<p><b>What to do:</b> {escape(f.fix)}</p>" if f.fix else ""
        self._detail.setHtml(f"<h3>{escape(f.title)}</h3>{when}<p>{escape(f.detail)}</p>{fix}")

    def _copy_report(self) -> None:
        if self._result is not None:
            QApplication.clipboard().setText(it.as_text(self._result))

    # ---- lifetime -----------------------------------------------------------------------------
    def _cancel_all(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        if widget_is_valid(self._progress):
            self._progress.hide()
