"""The Overview's own widgets: metric tiles with a sparkline, the per-core heat
grid, and a "needs attention" row. Colours come from `semantic()` and the
palette, never a literal, so both themes stay readable."""
from typing import List

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QSizePolicy, QVBoxLayout, QWidget)

from core.table_ui import set_role
from core.semantic_colors import semantic
from modules.dashboard.overview_health import Finding, heat_level

_LEVEL_ROLE = {"ok": "success", "warn": "warning", "hot": "error"}
_SEVERITY_ROLE = {"critical": "error", "warning": "warning",
                  "info": "info", "unknown": None}


def level_colour(pct: float) -> QColor:
    return QColor(semantic(_LEVEL_ROLE[heat_level(pct)]))


def index_kind(kinds: List[str], index: int) -> str:
    return kinds[index] if index < len(kinds) else ""


class Sparkline(QWidget):
    """A filled line over the last readings, scaled 0..ceiling."""

    def __init__(self, ceiling: float = 100.0, parent=None) -> None:
        super().__init__(parent)
        self._values: List[float] = []
        self._ceiling = ceiling
        self._colour = QColor(semantic("info"))
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values: List[float], colour: QColor) -> None:
        self._values = values
        self._colour = colour
        self.update()

    def paintEvent(self, event) -> None:
        if len(self._values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height() - 2
        top = max(self._ceiling, max(self._values), 1e-9)
        step = w / (len(self._values) - 1)
        points = [QPointF(i * step, h - (v / top) * h) for i, v in enumerate(self._values)]
        line = QPainterPath(points[0])
        for p in points[1:]:
            line.lineTo(p)
        fill = QPainterPath(line)
        fill.lineTo(QPointF(w, h))
        fill.lineTo(QPointF(0, h))
        fill.closeSubpath()
        soft = QColor(self._colour)
        soft.setAlpha(60)
        painter.fillPath(fill, soft)
        painter.setPen(QPen(self._colour, 1.6))
        painter.drawPath(line)


class MetricTile(QFrame):
    """Title, one big number, a caption and a sparkline."""

    def __init__(self, title: str, ceiling: float = 100.0, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("overviewTile")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self._title = QLabel(title)
        set_role(self._title, "mutedTitle")
        self._value = QLabel("—")
        font = self._value.font()
        font.setPointSize(font.pointSize() + 9)
        font.setBold(True)
        self._value.setFont(font)
        self._caption = QLabel("")
        set_role(self._caption, "muted")
        self._caption.setWordWrap(True)
        self.spark = Sparkline(ceiling)
        for w in (self._title, self._value, self._caption, self.spark):
            lay.addWidget(w)

    def show_reading(self, text: str, caption: str, pct: float, history: List[float]) -> None:
        colour = level_colour(pct)
        self._value.setText(text)
        self._value.setStyleSheet(f"color: {colour.name()};")
        self._caption.setText(caption)
        self.spark.set_values(history, colour)


class CoreGrid(QWidget):
    """One small square per logical core, tinted by load. 32 bars of text
    became 32 squares you read at a glance -- and an uneven spread (one core
    pinned, the rest idle) shows up as a single hot square."""

    CELL, GAP = 26, 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loads: List[float] = []
        self._kinds: List[str] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_kinds(self, kinds: List[str]) -> None:
        """'P' / 'E' / '' per logical processor; E cores get a mark and a tooltip."""
        self._kinds = list(kinds)
        self._refresh_tip()
        self.update()

    def set_loads(self, loads: List[float]) -> None:
        resized = len(loads) != len(self._loads)
        self._loads = list(loads)
        if resized:
            self.updateGeometry()
        self._refresh_tip()
        self.update()

    def _refresh_tip(self) -> None:
        self.setToolTip("\n".join(
            f"Core {i}: {v:.0f}%" + self._kind_note(i) for i, v in enumerate(self._loads)))

    def _kind_note(self, index: int) -> str:
        kind = self._kinds[index] if index < len(self._kinds) else ""
        return {"P": "  (performance core)", "E": "  (efficiency core)"}.get(kind, "")

    def _columns(self, width: int) -> int:
        return max(1, (width + self.GAP) // (self.CELL + self.GAP))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        rows = -(-max(len(self._loads), 1) // self._columns(width))
        return rows * (self.CELL + self.GAP)

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        return QSize(300, self.heightForWidth(300))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cols = self._columns(self.width())
        for i, load in enumerate(self._loads):
            x = (i % cols) * (self.CELL + self.GAP)
            y = (i // cols) * (self.CELL + self.GAP)
            base = level_colour(load)
            base.setAlpha(int(45 + 2.1 * min(load, 100)))
            rect = QRectF(x, y, self.CELL, self.CELL)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(base)
            painter.drawRoundedRect(rect, 4, 4)
            if index_kind(self._kinds, i) == "E":
                painter.setPen(QPen(self.palette().text().color(), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)
            if load >= 1:       # a wall of zeros is noise; idle cores stay blank
                painter.setPen(self.palette().text().color())
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(int(load)))


class FindingRow(QFrame):
    """One "needs attention" item, with a button to jump to the fix."""

    action_requested = pyqtSignal(str)

    def __init__(self, finding: Finding, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("overviewFinding")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        role = _SEVERITY_ROLE.get(finding.severity)
        colour = semantic(role) if role else self.palette().placeholderText().color().name()
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {colour};")
        lay.addWidget(dot)
        text = QVBoxLayout()
        text.setSpacing(0)
        title = QLabel(finding.title)
        set_role(title, "sectionTitle")
        text.addWidget(title)
        if finding.detail:
            detail = QLabel(finding.detail)
            set_role(detail, "muted")
            detail.setWordWrap(True)
            text.addWidget(detail)
        lay.addLayout(text, 1)
        if finding.action_module:
            button = QPushButton(finding.action_label or "Open")
            button.clicked.connect(
                lambda _=False, m=finding.action_module: self.action_requested.emit(m))
            lay.addWidget(button)


class VfdMeter(QWidget):
    """A segmented bar in the style of a vacuum-fluorescent display.

    Segments light from the left in the theme's success, then warning, then
    error colour as the reading climbs; unlit ones stay faintly visible, as on
    the real thing. A single brighter segment holds the recent peak and falls
    back slowly, which shows a spike that was over before anyone looked.
    """

    SEGMENTS = 40
    PEAK_FALL_PER_TICK = 0.6          # segments the held peak drops per update

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._peak = 0.0
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, percent: float) -> None:
        self._value = max(0.0, min(100.0, float(percent)))
        level = self._value * self.SEGMENTS / 100.0
        self._peak = level if level >= self._peak else max(level, self._peak - self.PEAK_FALL_PER_TICK)
        self.update()

    @property
    def lit(self) -> int:
        return int(round(self._value * self.SEGMENTS / 100.0))

    @property
    def peak_segment(self) -> int:
        return int(round(self._peak))

    def _colour_for(self, index: int) -> QColor:
        share = index / self.SEGMENTS
        role = "success" if share < 0.6 else ("warning" if share < 0.85 else "error")
        return QColor(semantic(role))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gap = 2.0
        width = (self.width() - gap * (self.SEGMENTS - 1)) / self.SEGMENTS
        height = self.height() - 4
        lit, peak = self.lit, self.peak_segment
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.SEGMENTS):
            colour = self._colour_for(i)
            if i < lit:
                colour.setAlpha(255)
            elif i == peak - 1 and peak > lit:
                colour.setAlpha(200)                  # the held peak
            else:
                colour.setAlpha(38)                   # unlit, still faintly there
            p.setBrush(colour)
            p.drawRoundedRect(QRectF(i * (width + gap), 2, width, height), 1.5, 1.5)
