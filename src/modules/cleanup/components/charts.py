"""Cleanup's charts: a donut, a horizontal bar chart and a trend chart.

All three are plain QPainter widgets (no pyqtgraph, no matplotlib -- the
PerfMon precedent) and share one contract:

- ``set_data(...)`` replaces what is shown. The donut and the bar chart take
  ``Slice``s; the trend takes ``TrendPoint``s.
- ``slice_clicked(key)`` fires on a click, or Enter/Space on the keyboard;
  ``hovered(key)`` fires as the pointer (or the arrow keys) moves between
  marks, with ``""`` when it leaves them.
- ``set_tooltip_provider(fn)`` lets the owner supply the hover text (the
  knowledge-base tooltip, say); otherwise a short default is shown.
- ``accessible_summary()`` is the whole chart as one sentence per mark, and is
  also set as the widget's accessible description, so a screen reader gets
  what the picture says.
- ``set_theme(theme)`` repaints. Every colour is read AT PAINT TIME from
  ``core.semantic_colors`` (``chart()``, ``chrome()``, ``semantic()``), so a
  theme switch needs nothing more than that repaint; it can be connected
  straight to ``ThemeManager.theme_changed``.

There is not one colour literal in this file, and every QPainter coordinate
is an ``int`` (PyQt6 is strict about it).
"""
from __future__ import annotations

import html
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
)
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from core.formatting import format_bytes
from core.semantic_colors import chart, chart_other, chrome, semantic

logger = logging.getLogger(__name__)

#: The key of the folded "Other (N)" slice.
OTHER_KEY = "__other__"

#: How each safety level is SAID, with a shape as well as a colour so it reads
#: in grey-scale too (the same words the row badges use).
SAFETY_TEXT: Dict[str, str] = {"safe": "✓ Safe", "caution": "! Review", "danger": "✕ Risky"}
SAFETY_ROLE: Dict[str, str] = {"safe": "success", "caution": "warning", "danger": "error"}
_SAFETY_ORDER = ("safe", "caution", "danger")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Slice:
    """One mark: a donut slice or a bar.

    ``safety_mix`` is bytes per safety level (``{"safe": n, "caution": m}``)
    for a mark that aggregates several items; ``safety`` is the single level
    of a mark that is one item (a bar is coloured by it). ``detail`` is extra
    tooltip text, such as the full path behind a shortened label.
    """
    label: str
    bytes: int
    key: str = ""
    safety_mix: Mapping[str, int] = field(default_factory=dict)
    safety: str = ""
    detail: str = ""
    #: For the folded Other slice: the labels folded into it.
    members: Tuple[str, ...] = ()

    @property
    def ident(self) -> str:
        return self.key or self.label


@dataclass(frozen=True)
class TrendPoint:
    """A value at a moment: ``when`` is seconds since the epoch."""
    when: float
    value: int


def fold_slices(slices: Sequence[Slice], top_n: int = 8,
                other_label: str = "Other") -> List[Slice]:
    """The largest ``top_n`` slices, then one "Other (N)" holding the rest.

    Slices of zero or negative size are dropped -- they cannot be drawn and
    would only pad the legend. Ties keep their input order. ``top_n`` < 1 is
    treated as 1, so there is always at least one named slice.
    """
    live = [s for s in slices if s.bytes > 0]
    live.sort(key=lambda s: s.bytes, reverse=True)       # stable: ties keep order
    top_n = max(1, int(top_n))
    if len(live) <= top_n:
        return live
    kept, rest = live[:top_n], live[top_n:]
    mix: Dict[str, int] = {}
    for s in rest:
        for level, amount in s.safety_mix.items():
            mix[level] = mix.get(level, 0) + int(amount)
        if s.safety and not s.safety_mix:
            mix[s.safety] = mix.get(s.safety, 0) + s.bytes
    other = Slice(
        label=f"{other_label} ({len(rest)})",
        bytes=sum(s.bytes for s in rest),
        key=OTHER_KEY,
        safety_mix=mix,
        members=tuple(s.label for s in rest),
    )
    return kept + [other]


def percent_text(part: int, whole: int) -> str:
    """``part`` as a share of ``whole``: "68%", "<1%", or "0%"."""
    if whole <= 0 or part <= 0:
        return "0%"
    pct = 100.0 * part / whole
    if pct < 1:
        return "<1%"
    return f"{pct:.0f}%"


def safety_mix_text(mix: Mapping[str, int]) -> str:
    """"Safe 12.0 GB · Review 4.1 GB", largest-risk last, empty levels left out."""
    parts = []
    for level in _SAFETY_ORDER:
        amount = int(mix.get(level, 0) or 0)
        if amount > 0:
            word = SAFETY_TEXT[level].split(" ", 1)[1]
            parts.append(f"{word} {format_bytes(amount)}")
    return " · ".join(parts)


def nice_byte_ticks(maximum: int, count: int = 4) -> List[int]:
    """Round axis ticks from 0 to at least ``maximum`` (about ``count`` steps).

    The step is 1, 2, 2.5 or 5 times a power of ten IN THE UNIT the maximum is
    shown in, so the labels read "0 / 5 GB / 10 GB / 15 GB" rather than
    "0 / 4.7 GB / 9.3 GB".
    """
    if maximum <= 0:
        return [0]
    unit = 1
    while maximum / unit >= 1024 and unit < 1024 ** 4:
        unit *= 1024
    raw = maximum / unit / max(1, count)
    exponent = math.floor(math.log10(raw)) if raw > 0 else 0
    base = 10 ** exponent
    step = base
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= factor * base:
            step = factor * base
            break
    ticks = []
    value = 0.0
    while True:
        ticks.append(int(round(value * unit)))
        if value * unit >= maximum:
            break
        value += step
    return ticks


def tick_label(value: int) -> str:
    """An axis label: a byte count without a pointless ".0"."""
    text = format_bytes(value)
    return text.replace(".0 ", " ")


def date_text(when: float) -> str:
    """"21 Oct", with English month names whatever the locale (English-only app)."""
    t = time.localtime(when)
    return f"{t.tm_mday} {_MONTHS[t.tm_mon - 1]}"


def datetime_text(when: float) -> str:
    t = time.localtime(when)
    return f"{t.tm_mday} {_MONTHS[t.tm_mon - 1]} {t.tm_year}, {t.tm_hour:02d}:{t.tm_min:02d}"


# --------------------------------------------------------------------------
# Shared plumbing
# --------------------------------------------------------------------------

def _qc(colour: str, alpha: Optional[int] = None) -> QColor:
    c = QColor(colour)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def scaled_font(font: QFont, factor: float, bold: Optional[bool] = None) -> QFont:
    """``font`` at ``factor`` times its size, whether it is sized in points or
    in pixels (a stylesheet's ``font-size: 12px`` leaves pointSizeF() at -1)."""
    f = QFont(font)
    if f.pixelSize() > 0:
        f.setPixelSize(max(7, int(round(f.pixelSize() * factor))))
    else:
        base = f.pointSizeF() if f.pointSizeF() > 0 else 9.0
        f.setPointSizeF(max(6.0, base * factor))
    if bold is not None:
        f.setBold(bold)
    return f


class _ChartBase(QWidget):
    """Hover, keyboard focus, tooltips, empty state and theme for every chart."""

    slice_clicked = pyqtSignal(str)
    hovered = pyqtSignal(str)

    PAD = 10

    def __init__(self, parent: Optional[QWidget] = None, *, title: str = "",
                 empty_text: str = "No data yet", empty_hint: str = "",
                 fill_background: bool = True):
        super().__init__(parent)
        self._title = title
        self._empty_text = empty_text
        self._empty_hint = empty_hint
        self._fill_background = fill_background
        self._hover = -1
        self._keyboard_focus = False
        self._tooltip_provider: Optional[Callable[[object], str]] = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if title:
            self.setAccessibleName(title)

    # -- public ------------------------------------------------------------

    def set_theme(self, theme: str = "") -> None:
        """Repaint in the theme now in force (colours are read at paint time)."""
        self.update()

    def set_empty_text(self, text: str, hint: str = "") -> None:
        self._empty_text = text
        self._empty_hint = hint
        self.update()

    def set_tooltip_provider(self, provider: Optional[Callable[[object], str]]) -> None:
        """``provider(mark) -> str`` (rich text allowed) replaces the default tooltip."""
        self._tooltip_provider = provider

    def accessible_summary(self) -> str:      # pragma: no cover - overridden
        return ""

    def hover_text(self, index: int) -> str:
        """The tooltip for mark ``index`` (provider first, else the default)."""
        mark = self._mark(index)
        if mark is None:
            return ""
        if self._tooltip_provider is not None:
            try:
                text = self._tooltip_provider(mark)
            except Exception as exc:          # a broken provider must not kill hover
                logger.warning("chart tooltip provider failed: %s", exc)
                text = ""
            if text:
                return text
        return self._default_tooltip(index)

    # -- hooks for subclasses ----------------------------------------------

    def _count(self) -> int:
        return 0

    def _mark(self, index: int):
        return None

    def _mark_key(self, index: int) -> str:
        return ""

    def _index_at(self, pos: QPoint) -> int:
        return -1

    def _default_tooltip(self, index: int) -> str:
        return ""

    # -- shared behaviour ---------------------------------------------------

    def _refresh_accessible(self) -> None:
        self.setAccessibleDescription(self.accessible_summary())

    def _set_hover(self, index: int, global_pos: Optional[QPoint] = None) -> None:
        if index == self._hover:
            return
        self._hover = index
        self.hovered.emit(self._mark_key(index) if index >= 0 else "")
        if global_pos is not None:
            if index >= 0:
                QToolTip.showText(global_pos, self.hover_text(index), self)
            else:
                QToolTip.hideText()
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        self._set_hover(self._index_at(pos), event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._set_hover(-1)
        QToolTip.hideText()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._index_at(event.position().toPoint())
            if index >= 0:
                self.slice_clicked.emit(self._mark_key(index))
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        count = self._count()
        key = event.key()
        if count and key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._keyboard_hover((self._hover + 1) % count)
            return
        if count and key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._keyboard_hover((self._hover - 1) % count if self._hover >= 0 else count - 1)
            return
        if count and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) \
                and self._hover >= 0:
            self.slice_clicked.emit(self._mark_key(self._hover))
            return
        if key == Qt.Key.Key_Escape and self._hover >= 0:
            self._set_hover(-1)
            QToolTip.hideText()
            return
        super().keyPressEvent(event)

    def _keyboard_hover(self, index: int) -> None:
        self._keyboard_focus = True
        self._set_hover(index)
        anchor = self._anchor(index)
        if anchor is not None:
            QToolTip.showText(self.mapToGlobal(anchor), self.hover_text(index), self)

    def _anchor(self, index: int) -> Optional[QPoint]:
        return None

    def focusInEvent(self, event):
        # A focus ring only for keyboard focus: a click already shows where
        # the user is, and a ring on every clicked chart is noise.
        self._keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason,
                                                  Qt.FocusReason.BacktabFocusReason,
                                                  Qt.FocusReason.ShortcutFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._keyboard_focus = False
        QToolTip.hideText()
        self.update()
        super().focusOutEvent(event)

    def _begin(self) -> QPainter:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        if self._fill_background:
            p.fillRect(self.rect(), _qc(chrome("surface")))
        return p

    def _paint_empty(self, p: QPainter, area: QRect, *, ring: Optional[QRectF] = None) -> None:
        """A centred message (and an optional faint track) instead of a blank box."""
        if ring is not None:
            track = QPen(_qc(chrome("outline"), 110), max(2, int(ring.width() * 0.11)))
            track.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(track)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(ring)
        f = scaled_font(self.font(), 1.15, bold=True)
        fm = QFontMetrics(f)
        hint_font = QFont(self.font())
        hfm = QFontMetrics(hint_font)
        width = max(40, area.width() - 16)
        title = fm.elidedText(self._empty_text, Qt.TextElideMode.ElideRight, width)
        block = fm.height() + (hfm.height() * 2 + 4 if self._empty_hint else 0)
        top = int(area.center().y() - block / 2)
        p.setFont(f)
        p.setPen(_qc(chrome("text_muted")))
        p.drawText(QRect(area.left(), top, area.width(), fm.height()),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), title)
        if self._empty_hint:
            p.setFont(hint_font)
            p.drawText(QRect(area.left() + 8, top + fm.height() + 4, area.width() - 16,
                             hfm.height() * 2),
                       int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                           | Qt.TextFlag.TextWordWrap),
                       self._empty_hint)

    def _paint_focus(self, p: QPainter) -> None:
        if self.hasFocus() and self._keyboard_focus and self._count():
            pen = QPen(_qc(chrome("outline"), 160), 1, Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = self.rect().adjusted(1, 1, -2, -2)
            p.drawRoundedRect(QRectF(r), 6, 6)


# --------------------------------------------------------------------------
# Donut
# --------------------------------------------------------------------------

class DonutChart(_ChartBase):
    """Share of a total by category, with its legend beside (or below) it.

    The slices and the legend are the SAME list -- the largest ``top_n``
    plus "Other (N)" -- so every slice can be named. The centre shows the
    total, or the hovered slice's size and label.
    """

    #: Legend goes to the right when at least this much width is left over.
    LEGEND_MIN_WIDTH = 180
    LEGEND_MAX_WIDTH = 300
    MIN_SIDE_DONUT = 120
    RING = 0.19          # ring thickness as a share of the diameter
    EXPLODE = 5          # how far a hovered slice grows outward, px

    def __init__(self, parent: Optional[QWidget] = None, *, title: str = "Space by category",
                 top_n: int = 8, center_caption: str = "reclaimable",
                 empty_text: str = "Nothing to show yet",
                 empty_hint: str = "Run a scan to see where the space is."):
        super().__init__(parent, title=title, empty_text=empty_text, empty_hint=empty_hint)
        self._top_n = top_n
        self._caption = center_caption
        self._slices: List[Slice] = []
        self._total = 0
        self._selected = ""
        self._legend_rects: List[QRect] = []
        self.setMinimumSize(220, 170)

    # -- data ------------------------------------------------------------

    def set_data(self, slices: Sequence[Slice], top_n: Optional[int] = None) -> None:
        if top_n is not None:
            self._top_n = top_n
        self._slices = fold_slices(slices, self._top_n)
        self._total = sum(s.bytes for s in self._slices)
        if self._hover >= len(self._slices):
            self._hover = -1
        self._refresh_accessible()
        self.update()

    def slices(self) -> List[Slice]:
        """What is drawn, after folding: the same list as the legend."""
        return list(self._slices)

    def total(self) -> int:
        return self._total

    def set_selected(self, key: str) -> None:
        """Ring the slice with ``key`` (``""`` clears)."""
        self._selected = key
        self.update()

    def set_center_caption(self, caption: str) -> None:
        self._caption = caption
        self.update()

    def colour_for(self, index: int) -> str:
        s = self._slices[index]
        return chart_other() if s.key == OTHER_KEY else chart(index)

    def accessible_summary(self) -> str:
        if not self._slices:
            return f"{self._title}: {self._empty_text}."
        lines = [f"{self._title}: {format_bytes(self._total)} in total, "
                 f"{len(self._slices)} slice{'s' if len(self._slices) != 1 else ''}."]
        for s in self._slices:
            line = f"{s.label}: {format_bytes(s.bytes)}, {percent_text(s.bytes, self._total)}"
            mix = safety_mix_text(s.safety_mix)
            if mix:
                line += f" ({mix})"
            if s.members:
                line += ". Includes " + ", ".join(s.members)
            lines.append(line + ".")
        return "\n".join(lines)

    # -- geometry ----------------------------------------------------------

    def _layout(self) -> Tuple[QRectF, QRect]:
        """(the donut's bounding square, the legend's area).

        The legend sits to the right when there is room for it, else below.
        The square is inset by EXPLODE so a hovered slice can grow outward
        without being clipped.
        """
        area = self.rect().adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        w, h = area.width(), area.height()
        e = self.EXPLODE
        if not self._slices:
            d = max(40, min(w, h)) - 2 * e
            return (QRectF(area.left() + (w - d) / 2, area.top() + (h - d) / 2, d, d),
                    QRect())
        legend_w = max(self.LEGEND_MIN_WIDTH, min(self.LEGEND_MAX_WIDTH, int(w * 0.56)))
        side = min(h, w - legend_w - 20, 280)
        if side >= self.MIN_SIDE_DONUT:
            d = side - 2 * e
            donut = QRectF(area.left() + e, area.top() + (h - d) / 2, d, d)
            legend_left = int(donut.right()) + e + 20
            return donut, QRect(legend_left, area.top(), area.right() - legend_left, h)
        row_h = self._legend_row_height()
        legend_h = min(len(self._slices) * row_h, max(row_h, h // 2))
        d = max(60, min(w, h - legend_h - 10)) - 2 * e
        donut = QRectF(area.left() + (w - d) / 2, area.top() + e, d, d)
        top = int(donut.bottom()) + e + 10
        return donut, QRect(area.left(), top, w, area.bottom() - top)

    def _legend_row_height(self) -> int:
        return max(22, QFontMetrics(self.font()).height() + 8)

    def _angles(self) -> List[Tuple[float, float]]:
        """(start, span) in degrees clockwise from 12 o'clock, per slice."""
        out = []
        start = 0.0
        for s in self._slices:
            span = 360.0 * s.bytes / self._total if self._total else 0.0
            out.append((start, span))
            start += span
        return out

    def slice_at(self, pos: QPoint) -> int:
        """The slice (or legend row) under ``pos``, or -1."""
        return self._index_at(pos)

    def _index_at(self, pos: QPoint) -> int:
        if not self._slices:
            return -1
        donut, legend = self._layout()
        self._legend_rects = self._legend_rows(legend)
        for i, r in enumerate(self._legend_rects):
            if r.contains(pos):
                return i
        c = donut.center()
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        dist = math.hypot(dx, dy)
        outer = donut.width() / 2
        inner = outer * (1 - 2 * self.RING)
        if dist < inner or dist > outer + self.EXPLODE + 2:
            return -1
        angle = math.degrees(math.atan2(dx, -dy)) % 360.0      # clockwise from top
        for i, (start, span) in enumerate(self._angles()):
            if start <= angle < start + span:
                return i
        return len(self._slices) - 1

    def _legend_rows(self, legend: QRect) -> List[QRect]:
        row_h = self._legend_row_height()
        fit = max(1, legend.height() // row_h) if legend.height() >= row_h else 0
        rows = min(len(self._slices), fit)
        if 1 < rows < len(self._slices):
            rows -= 1                     # the last slot says "+N more"
        slots = rows + (1 if rows < len(self._slices) else 0)
        top = legend.top() + max(0, (legend.height() - slots * row_h) // 2)
        return [QRect(legend.left(), top + i * row_h, legend.width(), row_h) for i in range(rows)]

    def _anchor(self, index: int) -> Optional[QPoint]:
        if not (0 <= index < len(self._slices)):
            return None
        donut, _ = self._layout()
        start, span = self._angles()[index]
        mid = math.radians(start + span / 2)
        r = donut.width() / 2 * (1 - self.RING)
        c = donut.center()
        return QPoint(int(c.x() + r * math.sin(mid)), int(c.y() - r * math.cos(mid)))

    # -- hooks --------------------------------------------------------------

    def _count(self) -> int:
        return len(self._slices)

    def _mark(self, index: int):
        return self._slices[index] if 0 <= index < len(self._slices) else None

    def _mark_key(self, index: int) -> str:
        s = self._mark(index)
        return s.ident if s is not None else ""

    def _default_tooltip(self, index: int) -> str:
        s = self._slices[index]
        parts = [f"<b>{html.escape(s.label)}</b>",
                 f"{format_bytes(s.bytes)} · {percent_text(s.bytes, self._total)} of "
                 f"{format_bytes(self._total)}"]
        mix = safety_mix_text(s.safety_mix)
        if mix:
            parts.append(html.escape(mix))
        if s.members:
            shown = ", ".join(s.members[:6]) + (" …" if len(s.members) > 6 else "")
            parts.append("Includes: " + html.escape(shown))
        if s.detail:
            parts.append(html.escape(s.detail))
        return "<br>".join(parts)

    # -- painting -------------------------------------------------------------

    def paintEvent(self, event):
        p = self._begin()
        try:
            donut, legend = self._layout()
            if not self._slices or self._total <= 0:
                self._paint_empty_donut(p)
                return
            self._paint_ring(p, donut)
            self._paint_centre(p, donut)
            self._paint_legend(p, legend)
            self._paint_focus(p)
        finally:
            p.end()

    def _paint_empty_donut(self, p: QPainter) -> None:
        """A faint ring with the message UNDER it: text inside a small ring
        would cross it, and text over the track reads as a broken chart."""
        area = self.rect().adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        fm = QFontMetrics(self.font())
        text_h = fm.height() * 4 + 8
        d = int(max(36, min(area.height() - text_h - 14, area.width() * 0.4, 130)))
        top = area.top() + max(0, (area.height() - (d + 14 + text_h)) // 2)
        ring = QRectF(area.center().x() - d / 2, top, d, d)
        track = QPen(_qc(chrome("outline"), 90), max(4, int(d * self.RING)))
        track.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(track)
        p.setBrush(Qt.BrushStyle.NoBrush)
        inset = d * self.RING / 2
        p.drawEllipse(ring.adjusted(inset, inset, -inset, -inset))
        below = QRect(area.left(), int(ring.bottom()) + 14, area.width(), text_h)
        self._paint_empty(p, below)

    def _slice_path(self, rect: QRectF, inner_r: float, start: float, span: float) -> QPainterPath:
        # Qt angles: degrees, counter-clockwise from 3 o'clock. Ours: clockwise
        # from 12 o'clock. qt = 90 - ours, and a clockwise span is negative.
        c = rect.center()
        path = QPainterPath()
        path.arcMoveTo(rect, 90.0 - start)
        path.arcTo(rect, 90.0 - start, -span)
        inner = QRectF(c.x() - inner_r, c.y() - inner_r, 2 * inner_r, 2 * inner_r)
        path.arcTo(inner, 90.0 - start - span, span)
        path.closeSubpath()
        return path

    def _paint_ring(self, p: QPainter, donut: QRectF) -> None:
        outer_r = donut.width() / 2
        inner_r = outer_r * (1 - 2 * self.RING)
        c = donut.center()
        many = len(self._slices) > 1
        surface = _qc(chrome("surface"))
        for i, (start, span) in enumerate(self._angles()):
            if span <= 0:
                continue
            s = self._slices[i]
            grow = self.EXPLODE if i == self._hover else 0
            rect = QRectF(c.x() - outer_r - grow, c.y() - outer_r - grow,
                          2 * (outer_r + grow), 2 * (outer_r + grow))
            path = self._slice_path(rect, inner_r, start, min(span, 359.999))
            colour = _qc(self.colour_for(i))
            if self._hover >= 0 and i != self._hover:
                colour.setAlpha(185)
            # A surface-coloured edge is the gap between neighbours: even
            # width all the way out, unlike a gap cut in degrees.
            p.setPen(QPen(surface, 2) if many else Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawPath(path)
            if s.ident and s.ident == self._selected:
                p.setPen(QPen(_qc(chrome("text")), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        # A thin inner rim keeps the hole crisp against slices of any colour.
        p.setPen(QPen(surface, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(c.x() - inner_r, c.y() - inner_r, 2 * inner_r, 2 * inner_r))

    def _paint_centre(self, p: QPainter, donut: QRectF) -> None:
        c = donut.center()
        inner_r = donut.width() / 2 * (1 - 2 * self.RING)
        box_w = int(inner_r * 2 * 0.9)
        if self._hover >= 0:
            s = self._slices[self._hover]
            value = format_bytes(s.bytes)
            caption = s.label
            third = percent_text(s.bytes, self._total)
        else:
            value = format_bytes(self._total)
            caption = self._caption
            third = ""
        factor = 1.55
        big = scaled_font(self.font(), factor, bold=True)
        fm_big = QFontMetrics(big)
        while fm_big.horizontalAdvance(value) > box_w and factor > 1.0:
            factor -= 0.05
            big = scaled_font(self.font(), factor, bold=True)
            fm_big = QFontMetrics(big)
        small = QFont(self.font())
        fm_small = QFontMetrics(small)
        # Shrink the caption a little before eliding it: "reclaimable" cut to
        # "reclaima..." in a small hole reads as a bug. A little: past ~0.88
        # it is too small to read, and eliding is the better failure.
        factor = 1.0
        while fm_small.horizontalAdvance(caption) > box_w and factor > 0.88:
            factor -= 0.04
            small = scaled_font(self.font(), factor)
            fm_small = QFontMetrics(small)
        caption = fm_small.elidedText(caption, Qt.TextElideMode.ElideRight, box_w)
        lines = [(value, big, fm_big, chrome("text"))]
        if caption:
            lines.append((caption, small, fm_small, chrome("text_muted")))
        if third:
            lines.append((third, small, fm_small, chrome("text_muted")))
        block = sum(fm.height() for _, _, fm, _ in lines)
        y = int(c.y() - block / 2)
        for text, font, fm, colour in lines:
            p.setFont(font)
            p.setPen(_qc(colour))
            p.drawText(QRect(int(c.x() - box_w / 2), y, box_w, fm.height()),
                       int(Qt.AlignmentFlag.AlignCenter), text)
            y += fm.height()

    def _paint_legend(self, p: QPainter, legend: QRect) -> None:
        rows = self._legend_rows(legend)
        self._legend_rects = rows
        fm = QFontMetrics(self.font())
        value_w = max(fm.horizontalAdvance(f"{format_bytes(s.bytes)}") for s in self._slices)
        pct_w = fm.horizontalAdvance("100%")
        # A narrow legend drops the percentage column before it starts
        # cutting names: the share is in the tooltip and the summary, and a
        # name is the one thing the legend exists to give.
        widest = max(fm.horizontalAdvance(s.label) for s in self._slices)
        show_pct = legend.width() - (10 + 10 + value_w + 10 + pct_w) >= min(widest, 110)
        if not show_pct:
            pct_w = 0
        for i, r in enumerate(rows):
            s = self._slices[i]
            if i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_qc(chrome("surface_selected")))
                p.drawRoundedRect(QRectF(r.adjusted(-4, 1, 0, -1)), 4, 4)
            sw = 10
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_qc(self.colour_for(i)))
            p.drawRoundedRect(QRectF(r.left() + 2, r.center().y() - sw / 2 + 1, sw, sw), 3, 3)
            text_left = r.left() + sw + 10
            pct_rect = QRect(r.right() - pct_w, r.top(), pct_w, r.height())
            val_rect = QRect(pct_rect.left() - (10 if show_pct else 0) - value_w, r.top(),
                             value_w, r.height())
            label_w = max(20, val_rect.left() - 10 - text_left)
            label = fm.elidedText(s.label, Qt.TextElideMode.ElideRight, label_w)
            p.setFont(self.font())
            p.setPen(_qc(chrome("text")))
            align_v = Qt.AlignmentFlag.AlignVCenter
            p.drawText(QRect(text_left, r.top(), label_w, r.height()),
                       int(Qt.AlignmentFlag.AlignLeft | align_v), label)
            p.drawText(val_rect, int(Qt.AlignmentFlag.AlignRight | align_v), format_bytes(s.bytes))
            if show_pct:
                p.setPen(_qc(chrome("text_muted")))
                p.drawText(pct_rect, int(Qt.AlignmentFlag.AlignRight | align_v),
                           percent_text(s.bytes, self._total))
        hidden = len(self._slices) - len(rows)
        if hidden > 0 and rows:
            p.setPen(_qc(chrome("text_muted")))
            last = rows[-1]
            p.drawText(QRect(last.left() + 22, last.bottom() + 1, last.width() - 22,
                             last.height()),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       f"+{hidden} more (hover the ring)")

    def sizeHint(self) -> QSize:
        return QSize(460, 230)


# --------------------------------------------------------------------------
# Horizontal bars
# --------------------------------------------------------------------------

class BarChart(_ChartBase):
    """The largest items, one horizontal bar each, longest first.

    A bar with a ``safety`` is coloured by it and carries the badge text
    ("✓ Safe", "! Review", "✕ Risky") inside the bar, so safety is never
    colour alone; a bar without one takes the categorical palette. Labels are
    elided in the MIDDLE by default, because they are usually paths and the
    end of a path is the part that says what it is.
    """

    def __init__(self, parent: Optional[QWidget] = None, *, title: str = "Largest locations",
                 top_n: int = 10, fold_other: bool = False,
                 elide: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
                 empty_text: str = "Nothing to show yet",
                 empty_hint: str = "Run a scan to list the largest locations."):
        super().__init__(parent, title=title, empty_text=empty_text, empty_hint=empty_hint)
        self._top_n = top_n
        self._fold_other = fold_other
        self._elide = elide
        self._bars: List[Slice] = []
        self.setMinimumSize(260, 120)

    @property
    def bar_clicked(self):
        """The same signal as ``slice_clicked``, by the name a bar chart suggests."""
        return self.slice_clicked

    def set_data(self, slices: Sequence[Slice], top_n: Optional[int] = None) -> None:
        if top_n is not None:
            self._top_n = top_n
        if self._fold_other:
            self._bars = fold_slices(slices, self._top_n)
        else:
            live = [s for s in slices if s.bytes > 0]
            live.sort(key=lambda s: s.bytes, reverse=True)
            self._bars = live[:max(1, int(self._top_n))]
        if self._hover >= len(self._bars):
            self._hover = -1
        self._refresh_accessible()
        self.updateGeometry()
        self.update()

    def bars(self) -> List[Slice]:
        return list(self._bars)

    def bar_at(self, pos: QPoint) -> int:
        return self._index_at(pos)

    def colour_for(self, index: int) -> str:
        s = self._bars[index]
        if s.safety in SAFETY_ROLE:
            return semantic(SAFETY_ROLE[s.safety])
        if s.key == OTHER_KEY:
            return chart_other()
        return chart(index)

    def accessible_summary(self) -> str:
        if not self._bars:
            return f"{self._title}: {self._empty_text}."
        lines = [f"{self._title}: {len(self._bars)} item{'s' if len(self._bars) != 1 else ''}, "
                 "largest first."]
        for i, s in enumerate(self._bars, 1):
            line = f"{i}. {s.detail or s.label}: {format_bytes(s.bytes)}"
            if s.safety in SAFETY_TEXT:
                line += f", {SAFETY_TEXT[s.safety].split(' ', 1)[1]}"
            lines.append(line + ".")
        return "\n".join(lines)

    # -- geometry --------------------------------------------------------------

    def _row_height(self) -> int:
        return max(24, QFontMetrics(self.font()).height() + 10)

    def _geometry(self) -> Tuple[List[QRect], int, int, int]:
        """(row rects, label width, bar left, bar max width)."""
        area = self.rect().adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        n = len(self._bars)
        if not n:
            return [], 0, 0, 0
        row_h = max(18, min(self._row_height(), area.height() // n if n else 0))
        fm = QFontMetrics(self.font())
        widest = max(fm.horizontalAdvance(s.label) for s in self._bars)
        label_w = min(widest + 4, int(area.width() * 0.42), 320)
        value_w = max(fm.horizontalAdvance(format_bytes(s.bytes)) for s in self._bars) + 6
        bar_left = area.left() + label_w + 12
        bar_max = max(20, area.right() - value_w - 10 - bar_left)
        rows = [QRect(area.left(), area.top() + i * row_h, area.width(), row_h) for i in range(n)]
        return rows, label_w, bar_left, bar_max

    def _bar_rect(self, index: int, rows, bar_left: int, bar_max: int) -> QRectF:
        r = rows[index]
        top_value = self._bars[0].bytes or 1
        w = max(3.0, bar_max * self._bars[index].bytes / top_value)
        h = max(10, int(r.height() * 0.62))
        return QRectF(bar_left, r.top() + (r.height() - h) / 2, w, h)

    def _index_at(self, pos: QPoint) -> int:
        rows, _, _, _ = self._geometry()
        for i, r in enumerate(rows):
            if r.contains(pos):
                return i
        return -1

    def _anchor(self, index: int) -> Optional[QPoint]:
        rows, _, bar_left, bar_max = self._geometry()
        if not (0 <= index < len(rows)):
            return None
        b = self._bar_rect(index, rows, bar_left, bar_max)
        return QPoint(int(b.right()), int(b.center().y()))

    # -- hooks --------------------------------------------------------------

    def _count(self) -> int:
        return len(self._bars)

    def _mark(self, index: int):
        return self._bars[index] if 0 <= index < len(self._bars) else None

    def _mark_key(self, index: int) -> str:
        s = self._mark(index)
        return s.ident if s is not None else ""

    def _default_tooltip(self, index: int) -> str:
        s = self._bars[index]
        total = sum(b.bytes for b in self._bars)
        parts = [f"<b>{html.escape(s.label)}</b>"]
        if s.detail and s.detail != s.label:
            parts.append(html.escape(s.detail))
        line = f"{format_bytes(s.bytes)} · {percent_text(s.bytes, total)} of what is shown"
        parts.append(line)
        if s.safety in SAFETY_TEXT:
            parts.append(html.escape(SAFETY_TEXT[s.safety]))
        mix = safety_mix_text(s.safety_mix)
        if mix:
            parts.append(html.escape(mix))
        return "<br>".join(parts)

    # -- painting -------------------------------------------------------------

    def paintEvent(self, event):
        p = self._begin()
        try:
            if not self._bars:
                self._paint_empty(p, self.rect())
                return
            rows, label_w, bar_left, bar_max = self._geometry()
            fm = QFontMetrics(self.font())
            badge_font = scaled_font(self.font(), 0.88, bold=True)
            bfm = QFontMetrics(badge_font)
            align_v = Qt.AlignmentFlag.AlignVCenter
            for i, r in enumerate(rows):
                s = self._bars[i]
                if i == self._hover:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(_qc(chrome("surface_selected")))
                    p.drawRoundedRect(QRectF(r.adjusted(-4, 1, 4, -1)), 4, 4)
                # Label
                p.setFont(self.font())
                p.setPen(_qc(chrome("text")))
                label = fm.elidedText(s.label, self._elide, label_w)
                p.drawText(QRect(r.left(), r.top(), label_w, r.height()),
                           int(Qt.AlignmentFlag.AlignRight | align_v), label)
                # Track, then the bar
                bar = self._bar_rect(i, rows, bar_left, bar_max)
                track = QRectF(bar_left, bar.top(), bar_max, bar.height())
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_qc(chrome("outline"), 38))
                p.drawRoundedRect(track, 3, 3)
                colour = _qc(self.colour_for(i))
                if self._hover >= 0 and i != self._hover:
                    colour.setAlpha(170)
                p.setBrush(colour)
                p.drawRoundedRect(bar, 3, 3)
                # Badge: inside the bar when it fits, else just after it.
                badge = SAFETY_TEXT.get(s.safety, "")
                if badge:
                    p.setFont(badge_font)
                    tw = bfm.horizontalAdvance(badge)
                    if bar.width() >= tw + 14:
                        p.setPen(_qc(chrome("surface")))
                        p.drawText(QRect(int(bar.left()) + 7, int(bar.top()), tw + 2,
                                         int(bar.height())),
                                   int(Qt.AlignmentFlag.AlignLeft | align_v), badge)
                    else:
                        p.setPen(_qc(self.colour_for(i)))
                        p.drawText(QRect(int(bar.right()) + 6, r.top(), tw + 2, r.height()),
                                   int(Qt.AlignmentFlag.AlignLeft | align_v), badge)
                # Value, right-aligned in its own column
                p.setFont(self.font())
                p.setPen(_qc(chrome("text_muted")))
                p.drawText(QRect(bar_left + bar_max + 10, r.top(),
                                 r.right() - (bar_left + bar_max + 10), r.height()),
                           int(Qt.AlignmentFlag.AlignRight | align_v), format_bytes(s.bytes))
            self._paint_focus(p)
        finally:
            p.end()

    def sizeHint(self) -> QSize:
        n = max(3, len(self._bars))
        return QSize(460, n * self._row_height() + 2 * self.PAD)


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------

class TrendChart(_ChartBase):
    """Freed per clean (columns), total freed (line) and reclaimable at each
    scan (dashed line), on one time axis.

    With fewer than two points in every series it shows "Not enough history
    yet" rather than an empty pair of axes.
    """

    SERIES = (("freed", "Freed per clean"), ("cumulative", "Total freed"),
              ("reclaimable", "Reclaimable at scan"))

    def __init__(self, parent: Optional[QWidget] = None, *, title: str = "Trend",
                 empty_text: str = "Not enough history yet",
                 empty_hint: str = "The trend appears after two cleans or two scans."):
        super().__init__(parent, title=title, empty_text=empty_text, empty_hint=empty_hint)
        self._cleans: List[TrendPoint] = []
        self._scans: List[TrendPoint] = []
        self._cumulative: List[TrendPoint] = []
        self._marks: List[Tuple[str, int]] = []       # (series, index) by time
        self.setMinimumSize(260, 150)

    def set_data(self, cleans: Sequence[TrendPoint] = (),
                 scans: Sequence[TrendPoint] = ()) -> None:
        self._cleans = sorted((TrendPoint(float(c.when), int(c.value)) for c in cleans),
                              key=lambda t: t.when)
        self._scans = sorted((TrendPoint(float(s.when), int(s.value)) for s in scans),
                             key=lambda t: t.when)
        running = 0
        self._cumulative = []
        for c in self._cleans:
            running += max(0, c.value)
            self._cumulative.append(TrendPoint(c.when, running))
        marks = [("freed", i, c.when) for i, c in enumerate(self._cleans)]
        marks += [("reclaimable", i, s.when) for i, s in enumerate(self._scans)]
        marks.sort(key=lambda m: m[2])
        self._marks = [(series, i) for series, i, _ in marks]
        if self._hover >= len(self._marks):
            self._hover = -1
        self._refresh_accessible()
        self.update()

    def has_enough_data(self) -> bool:
        return max(len(self._cleans), len(self._scans)) >= 2

    def colour_for(self, series: str) -> str:
        return chart({"freed": 0, "cumulative": 1, "reclaimable": 2}[series])

    def accessible_summary(self) -> str:
        if not self.has_enough_data():
            return f"{self._title}: {self._empty_text}."
        lines = [f"{self._title}:"]
        if self._cleans:
            first, last = self._cleans[0], self._cleans[-1]
            lines.append(
                f"{len(self._cleans)} clean{'s' if len(self._cleans) != 1 else ''} between "
                f"{date_text(first.when)} and {date_text(last.when)} freed "
                f"{format_bytes(self._cumulative[-1].value)} in total; the latest freed "
                f"{format_bytes(last.value)}.")
        if self._scans:
            first, last = self._scans[0], self._scans[-1]
            lines.append(
                f"Reclaimable at scan went from {format_bytes(first.value)} on "
                f"{date_text(first.when)} to {format_bytes(last.value)} on "
                f"{date_text(last.when)}, over {len(self._scans)} "
                f"scan{'s' if len(self._scans) != 1 else ''}.")
        return "\n".join(lines)

    # -- geometry -----------------------------------------------------------

    def _visible_series(self) -> List[Tuple[str, str]]:
        out = []
        if self._cleans:
            out += [self.SERIES[0], self.SERIES[1]]
        if self._scans:
            out.append(self.SERIES[2])
        return out

    def _range(self) -> Tuple[float, float, int]:
        whens = [c.when for c in self._cleans] + [s.when for s in self._scans]
        lo, hi = min(whens), max(whens)
        if hi - lo < 3600:
            lo, hi = lo - 86400, hi + 86400
        top = max([c.value for c in self._cumulative] + [s.value for s in self._scans]
                  + [c.value for c in self._cleans] + [1])
        return lo, hi, top

    def _plot(self) -> Tuple[QRect, List[int], float, float]:
        """(plot area, y ticks, x low, x high)."""
        fm = QFontMetrics(self.font())
        lo, hi, top = self._range()
        ticks = nice_byte_ticks(top)
        y_label_w = max(fm.horizontalAdvance(tick_label(t)) for t in ticks) + 8
        legend_h = fm.height() + 10
        area = self.rect().adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        plot = QRect(area.left() + y_label_w, area.top() + legend_h,
                     area.width() - y_label_w - 12, area.height() - legend_h - fm.height() - 8)
        return plot, ticks, lo, hi

    def _x(self, when: float, plot: QRect, lo: float, hi: float) -> int:
        inset = 14
        span = plot.width() - 2 * inset
        return int(plot.left() + inset + span * (when - lo) / (hi - lo))

    @staticmethod
    def _y(value: int, plot: QRect, ymax: int) -> int:
        return int(plot.bottom() - plot.height() * value / max(1, ymax))

    def _mark_point(self, index: int) -> Optional[QPoint]:
        if not (0 <= index < len(self._marks)) or not self.has_enough_data():
            return None
        plot, ticks, lo, hi = self._plot()
        series, i = self._marks[index]
        point = self._cleans[i] if series == "freed" else self._scans[i]
        return QPoint(self._x(point.when, plot, lo, hi), self._y(point.value, plot, ticks[-1]))

    def _index_at(self, pos: QPoint) -> int:
        if not self.has_enough_data():
            return -1
        plot, _, _, _ = self._plot()
        if not plot.adjusted(-8, -8, 8, 8).contains(pos):
            return -1
        best, best_d = -1, 1e9
        for k in range(len(self._marks)):
            pt = self._mark_point(k)
            d = abs(pt.x() - pos.x()) + 0.25 * abs(pt.y() - pos.y())
            if d < best_d:
                best, best_d = k, d
        return best if best_d <= 40 else -1

    def _anchor(self, index: int) -> Optional[QPoint]:
        return self._mark_point(index)

    # -- hooks --------------------------------------------------------------

    def _count(self) -> int:
        return len(self._marks) if self.has_enough_data() else 0

    def _mark(self, index: int):
        if not (0 <= index < len(self._marks)):
            return None
        series, i = self._marks[index]
        return self._cleans[i] if series == "freed" else self._scans[i]

    def _mark_key(self, index: int) -> str:
        if not (0 <= index < len(self._marks)):
            return ""
        series, i = self._marks[index]
        return f"{series}:{i}"

    def _default_tooltip(self, index: int) -> str:
        series, i = self._marks[index]
        if series == "freed":
            c = self._cleans[i]
            return (f"<b>Clean, {html.escape(datetime_text(c.when))}</b><br>"
                    f"Freed {format_bytes(c.value)}<br>"
                    f"Total freed so far {format_bytes(self._cumulative[i].value)}")
        s = self._scans[i]
        return (f"<b>Scan, {html.escape(datetime_text(s.when))}</b><br>"
                f"Reclaimable {format_bytes(s.value)}")

    # -- painting -------------------------------------------------------------

    def paintEvent(self, event):
        p = self._begin()
        try:
            if not self.has_enough_data():
                self._paint_empty(p, self.rect())
                return
            plot, ticks, lo, hi = self._plot()
            ymax = ticks[-1]
            fm = QFontMetrics(self.font())
            self._paint_axes(p, plot, ticks, lo, hi, fm)
            self._paint_columns(p, plot, lo, hi, ymax)
            self._paint_line(p, plot, lo, hi, ymax, self._cumulative, "cumulative", dashed=False)
            self._paint_line(p, plot, lo, hi, ymax, self._scans, "reclaimable", dashed=True)
            self._paint_hover(p, plot)
            self._paint_legend(p, fm)
            self._paint_focus(p)
        finally:
            p.end()

    def _paint_axes(self, p, plot: QRect, ticks, lo, hi, fm) -> None:
        grid = QPen(_qc(chrome("outline"), 70), 1)
        muted = _qc(chrome("text_muted"))
        for t in ticks:
            y = self._y(t, plot, ticks[-1])
            p.setPen(grid)
            p.drawLine(plot.left(), y, plot.right(), y)
            p.setPen(muted)
            p.drawText(QRect(self.PAD, y - fm.height() // 2, plot.left() - self.PAD - 6,
                             fm.height()),
                       int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                       tick_label(t))
        p.setPen(QPen(_qc(chrome("outline")), 1))
        p.drawLine(plot.left(), plot.bottom(), plot.right(), plot.bottom())
        # Date labels: up to five, evenly spread, never overlapping.
        label_w = fm.horizontalAdvance("28 Sep") + 16
        slots = max(2, min(5, plot.width() // max(1, label_w)))
        p.setPen(muted)
        last_right = -10 ** 6
        for k in range(slots):
            when = lo + (hi - lo) * k / (slots - 1)
            x = self._x(when, plot, lo, hi)
            text = date_text(when)
            w = fm.horizontalAdvance(text)
            left = max(plot.left() - 6, min(x - w // 2, plot.right() + 6 - w))
            if left < last_right + 8:
                continue
            p.drawText(QRect(left, plot.bottom() + 4, w + 2, fm.height()),
                       int(Qt.AlignmentFlag.AlignLeft), text)
            last_right = left + w

    def _paint_columns(self, p, plot: QRect, lo, hi, ymax) -> None:
        if not self._cleans:
            return
        n = len(self._cleans)
        width = max(4, min(18, int(plot.width() / max(1, n) * 0.45)))
        colour = _qc(self.colour_for("freed"))
        for i, c in enumerate(self._cleans):
            x = self._x(c.when, plot, lo, hi)
            y = self._y(max(0, c.value), plot, ymax)
            col = QColor(colour)
            if self._hover >= 0 and self._marks[self._hover] != ("freed", i):
                col.setAlpha(170)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            h = max(2, plot.bottom() - y)
            path = QPainterPath()
            path.addRoundedRect(QRectF(x - width / 2, plot.bottom() - h, width, h + 3), 2, 2)
            p.save()
            p.setClipRect(QRect(plot.left() - 20, plot.top() - 20, plot.width() + 40,
                                plot.bottom() - plot.top() + 20))
            p.drawPath(path)
            p.restore()

    def _paint_line(self, p, plot: QRect, lo, hi, ymax, points: Sequence[TrendPoint],
                    series: str, *, dashed: bool) -> None:
        if not points:
            return
        colour = _qc(self.colour_for(series))
        pen = QPen(colour, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        pts = [QPointF(self._x(t.when, plot, lo, hi), self._y(t.value, plot, ymax))
               for t in points]
        if len(pts) > 1:
            path = QPainterPath(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.setPen(QPen(_qc(chrome("surface")), 2))
        p.setBrush(colour)
        for q in pts:
            p.drawEllipse(QPointF(q), 3.5, 3.5)

    def _paint_hover(self, p, plot: QRect) -> None:
        if self._hover < 0:
            return
        pt = self._mark_point(self._hover)
        if pt is None:
            return
        p.setPen(QPen(_qc(chrome("outline")), 1, Qt.PenStyle.DotLine))
        p.drawLine(pt.x(), plot.top(), pt.x(), plot.bottom())
        series, _ = self._marks[self._hover]
        p.setPen(QPen(_qc(chrome("text")), 2))
        p.setBrush(_qc(self.colour_for(series)))
        p.drawEllipse(QPointF(pt), 5.0, 5.0)

    #: Shorter legend words for a narrow chart, so the legend never clips.
    _SHORT = {"freed": "Freed", "cumulative": "Total", "reclaimable": "Reclaimable"}

    def _paint_legend(self, p, fm) -> None:
        items = self._visible_series()
        swatch, gap = 18, 16
        avail = self.width() - 2 * self.PAD

        def width(labels):
            return sum(swatch + 6 + fm.horizontalAdvance(lbl) for lbl in labels)                 + gap * (len(labels) - 1)

        labels = [label for _, label in items]
        if width(labels) > avail:
            labels = [self._SHORT[key] for key, _ in items]
        x = self.PAD + max(0, (avail - width(labels)))      # right-aligned when it fits
        y = self.PAD
        h = fm.height()
        cy = y + h // 2
        for (key, _), label in zip(items, labels):
            colour = _qc(self.colour_for(key))
            if key == "freed":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(colour)
                p.drawRoundedRect(QRectF(x + 4, cy - 5, 10, 10), 2, 2)
            else:
                pen = QPen(colour, 2)
                if key == "reclaimable":
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(x, cy, x + swatch, cy)
            x += swatch + 6
            w = fm.horizontalAdvance(label)
            p.setPen(_qc(chrome("text_muted")))
            p.drawText(QRect(x, y, w + 2, h), int(Qt.AlignmentFlag.AlignLeft), label)
            x += w + gap

    def sizeHint(self) -> QSize:
        return QSize(460, 220)


# --------------------------------------------------------------------------
# Card
# --------------------------------------------------------------------------

class ChartCard(QFrame):
    """A chart in a ``QFrame#chartCard`` with a title and an optional caption.

    The card's background is the same ``chrome("surface")`` the chart paints,
    and that is the colour the chart palette's contrast is measured against.
    """

    def __init__(self, title: str, chart_widget: QWidget, caption: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("chartCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("chartTitle")
        layout.addWidget(self.title_label)
        layout.addWidget(chart_widget, 1)
        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("kpiCaption")
        self.caption_label.setWordWrap(True)
        self.caption_label.setVisible(bool(caption))
        layout.addWidget(self.caption_label)
        self.chart = chart_widget

    def set_caption(self, text: str) -> None:
        self.caption_label.setText(text)
        self.caption_label.setVisible(bool(text))
