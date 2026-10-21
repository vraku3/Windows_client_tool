"""Free space on every fixed drive and what the last clean did -- the two
numbers a person checks before and after cleaning. Measured, never estimated."""
import logging
import shutil
from typing import List, Tuple

from PyQt6.QtCore import QThreadPool, Qt, QSize, QRect, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from core.formatting import format_size
from core.widget_life import widget_is_valid
from core.worker import Worker, retire
from core.semantic_colors import chrome, semantic
from core.table_ui import set_role
from modules.cleanup import cleanup_history
from modules.cleanup.cleanup_scanner import drives

logger = logging.getLogger(__name__)


class _LastCleanLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ''
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(32)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Last clean; open History')
        set_role(self, 'muted')

    def setText(self, text):
        self._full_text = text
        self.setToolTip(text + '\nClick or press Enter to open History.')
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, int(max(0, self.width()))))

    def minimumSizeHint(self):
        return QSize(32, self.fontMetrics().height() + 4)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class _DriveBar(QProgressBar):
    """Compact meter, with colours resolved at paint time for theme switches."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QColor(chrome('text')))
        painter.drawText(QRect(0, 0, int(self.width()), 22),
                         int(Qt.AlignmentFlag.AlignCenter), self.format())
        track = QRect(2, 25, int(max(0, self.width() - 4)), 5)
        painter.fillRect(track, QColor(chrome('outline')))
        painter.fillRect(QRect(track.x(), track.y(), int(track.width() * self.value() / 100), 5),
                         QColor(semantic(self.property('severity') or 'success')))


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
        self.last = _LastCleanLabel()
        self._layout.addWidget(self.last)

    def cancel(self) -> None:
        retire(self._workers)       # held until delivered: see core.worker.retire
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
            bar = _DriveBar()
            fraction = free / total if total else 1
            severity = ('error' if fraction < .05 or free < 10 * 1024 ** 3
                        else 'warning' if fraction < .15 else 'success')
            bar.setProperty('severity', severity)
            bar.setFixedSize(120, 34)
            bar.setRange(0, 100)
            bar.setValue(round(100 * (total - free) / total) if total else 0)
            bar.setFormat(f"{root.rstrip(chr(92))} {format_size(free)} free")
            bar.setToolTip(f"{root}: {free:,} bytes free of {total:,} bytes "
                           f"({fraction:.1%} free). Warning below 15%; critical below 5% or 10 GB.")
            bar.setAccessibleName(f"Free space on {root}")
            bar.setAccessibleDescription(bar.toolTip())
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
