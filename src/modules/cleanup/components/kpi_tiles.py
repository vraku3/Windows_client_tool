"""KPI tiles for the Cleanup overview (row A of the redesign).

Two halves, kept apart the way the rest of Cleanup keeps them:

- ``OverviewFigures`` / ``compute_figures`` -- plain arithmetic over scan
  results, no Qt, so the numbers on the tiles are testable headless. Every
  figure is computed from what the scan MEASURED; nothing is estimated except
  the one tile that says "estimate" on its face (free space after a clean).
- ``KpiTile`` / ``KpiStrip`` -- the widgets. Colours come only from QSS roles
  (``kpiTile``, ``kpiValue``, ``kpiCaption``) and, for the small shape beside
  each caption, from ``core.semantic_colors`` at paint time, so a theme switch
  needs nothing but the repaint Qt already does.

A tile never shows 0 for something it could not read: an unreadable figure is
"—" with the reason in its sub-line and tooltip.
"""
from __future__ import annotations

import ntpath
from dataclasses import dataclass, field
from html import escape
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from core.semantic_colors import chrome, semantic

_RANK = {"safe": 0, "caution": 1, "danger": 2}
DASH = "—"


def _fmt(n: int) -> str:
    from modules.cleanup.cleanup_scanner import format_size
    return format_size(int(n))


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def on_drive(path: str, drive: str) -> bool:
    """Is ``path`` on ``drive`` ("C:")? Case-insensitive, no filesystem call."""
    d = ntpath.splitdrive(str(path))[0]
    return d.rstrip("\\").upper() == drive.rstrip("\\").upper()


# ── Figures (Qt-free) ────────────────────────────────────────────────────

@dataclass(frozen=True)
class OverviewFigures:
    """What row A shows. Sizes are bytes; every path is counted once."""
    total: int = 0
    locations: int = 0
    preset_bytes: int = 0
    preset_count: int = 0
    review_bytes: int = 0
    review_count: int = 0
    preset_on_drive: Dict[str, int] = field(default_factory=dict)
    categories_with_data: int = 0


def compute_figures(rows: Iterable[Tuple[str, int, str]],
                    preset_paths: Iterable[Tuple[str, int]],
                    categories_with_data: int = 0) -> OverviewFigures:
    """``rows`` are (path, size, effective_safety) for every reclaimable item,
    already de-duplicated across categories; ``preset_paths`` are (path, size)
    for what the active preset would remove.

    "Needs review" is everything rated Review or Risky -- the more cautious of
    the catalog's rating and the research's -- because those are the rows that
    start unticked and that no Light clean touches.
    """
    total = locations = review = review_n = 0
    for path, size, safety in rows:
        size = max(0, int(size))
        total += size
        locations += 1
        if _RANK.get(safety, 2) > 0:
            review += size
            review_n += 1
    preset = preset_n = 0
    drives: Dict[str, int] = {}
    seen = set()
    for path, size in preset_paths:
        key = ntpath.normcase(ntpath.normpath(str(path)))
        if key in seen:
            continue
        seen.add(key)
        size = max(0, int(size))
        preset += size
        preset_n += 1
        letter = ntpath.splitdrive(str(path))[0].upper()
        if letter:
            drives[letter] = drives.get(letter, 0) + size
    return OverviewFigures(total=total, locations=locations, preset_bytes=preset,
                           preset_count=preset_n, review_bytes=review, review_count=review_n,
                           preset_on_drive=drives, categories_with_data=categories_with_data)


@dataclass(frozen=True)
class NotOffered:
    """Catalog entries present on this PC that Cleanup deliberately never
    offers, with the catalog's own reason for each (never invented)."""
    count: int = 0
    reasons: Tuple[Tuple[str, str], ...] = ()      # (label, reason)


#: A disabled entry whose reason only says it duplicates another entry is not
#: "not offered" -- its paths ARE offered, by the entry it duplicates.
_DUPLICATE_PREFIXES = ("duplicate of", "same paths as")


def not_offered_on_this_pc(specs: Iterable, exists=None, targets=None) -> NotOffered:
    """Disabled catalog entries whose target exists here. Blocking (it checks
    paths): call it on a Worker. ``exists``/``targets`` are injectable for tests."""
    import os
    exists = exists or os.path.exists
    if targets is None:
        from modules.cleanup.cleanup_scanner.catalog import targets_of as targets
    found: List[Tuple[str, str]] = []
    for spec in specs:
        reason = (getattr(spec, "disabled_reason", "") or "").strip()
        if not reason or reason.lower().startswith(_DUPLICATE_PREFIXES):
            continue
        try:
            present = any(exists(p) for p in targets(spec))
        except (OSError, ValueError) as exc:
            import logging
            logging.getLogger(__name__).warning("not-offered check failed for %s: %s",
                                                getattr(spec, "id", "?"), exc)
            continue
        if present:
            label = getattr(spec, "short_label", "") or getattr(spec, "label", "") or spec.id
            found.append((label, reason))
    found.sort(key=lambda r: r[0].casefold())
    return NotOffered(count=len(found), reasons=tuple(found))


def not_offered_tooltip(info: Optional[NotOffered], limit: int = 8) -> str:
    head = ("<b>Not offered</b><p>Locations on this PC that Cleanup deliberately never "
            "offers: the folder holds settings, sign-ins or user data rather than a "
            "rebuildable cache. Cleanup also refuses any sign-in store (cookies, "
            "IndexedDB, Local Storage), Outlook .pst/.ost files and their folders, "
            "whatever offers them.</p>")
    if info is None:
        return head + "<p>Not checked yet.</p>"
    if not info.reasons:
        return head + "<p>None of those locations exist on this PC.</p>"
    items = "".join(f"<li><b>{escape(label)}</b>: {escape(reason)}</li>"
                    for label, reason in info.reasons[:limit])
    more = (f"<p>…and {len(info.reasons) - limit} more.</p>"
            if len(info.reasons) > limit else "")
    return head + f"<ul>{items}</ul>" + more


# ── Widgets ──────────────────────────────────────────────────────────────

#: tone -> (semantic meaning, shape). The shape says it in grey-scale too.
TONES: Mapping[str, Tuple[str, str]] = {
    "total": ("info", "disc"),
    "safe": ("success", "check"),
    "review": ("warning", "diamond"),
    "blocked": ("error", "bar"),
    "drive": ("info", "ring"),
}


class _Glyph(QWidget):
    """A 14 px shape in a semantic colour, read at paint time."""

    def __init__(self, tone: str, parent=None):
        super().__init__(parent)
        self._tone = tone
        self.setFixedSize(14, 14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event):
        meaning, shape = TONES.get(self._tone, TONES["total"])
        colour = QColor(semantic(meaning))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.5, 1.5, 11.0, 11.0)
        if shape == "disc":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawEllipse(r)
        elif shape == "ring":
            p.setPen(QPen(colour, 2.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        elif shape == "check":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawEllipse(r)
            pen = QPen(QColor(chrome("surface")), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(QPointF(4.3, 7.3))
            path.lineTo(QPointF(6.3, 9.3))
            path.lineTo(QPointF(9.9, 5.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        elif shape == "diamond":
            path = QPainterPath(QPointF(7, 1))
            path.lineTo(QPointF(13, 7))
            path.lineTo(QPointF(7, 13))
            path.lineTo(QPointF(1, 7))
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawPath(path)
        else:                                   # "bar": a no-entry disc
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawEllipse(r)
            p.setBrush(QColor(chrome("surface")))
            p.drawRect(QRectF(3.8, 6.0, 6.4, 2.0))
        p.end()


class KpiTile(QFrame):
    """Caption (with a shape), a large value and one sub-line."""

    def __init__(self, caption: str, tone: str = "total", tooltip: str = "", parent=None):
        super().__init__(parent)
        # Same surface as the chart cards beside it (chrome("surface")), so row A
        # and row B read as one set of cards in every theme.
        self.setObjectName("chartCard")
        self.setProperty("kpiTile", True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.glyph = _Glyph(tone, self)
        head.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self.caption = QLabel(caption.upper())
        self.caption.setObjectName("kpiCaption")
        head.addWidget(self.caption, 1)
        lay.addLayout(head)
        self.value = QLabel(DASH)
        self.value.setObjectName("kpiValue")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.value)
        self.sub = QLabel("")
        self.sub.setObjectName("kpiCaption")
        self.sub.setWordWrap(False)
        lay.addWidget(self.sub)
        self._caption_text = caption
        self.setAccessibleName(caption)
        self.set_tooltip(tooltip)

    def set_tooltip(self, text: str) -> None:
        self.setToolTip(text)
        for w in (self.caption, self.value, self.sub):
            w.setToolTip(text)

    def set(self, value: str, sub: str = "", tooltip: Optional[str] = None) -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        self.setAccessibleDescription(f"{self._caption_text}: {value}. {sub}".strip())
        if tooltip is not None:
            self.set_tooltip(tooltip)

    def minimumSizeHint(self) -> QSize:
        return QSize(150, super().minimumSizeHint().height())


class KpiStrip(QWidget):
    """Tiles in one row, wrapping to fewer columns when the pane is narrow."""

    MIN_TILE_WIDTH = 190

    def __init__(self, tiles: Sequence[KpiTile], parent=None):
        super().__init__(parent)
        self.tiles = list(tiles)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)
        self._columns = 0
        self._available_width = None
        self.setAccessibleName("Key figures")
        self._place(len(self.tiles))

    def _place(self, columns: int) -> None:
        columns = max(1, min(len(self.tiles), columns))
        if columns == self._columns:
            return
        self._columns = columns
        for t in self.tiles:
            self._grid.removeWidget(t)
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for i, t in enumerate(self.tiles):
            self._grid.addWidget(t, i // columns, i % columns)
        for c in range(columns):
            self._grid.setColumnStretch(c, 1)

    def columns(self) -> int:
        return self._columns

    def resizeEvent(self, event):
        self._reflow(self._available_width or event.size().width())
        super().resizeEvent(event)

    def set_available_width(self, width: int) -> None:
        self._available_width = width
        self._reflow(width)

    def _reflow(self, width: int) -> None:
        fit = max(1, (width + 10) // (self.MIN_TILE_WIDTH + 10))
        n = len(self.tiles)
        # Prefer balanced rows (5 -> 3+2, never 4+1).
        if fit < n:
            rows = -(-n // fit)
            fit = -(-n // rows)
        self._place(fit)
