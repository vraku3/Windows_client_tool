"""Free space on every fixed drive and what the last clean did -- the two
numbers a person checks before and after cleaning. Measured, never estimated."""
import logging
import shutil
from typing import List, Tuple

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from core.formatting import format_size
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
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._row = QHBoxLayout()
        self._layout.addLayout(self._row)
        self.last = QLabel()
        self.last.setWordWrap(True)
        self._layout.addWidget(self.last)

    def refresh(self) -> None:
        for bar in self.bars:
            self._row.removeWidget(bar)
            bar.deleteLater()
        self.bars = []
        for root, free, total in drive_lines():
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(round(100 * (total - free) / total) if total else 0)
            bar.setFormat(f"{root.rstrip(chr(92))}  {format_size(free)} free of {format_size(total)}")
            bar.setTextVisible(True)
            self._row.addWidget(bar)
            self.bars.append(bar)
        recent = cleanup_history.recent_deleted(limit=1)
        if recent:
            when = recent[0].get("at", "").replace("T", " ")
            self.last.setText(f"Last clean: {cleanup_history.summarise(recent[0])}  ({when})")
        else:
            self.last.setText("No clean recorded yet.")
