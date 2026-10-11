"""Free space on every fixed drive and what the last clean did -- the two
numbers a person checks before and after cleaning. Measured, never estimated."""
import logging
import shutil
from typing import List, Tuple

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from core.formatting import format_size
from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.cleanup import cleanup_history
from modules.cleanup.cleanup_scanner import drives

logger = logging.getLogger(__name__)


def drive_lines() -> List[Tuple[str, int, int]]:
    out = []
    for root in drives.fixed_drive_roots():
        try:
            usage = shutil.disk_usage(root)
            out.append((root, usage.free, usage.total))
        except OSError as exc:
            logger.warning("cannot read free space on %s: %s", root, exc)
    return out


class DriveSummaryBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bars: List[QProgressBar] = []
        self._workers: list = []
        self._generation = 0
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._row = QHBoxLayout()
        self._layout.addLayout(self._row)
        self.last = QLabel()
        self.last.setWordWrap(True)
        self._layout.addWidget(self.last)

    def cancel(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self._generation += 1

    def refresh(self) -> None:
        """Free space is read on a worker (a sleeping HDD blocks for seconds);
        the last-clean label is a small JSON file and stays synchronous."""
        self._refresh_last()
        self.cancel()
        generation = self._generation
        worker = Worker(lambda _w: drive_lines())
        worker.signals.result.connect(lambda lines, g=generation: self._deliver(g, lines))
        worker.signals.error.connect(
            lambda msg: logger.warning("drive free-space read failed: %s", msg))
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _deliver(self, generation: int, lines) -> None:
        if not widget_is_valid(self) or generation != self._generation:
            return
        self._show_drives(lines)

    def _show_drives(self, lines) -> None:
        for bar in self.bars:
            self._row.removeWidget(bar)
            bar.deleteLater()
        self.bars = []
        for root, free, total in lines:
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(round(100 * (total - free) / total) if total else 0)
            bar.setFormat(f"{root.rstrip(chr(92))}  {format_size(free)} free of {format_size(total)}")
            bar.setTextVisible(True)
            self._row.addWidget(bar)
            self.bars.append(bar)

    def _refresh_last(self) -> None:
        recent = cleanup_history.recent_deleted(limit=1)
        if recent:
            when = recent[0].get("at", "").replace("T", " ")
            self.last.setText(f"Last clean: {cleanup_history.summarise(recent[0])}  ({when})")
        else:
            self.last.setText("No clean recorded yet.")
