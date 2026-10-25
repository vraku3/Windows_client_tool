"""Responsive, clickable summary tiles with theme-aware status and trends."""
from math import isfinite
from PyQt6 import sip
from PyQt6.QtCore import Qt, QSize, QPointF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QGridLayout, QWidget, QSizePolicy
from core.semantic_colors import semantic, chrome

ROLES = {"ok": "success", "info": "info", "warning": "warning", "danger": "error"}
ALIASES = {"total": "info", "safe": "ok", "review": "warning", "blocked": "danger", "drive": "info"}


class GlanceTile(QFrame):
    """A measured value; None always means unreadable, never zero."""
    clicked = pyqtSignal()

    def __init__(self, caption, severity="info", tooltip="", parent=None):
        super().__init__(parent)
        self.setObjectName("glanceTile")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.severity = "info"
        self._measured_severity = "info"
        self.history = []
        self.reason = ""
        self._unreadable = False
        self._base_tooltip = tooltip or f"{caption}. Click or press Enter to filter the displayed results."
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 14, 12)
        layout.setSpacing(3)
        self.caption = QLabel(caption.upper())
        self.caption.setObjectName("glanceCaption")
        self.value = QLabel("—")
        self.value.setObjectName("glanceValue")
        self.sub = QLabel("")
        self.sub.setObjectName("glanceCaption")
        for label in (self.caption, self.value, self.sub):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(label)
        self.setAccessibleName(caption)
        self.set_tooltip(self._base_tooltip)
        self.set_severity(severity)

    def set_tooltip(self, text):
        self.setToolTip(text)
        for label in (self.caption, self.value, self.sub):
            label.setToolTip(text)

    def set_severity(self, severity):
        severity = ALIASES.get(severity, severity)
        if severity not in (*ROLES, "unknown", "muted"):
            raise ValueError(f"Unknown severity: {severity}")
        self.severity = severity
        if severity != "unknown":
            self._measured_severity = severity
        self.update()

    def set(self, value, sub="", tooltip=None, *, reason="", severity=None):
        if sip.isdeleted(self):
            return
        self._unreadable = value is None
        self.reason = reason if value is None else ""
        self.value.setText("—" if value is None else str(value))
        self.sub.setText((reason or "Could not read") if value is None else sub)
        self.set_severity("unknown" if value is None else (severity or self._measured_severity))
        tip = tooltip or self._base_tooltip
        if value is None:
            tip += f"\n{reason or 'Could not read'}"
        self.set_tooltip(tip)
        self.setAccessibleDescription(f"{self.value.text()}. {self.sub.text()}")

    def set_value(self, value, sub="", **kwargs):
        self.set(value, sub, **kwargs)

    def set_unreadable(self, reason):
        self.set(None, reason=reason)

    def set_history(self, values, limit=30):
        self.history = [float(v) for v in values if v is not None and isfinite(float(v))][-max(2, limit):]
        self.update()

    def minimumSizeHint(self):
        return QSize(160, super().minimumSizeHint().height())

    def paintEvent(self, event):
        super().paintEvent(event)
        color = QColor(semantic(ROLES[self.severity]) if self.severity in ROLES else chrome("text_muted"))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(0, 10, 3, max(0, self.height() - 20), color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(9, 17, 5, 5)
        if len(self.history) > 1 and not self._unreadable:
            low, high = min(self.history), max(self.history)
            span = high - low or 1
            width = min(76, self.width() // 3)
            points = QPolygonF([QPointF(self.width() - width - 12 + i * width / (len(self.history) - 1),
                64 - (v - low) * 24 / span) for i, v in enumerate(self.history)])
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(points)
        painter.end()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class GlanceStrip(QWidget):
    """Balanced rows that reflow without changing tile identities or signals."""
    MIN_TILE_WIDTH = 190

    def __init__(self, tiles=(), parent=None):
        super().__init__(parent)
        self.tiles = list(tiles)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(10)
        self._columns = 0
        self._available_width = None
        self.setToolTip("Key measurements. Select a tile to inspect its related results.")
        self._place(max(1, len(self.tiles)))

    def _place(self, columns):
        columns = max(1, min(len(self.tiles), columns))
        if columns == self._columns:
            return
        self._columns = columns
        for tile in self.tiles:
            self._grid.removeWidget(tile)
        for column in range(self._grid.columnCount()):
            self._grid.setColumnStretch(column, 0)
        for index, tile in enumerate(self.tiles):
            self._grid.addWidget(tile, index // columns, index % columns)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)

    def columns(self):
        return self._columns

    def set_available_width(self, width):
        self._available_width = width
        self._reflow(width)

    def _reflow(self, width):
        fit = max(1, (width + 10) // (self.MIN_TILE_WIDTH + 10))
        count = len(self.tiles)
        if fit < count:
            rows = -(-count // fit)
            fit = -(-count // rows)
        self._place(fit)

    def resizeEvent(self, event):
        self._reflow(self._available_width or event.size().width())
        super().resizeEvent(event)
