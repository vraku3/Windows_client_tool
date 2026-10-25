"""Choose an automatic backup and approve its exact, typed transaction plan."""
import logging
from functools import partial

from PyQt6 import sip
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QLabel,
                             QPlainTextEdit, QVBoxLayout)

from core.worker import Worker, retire, track
from .engine.backups import describe_plan

logger = logging.getLogger(__name__)


class BackupDialog(QDialog):
    def __init__(self, engine, guid, pool, parent=None):
        super().__init__(parent)
        self.engine, self.guid, self.pool = engine, guid, pool
        self._workers, self._generation = [], 0
        self.path, self.plan = None, None
        self.setWindowTitle("Restore navigation entry backup")
        self.resize(850, 600)
        layout = QVBoxLayout(self)
        self.backups = QComboBox()
        self.backups.setToolTip("Choose an automatic backup belonging to this entry's GUID.")
        layout.addWidget(self.backups)
        self.status = QLabel("Reading backups...")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setToolTip("Exact additions, replacements and deletions: before values, new values, registry types and junction paths.")
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Restore this backup")
        self.ok.setToolTip("Apply this preview through the recovery journal and verify every registry write.")
        self.ok.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip("Close without restoring anything.")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.backups.currentIndexChanged.connect(self.choose)
        self._run(partial(engine.list_backups, guid), self.listed)

    def _run(self, function, callback):
        generation = self._generation
        worker = Worker(lambda w: function())
        track(worker)
        self._workers.append(worker)
        def result(value):
            if not sip.isdeleted(self) and generation == self._generation:
                callback(value)
        def error(message):
            logger.warning("Navigation backup preview failed: %s", message)
            if not sip.isdeleted(self) and generation == self._generation:
                self.status.setText("Could not read backup: " + str(message))
        worker.signals.result.connect(result)
        worker.signals.error.connect(error)
        worker.signals.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self.pool.start(worker)

    def listed(self, paths):
        for path in paths:
            self.backups.addItem(path.parent.name, path)
        if not paths:
            self.status.setText("No automatic backups for this entry yet. Backups are saved before edits and removal.")

    def choose(self):
        self._generation += 1
        retire(self._workers)
        self.ok.setEnabled(False)
        self.path, self.plan = self.backups.currentData(), None
        self.preview.clear()
        self.status.setText("Preparing exact changes...")
        if self.path:
            self._run(partial(self.engine.preview_restore, self.guid, self.path), self.previewed)

    def previewed(self, plan):
        self.plan = plan
        self.preview.setPlainText(describe_plan(plan))
        self.status.setText("Review every change below. Existing values may be replaced or deleted. Restore applies only this entry.")
        self.ok.setEnabled(True)

    def accept(self):
        if self.path is not None and self.plan is not None and self.ok.isEnabled():
            super().accept()

    def done(self, result):
        self._generation += 1
        retire(self._workers)
        super().done(result)
