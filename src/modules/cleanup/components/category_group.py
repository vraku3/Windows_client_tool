"""One cleanup category on the overview: a compact row that opens into its paths.

The row (``_CategoryHeader``) is painted, so every row lines up in columns::

    ▸ Temp Files                 16.7 GB  ▓▓▓▓▓▓▓▓░░  68%   ✓ Safe   2 locations   ⓘ  ⋯
      The user temp folder (%TEMP%) and Windows\\Temp.

Name and the research's one-line "what" on the left; size, share of everything
reclaimable, the safety badge (text AND shape, never colour alone) and the
location count on the right; then an info button that opens the detail pane
and a ``⋯`` menu (Scan again, Clean this category…, Open folder, Exclude…,
Review on the area tab). Clicking the row opens the path list.

The path list stays a ``QTreeWidget`` with one root and one child per item, so
the tick rules every test pins are unchanged:

- a row the scanner left ``selected=False`` (an orphaned VM disk) starts
  unticked, and a click on the root never ticks it;
- a row rated Review or Risky -- the more cautious of the catalog and the
  research -- also starts unticked (``UNTICK_REVIEW_ROWS``); a person may
  still tick it by hand, and that tick is what gets passed to the deleter;
- deletion goes through ``delete_items_report`` only, and what is reported
  freed is what the deleter MEASURED.

Every colour is read at paint time from ``core.semantic_colors``; there is no
colour literal here.
"""
from __future__ import annotations

import dataclasses
import logging
import os
from html import escape
from typing import Callable, List, Optional

from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication, QDialog, QHeaderView, QLabel, QMenu, QMessageBox, QSizePolicy, QToolButton,
    QScrollArea, QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QHBoxLayout, QWidget,
)

from core.semantic_colors import chrome, semantic
from core.widget_life import widget_is_valid
from core.worker import Worker

try:
    from modules.cleanup.cleanup_scanner import ScanResult, ScanItem, format_size
except ImportError:                                   # pragma: no cover
    ScanResult = object
    ScanItem = object

    def format_size(s):
        return str(s)

logger = logging.getLogger(__name__)

_alive = widget_is_valid

#: Owner decision (2026-10-21): rows rated Review or Risky start UNTICKED.
#: Flip to False to go back to "whatever the scanner selected".
UNTICK_REVIEW_ROWS = True

_RANK = {"safe": 0, "caution": 1, "danger": 2}
BADGE = {"safe": "✓ Safe", "caution": "! Review", "danger": "✕ Risky"}
_MEANING = {"safe": "success", "caution": "warning", "danger": "error"}
SAFETY_HELP = {
    "safe": "Safe: Windows or the app rebuilds this on its own. Cleaning it costs nothing "
            "but the time to rebuild.",
    "caution": "Review: worth reading what it is first. Cleaning it can cost a re-download, "
               "a re-index or diagnostics you might still want. Starts unticked.",
    "danger": "Risky: deleting this can lose data or break something. Never cleaned by the "
              "Light or Thorough presets. Starts unticked.",
}

# Path-tree columns
COL_PATH, COL_SIZE, COL_SHARE, COL_SAFETY, COL_VERDICT = range(5)
TREE_HEADERS = ("Location", "Size", "Share of total", "Safety", "What happens")


def _rank(level: str) -> int:
    return _RANK.get(level, 2)


def most_cautious(levels) -> str:
    levels = [lv for lv in levels if lv]
    if not levels:
        return "safe"
    return max(levels, key=_rank)


_ABBREVIATIONS = ("incl.", "e.g.", "i.e.", "etc.", "vs.", "approx.", "ca.")


def first_sentence(text: str, limit: int = 140) -> str:
    """The first sentence of ``text`` -- not cut inside brackets or after an
    abbreviation such as "incl." -- or the text shortened to ``limit``."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    start = 0
    while True:
        cut = text.find(". ", start)
        if cut <= 0 or cut >= limit:
            break
        head = text[:cut + 1]
        word = head.rsplit(" ", 1)[-1].lower()
        if head.count("(") == head.count(")") and not word.endswith(_ABBREVIATIONS):
            return head
        start = cut + 2
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _knowledge():
    try:
        from modules.cleanup.cleanup_scanner import knowledge
        return knowledge
    except ImportError as exc:                       # pragma: no cover
        logger.warning("cleanup knowledge unavailable: %s", exc)
        return None


# ── The painted row ──────────────────────────────────────────────────────

class _CategoryHeader(QWidget):
    """The category row. Painted; tooltips per region; Enter/Space toggles."""

    toggled = pyqtSignal()

    HEIGHT = 50
    COL_COUNT = 104
    COL_BADGE = 92
    COL_SHARE = 150
    COL_SIZE = 92
    GAP = 14

    def __init__(self, group: "CategoryGroup"):
        super().__init__(group)
        self._g = group
        self._hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # geometry ----------------------------------------------------------
    def _columns(self):
        return row_columns(self.rect())

    def region_at(self, pos: QPoint) -> str:
        for key, rect in self._columns().items():
            if rect.contains(pos):
                return key
        return "name"

    # events ------------------------------------------------------------
    def enterEvent(self, event):
        self._hover = True
        self._g.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._g.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.toggled.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggled.emit()
            return
        if event.key() == Qt.Key.Key_F1:
            self._g.show_details()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.update()
        super().focusOutEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            text = self._g.tooltip_for(self.region_at(event.pos()))
            if text:
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    # paint -------------------------------------------------------------
    def paintEvent(self, _event):
        g = self._g
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        text = QColor(chrome("text"))
        muted = QColor(chrome("text_muted"))
        outline = QColor(chrome("outline"))

        if self.hasFocus():
            pen = QPen(QColor(semantic("info")), 1)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(1.5, 1.5, -1.5, -1.5), 4, 4)

        # chevron
        cx, cy = 17, r.height() // 2
        path = QPainterPath()
        if g.is_expanded():
            path.moveTo(cx - 4, cy - 2)
            path.lineTo(cx + 4, cy - 2)
            path.lineTo(cx, cy + 3)
        else:
            path.moveTo(cx - 2, cy - 4)
            path.lineTo(cx + 3, cy)
            path.lineTo(cx - 2, cy + 4)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(muted)
        p.drawPath(path)

        cols = self._columns()
        base = QFont(self.font())
        fm_base = p.fontMetrics()
        empty = g.has_result() and g.total_size() <= 0

        # name + subtitle
        name_font = QFont(base)
        name_font.setBold(True)
        name_font.setPointSizeF(base.pointSizeF() + 0.5 if base.pointSizeF() > 0 else 10)
        nr = cols["name"]
        p.setFont(name_font)
        p.setPen(muted if empty else text)
        nfm = p.fontMetrics()
        sub = g.subtitle()
        if sub:
            top = QRect(nr.left(), 7, nr.width(), nfm.height())
            p.drawText(top, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       nfm.elidedText(g.name(), Qt.TextElideMode.ElideRight, nr.width()))
            small = QFont(base)
            if base.pointSizeF() > 0:
                small.setPointSizeF(max(7.5, base.pointSizeF() - 0.5))
            p.setFont(small)
            sfm = p.fontMetrics()
            p.setPen(muted)
            bottom = QRect(nr.left(), top.bottom() + 2, nr.width(), sfm.height())
            p.drawText(bottom, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       sfm.elidedText(sub, Qt.TextElideMode.ElideRight, nr.width()))
        else:
            p.drawText(nr, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       nfm.elidedText(g.name(), Qt.TextElideMode.ElideRight, nr.width()))

        # size
        p.setFont(name_font if not empty else base)
        p.setPen(muted if (empty or not g.has_result()) else text)
        p.drawText(cols["size"], int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                   g.size_text())

        safety = g.safety()
        colour = QColor(semantic(_MEANING.get(safety, "error")))

        # share bar
        sr = cols["share"]
        if sr.width() > 0 and g.has_result() and not empty:
            p.setFont(base)
            pct_w = fm_base.horizontalAdvance("100%") + 6
            track = QRect(sr.left(), sr.center().y() - 3, max(0, sr.width() - pct_w), 6)
            back = QColor(outline)
            back.setAlpha(90)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(back)
            p.drawRoundedRect(QRectF(track), 3, 3)
            share = max(0.0, min(1.0, g.share()))
            if share > 0:
                fill = QRect(track.left(), track.top(), max(3, int(track.width() * share)), 6)
                p.setBrush(colour)
                p.drawRoundedRect(QRectF(fill), 3, 3)
            p.setPen(muted)
            p.drawText(QRect(track.right() + 4, sr.top(), pct_w, sr.height()),
                       int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                       g.share_text())

        # badge
        br = cols["badge"]
        if g.has_result() and not empty:
            p.setFont(base)
            label = BADGE.get(safety, BADGE["danger"])
            w = min(br.width(), fm_base.horizontalAdvance(label) + 18)
            pill = QRectF(br.left(), br.center().y() - 10, w, 20)
            tint = QColor(colour)
            tint.setAlpha(36)
            p.setBrush(tint)
            p.setPen(QPen(colour, 1))
            p.drawRoundedRect(pill, 10, 10)
            p.setPen(colour)
            p.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), label)

        # count / state
        p.setFont(base)
        p.setPen(muted)
        p.drawText(cols["count"], int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   fm_base.elidedText(g.count_text(), Qt.TextElideMode.ElideRight,
                                      cols["count"].width()))

        p.end()

    def sizeHint(self) -> QSize:
        return QSize(720, self.HEIGHT)


def row_columns(r: QRect) -> dict:
    """Column rectangles of a category row of rect ``r`` (shared with ColumnHeader)."""
    H = _CategoryHeader
    right = r.right() - 4
    count = QRect(right - H.COL_COUNT, 0, H.COL_COUNT, r.height())
    badge = QRect(count.left() - H.GAP - H.COL_BADGE, 0, H.COL_BADGE, r.height())
    share = QRect(badge.left() - H.GAP - H.COL_SHARE, 0, H.COL_SHARE, r.height())
    size = QRect(share.left() - H.GAP - H.COL_SIZE, 0, H.COL_SIZE, r.height())
    name = QRect(34, 0, max(40, size.left() - H.GAP - 34), r.height())
    if name.width() < 170:      # narrow pane: drop the share bar, keep the badge
        share = QRect(badge.left(), 0, 0, r.height())
        size = QRect(badge.left() - H.GAP - H.COL_SIZE, 0, H.COL_SIZE, r.height())
        name = QRect(34, 0, max(40, size.left() - H.GAP - 34), r.height())
    return {"name": name, "size": size, "share": share, "badge": badge, "count": count}


class _ColumnCanvas(QWidget):
    TITLES = (("name", "Category", Qt.AlignmentFlag.AlignLeft),
              ("size", "Size", Qt.AlignmentFlag.AlignRight),
              ("share", "Share of total", Qt.AlignmentFlag.AlignLeft),
              ("badge", "Safety", Qt.AlignmentFlag.AlignLeft),
              ("count", "Found", Qt.AlignmentFlag.AlignLeft))
    TIPS = {"name": "The category, and what the research says it is.",
            "size": "Measured by the last scan; each path counted once across categories.",
            "share": "Share of everything this scan found reclaimable.",
            "badge": "The most cautious rating of anything in the category: catalog and "
                     "research combined, never relaxed.",
            "count": "How many separate locations the category found."}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            for key, rect in row_columns(self.rect()).items():
                if rect.contains(event.pos()):
                    QToolTip.showText(event.globalPos(), self.TIPS[key], self)
                    return True
            QToolTip.hideText()
            return True
        return super().event(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        font = QFont(self.font())
        if font.pointSizeF() > 0:
            font.setPointSizeF(max(7.5, font.pointSizeF() - 0.5))
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(chrome("text_muted")))
        cols = row_columns(self.rect())
        for key, title, align in self.TITLES:
            rect = cols[key]
            if rect.width() <= 0:
                continue
            p.drawText(rect, int(align | Qt.AlignmentFlag.AlignVCenter), title.upper())
        line = QColor(chrome("outline"))
        line.setAlpha(110)
        p.setPen(QPen(line, 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


class ColumnHeader(QWidget):
    """Column titles lined up with every CategoryGroup row below them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(2)
        self.canvas = _ColumnCanvas(self)
        lay.addWidget(self.canvas, 1)
        lay.addSpacing(30)
        lay.addSpacing(30)

    def update(self):
        self.canvas.update()
        super().update()


# ── The category ─────────────────────────────────────────────────────────

class CategoryGroup(QWidget):
    """One category: a painted row, an action menu and a lazily built path tree.

    Lifecycle:
        - Auto-refresh is controlled externally via start_auto_refresh() /
          stop_auto_refresh(). Do NOT start timers in __init__.
        - scan() and the clean run in background workers, tracked in
          ``self._workers`` and cancelled by ``cancel()``.
    """

    # Emitted when scan completes: (item_count, total_size)
    scan_done = pyqtSignal(int, int)
    # Emitted when a clean completes: the bytes MEASURED as freed.
    freed_bytes = pyqtSignal(int)
    # "Review on the area tab" -- the owner decides where that is.
    clicked = pyqtSignal()
    # The expanded state changed (the overview re-lays out its list).
    expanded_changed = pyqtSignal(bool)

    def __init__(
        self,
        group_name: str,
        scanner_fn,
        auto_refresh: bool = False,
        refresh_interval_ms: int = 30_000,
        parent=None,
        *,
        base_safety: str = "",
        tab_name: str = "",
        catalog_id: str = "",
    ):
        super().__init__(parent)
        self._group_name = group_name
        self._scanner_fn = scanner_fn
        self._results = None
        self._scanning = False
        self._cleaning = False
        self._syncing_ticks = False
        self._auto_refresh_enabled = auto_refresh
        self._refresh_interval_ms = refresh_interval_ms
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_timer_refresh)
        self._workers: List[Worker] = []
        self._expanded = False
        self._tree_widget: Optional[QTreeWidget] = None
        self._share = 0.0
        self._state = ""
        self._generation = 0
        self._tab_name = tab_name
        self._base_safety = base_safety or self._spec_attr("safety", "") or ""
        self._kb = _knowledge()
        self._kb_id = catalog_id or (
            self._kb.id_for_scanner(scanner_fn) if (self._kb and scanner_fn) else "")
        self._entry = self._kb.entry_for(self._kb_id) if (self._kb and self._kb_id) else None
        self._setup_ui()

    # ── Facts the row shows ───────────────────────────────────────────────

    def _spec_attr(self, name: str, default=""):
        spec = getattr(self._scanner_fn, "spec", None)
        return getattr(spec, name, default) if spec is not None else default

    def catalog_id(self) -> str:
        return self._kb_id

    def name(self) -> str:
        return self._group_name

    def description(self) -> str:
        desc = self._spec_attr("description", "") or self._spec_attr("label", "")
        if not desc and self._kb_id:
            from modules.cleanup.cleanup_scanner.catalog import load_catalog
            spec = load_catalog().get(self._kb_id)
            desc = getattr(spec, "description", "") or getattr(spec, "label", "")
        if not desc and self._scanner_fn is not None:
            doc = (getattr(self._scanner_fn, "__doc__", "") or "").strip()
            desc = doc.splitlines()[0].strip() if doc else ""
        return desc

    def subtitle(self) -> str:
        if self._state and self._state.startswith(("Scan error", "Clean error")):
            return self._state
        if self._entry is not None and self._entry.what:
            return first_sentence(self._entry.what)
        desc = first_sentence(self.description())
        return desc or "Not yet researched"

    def has_result(self) -> bool:
        return self._results is not None

    def items(self) -> list:
        return list(getattr(self._results, "items", None) or [])

    def total_size(self) -> int:
        return int(getattr(self._results, "total_size", 0) or 0) if self._results else 0

    def size_text(self) -> str:
        if self._scanning and not self.has_result():
            return "…"
        if not self.has_result():
            return "—"
        return format_size(self.total_size())

    def set_share(self, fraction: float) -> None:
        """This category's share of everything reclaimable (0..1)."""
        self._share = max(0.0, min(1.0, float(fraction or 0.0)))
        if self._tree_widget is not None and self._results is not None:
            self._fill_tree(self._results)
        self._header.update()

    def share(self) -> float:
        return self._share

    def share_text(self) -> str:
        pct = self._share * 100
        if self.total_size() <= 0:
            return "0%"
        return "<1%" if pct < 1 else f"{pct:.0f}%"

    def item_safety(self, item) -> str:
        """The more cautious of the item's, the catalog's and the research's rating."""
        catalog = most_cautious([getattr(item, "safety", "safe"), self._base_safety])
        if self._kb is None:
            return catalog
        return most_cautious([catalog, self._kb.effective_safety(catalog, self._entry)])

    def safety(self) -> str:
        """The badge: the most cautious level among what was found (never relaxed)."""
        items = self.items()
        if items:
            return most_cautious(self.item_safety(i) for i in items)
        base = self._base_safety or "safe"
        return self._kb.effective_safety(base, self._entry) if self._kb else base

    def safety_mix(self) -> dict:
        mix: dict = {}
        for i in self.items():
            level = self.item_safety(i)
            mix[level] = mix.get(level, 0) + max(0, int(i.size))
        return mix

    def count_text(self) -> str:
        if self._state and not self._state.startswith(("Scan error", "Clean error")):
            return self._state
        if not self.has_result():
            return "Not scanned"
        n = len(self.items())
        if n == 0:
            return "Nothing found"
        return f"{n} location" + ("" if n == 1 else "s")

    def tooltip_for(self, region: str = "name") -> str:
        if region == "badge" and self.has_result() and self.items():
            level = self.safety()
            mix = self.safety_mix()
            parts = [f"<b>{escape(BADGE.get(level, ''))}</b><p>{escape(SAFETY_HELP.get(level, ''))}</p>"]
            if len(mix) > 1:
                parts.append("<p>" + " · ".join(
                    f"{escape(BADGE[k].split(' ', 1)[1])} {escape(format_size(v))}"
                    for k, v in sorted(mix.items(), key=lambda kv: _rank(kv[0]))) + "</p>")
            parts.append("<p>The badge shows the most cautious rating of anything in this "
                         "category; research can only make it more cautious, never less.</p>")
            return "".join(parts)
        if region == "share" and self.has_result():
            return (f"<b>{escape(self.share_text())}</b> of everything this scan found "
                    "reclaimable (each path counted once).")
        if region == "size" and self.has_result():
            return (f"<b>{escape(format_size(self.total_size()))}</b> "
                    f"({self.total_size():,} bytes) measured by the last scan.")
        return self.row_tooltip()

    def row_tooltip(self) -> str:
        level = self.safety()
        size = format_size(self.total_size()) if self.has_result() else "not scanned yet"
        if self._kb is not None:
            html = self._kb.tooltip_html(self._entry, self._group_name,
                                         BADGE.get(level, level), size)
            if self._entry is None and self.description():
                html = html.replace("<p>Not yet researched</p>",
                                    f"<p>{escape(self.description())}</p>"
                                    "<p><i>Not yet researched</i></p>", 1)
        else:
            html = (f"<b>{escape(self._group_name)}</b><p>{escape(self.description())}</p>"
                    "<p><i>Not yet researched</i></p>")
        extra = f"<p>Catalog id: {escape(self._kb_id)}</p>" if self._kb_id else ""
        return f"<div style='max-width:420px'>{html}{extra}</div>"

    # ── Public API ────────────────────────────────────────────────────────

    def start_auto_refresh(self) -> None:
        """Start the auto-refresh timer. Safe to call even if already started."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(self._refresh_interval_ms)

    def stop_auto_refresh(self) -> None:
        self._refresh_timer.stop()

    def is_auto_refresh_enabled(self) -> bool:
        return self._auto_refresh_enabled

    def scan(self) -> None:
        """Trigger a background scan. Idempotent — ignores if already scanning."""
        if self._scanning:
            return
        self._do_scan()

    def clean_all(self) -> None:
        """Clean all ticked rows, after the person confirms it."""
        self._confirm_and_clean()

    def cancel(self) -> None:
        """Cancel any in-flight workers."""
        self._generation += 1
        # Cancel, but keep each worker referenced until its own `finished`
        # arrives (_track_worker removes it then). Clearing the list dropped the
        # last Python reference to a still-running worker: when it ended, its
        # signals object was freed while its queued `cancelled` was still
        # waiting, and delivering it ran a freed closure -- an access violation
        # (test_cleanup_final_review_ui, every run, 2026-10-21).
        for w in list(self._workers):
            w.cancel()
        self._scanning = False
        # A cancelled Worker emits `cancelled`, never `result`: nothing else
        # would ever clear this, and _do_clean_all would refuse forever.
        self._cleaning = False
        self._set_state("")

    def paintEvent(self, event):
        """A hairline under the whole category (row, buttons and path list)."""
        super().paintEvent(event)
        p = QPainter(self)
        if self._header._hover or self._expanded:
            tint = QColor(chrome("outline"))
            tint.setAlpha(40 if self._header._hover else 26)
            p.fillRect(0, 0, self.width(), self._header.height(), tint)
        line = QColor(chrome("outline"))
        line.setAlpha(70)
        p.setPen(QPen(line, 1))
        y = self.height() - 1
        p.drawLine(8, y, self.width() - 8, y)
        p.end()

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        self._set_expanded(expanded)

    # ── Setup ─────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setObjectName("categoryGroup")
        # Rows sit on their card: no pane-coloured block behind each one.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(2)
        self._header = _CategoryHeader(self)
        self._header.setObjectName("categoryRow")
        self._header.toggled.connect(self._toggle_expand)
        self._header.setAccessibleName(self._group_name)
        row.addWidget(self._header, 1)

        self._info_btn = QToolButton(self)
        self._info_btn.setText("ⓘ")
        self._info_btn.setAutoRaise(True)
        self._info_btn.setFixedSize(30, 30)
        self._info_btn.setToolTip("What this is, what cleaning it costs, the exact paths and "
                                  "the sources the research is based on (F1).")
        self._info_btn.setAccessibleName(f"Details: {self._group_name}")
        self._info_btn.clicked.connect(self.show_details)
        row.addWidget(self._info_btn)

        self._more_btn = QToolButton(self)
        self._more_btn.setText("⋯")
        self._more_btn.setAutoRaise(True)
        self._more_btn.setFixedSize(30, 30)
        self._more_btn.setToolTip("Scan again, clean this category, open its folder, exclude a "
                                  "path or review it on its own tab.")
        self._more_btn.setAccessibleName(f"Actions: {self._group_name}")
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        # The "…" already says "menu"; Qt's own arrow beside it only adds noise.
        self._more_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        self._menu = QMenu(self._more_btn)
        self._menu.aboutToShow.connect(self._fill_menu)
        self._more_btn.setMenu(self._menu)
        row.addWidget(self._more_btn)
        layout.addLayout(row)

        # The path list lives in its own box so a closed row takes no space.
        self._tree_box = QWidget(self)
        self._tree_box.setObjectName("categoryPathsBox")
        self._tree_holder = QVBoxLayout(self._tree_box)
        self._tree_holder.setContentsMargins(34, 4, 8, 10)
        self._tree_box.setVisible(False)
        layout.addWidget(self._tree_box)

        self._err_lbl = QLabel()
        self._err_lbl.setObjectName("statusError")
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setContentsMargins(34, 0, 8, 6)
        self._err_lbl.hide()
        layout.addWidget(self._err_lbl)
        self._refresh_accessible()

    def _refresh_accessible(self) -> None:
        self._header.setAccessibleDescription(
            f"{self.size_text()}, {BADGE.get(self.safety(), '')}, {self.count_text()}. "
            f"{self.subtitle()}")
        self._header.setToolTip("")          # served per region by _CategoryHeader.event

    def _set_state(self, text: str) -> None:
        self._state = text
        self._refresh_accessible()
        self._header.update()

    @property
    def _tree(self) -> QTreeWidget:
        """The path tree, built the first time anything needs it."""
        if self._tree_widget is None:
            self._build_tree()
        return self._tree_widget

    def _build_tree(self) -> None:
        tree = QTreeWidget(self)
        tree.setObjectName("categoryPaths")
        tree.setColumnCount(len(TREE_HEADERS))
        tree.setHeaderLabels(list(TREE_HEADERS))
        h = tree.header()
        h.setSectionResizeMode(COL_PATH, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_SHARE, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(COL_SAFETY, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(COL_VERDICT, QHeaderView.ResizeMode.Interactive)
        h.resizeSection(COL_SHARE, 120)
        h.resizeSection(COL_SAFETY, 96)
        h.resizeSection(COL_VERDICT, 220)
        h.setStretchLastSection(False)
        tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        tree.setUniformRowHeights(True)
        tree.setRootIsDecorated(True)
        tree.setAlternatingRowColors(False)
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._show_context_menu)
        tree.itemChanged.connect(self._on_tick_changed)
        tree.itemActivated.connect(self._activate_row)
        tree.setAccessibleName(f"{self._group_name} paths")
        try:
            from modules.cleanup.components.safety_delegate import SafetyDelegate
            tree.setItemDelegate(SafetyDelegate(tree, safety_column=COL_SAFETY,
                                                share_column=COL_SHARE))
        except ImportError as exc:                     # pragma: no cover
            logger.warning("safety delegate unavailable: %s", exc)
        self._tree_widget = tree
        self._tree_holder.addWidget(tree)
        if self._results is not None:
            self._fill_tree(self._results)

    # ── Auto-refresh ──────────────────────────────────────────────────────

    def _on_timer_refresh(self):
        """Timer-fired refresh — skips if already scanning or cleaning."""
        if self._scanning or self._cleaning:
            return
        self._do_scan()

    # ── Scan ──────────────────────────────────────────────────────────────

    def _do_scan(self):
        if self._scanning or self._scanner_fn is None:
            return
        self._scanning = True
        self._generation += 1
        generation = self._generation
        self._err_lbl.hide()
        self._set_state("Scanning…")

        scanner = self._scanner_fn

        def _run(_worker):
            from modules.cleanup.cleanup_scanner import scan_cache
            return scan_cache.cached_scan(scanner, 0)

        w = Worker(_run)
        def current():
            return _alive(self) and generation == self._generation
        w.signals.result.connect(lambda result: self._on_scan_result(result) if current() else None)
        w.signals.error.connect(lambda error: self._on_scan_error(error) if current() else None)
        w.signals.cancelled.connect(lambda: self._on_scan_cancelled() if current() else None)
        self._track_worker(w)
        QThreadPool.globalInstance().start(w)

    def _on_scan_result(self, result):
        self._scanning = False
        if result is None:
            result = ScanResult(items=[], total_size=0)
        self._state = ""
        self._apply_result(result)
        self.scan_done.emit(len(result.items), result.total_size)

    def _track_worker(self, worker):
        def finished():
            if _alive(self) and worker in self._workers:
                self._workers.remove(worker)
        worker.signals.finished.connect(finished)
        self._workers.append(worker)

    def _on_scan_cancelled(self):
        self._scanning = False
        self._set_state("")

    def set_result(self, result) -> None:
        """Display a ScanResult produced elsewhere (the overview's Scan all),
        so opening this category shows its paths without a re-scan."""
        if result is None:
            result = ScanResult(items=[], total_size=0)
        self._apply_result(result)

    def _apply_result(self, result):
        self._results = result
        if self._tree_widget is not None:
            self._fill_tree(result)
        self._refresh_accessible()
        self._header.update()
        if self._expanded:
            self._fit_tree_height()
            QTimer.singleShot(0, self._fit_tree_height)

    def default_tick(self, item) -> bool:
        """Is this row ticked before anyone touches it?"""
        if not getattr(item, "selected", False):
            return False
        if UNTICK_REVIEW_ROWS and _rank(self.item_safety(item)) > 0:
            return False
        return True

    def _verdict(self) -> str:
        if self._entry is None:
            return "Not yet researched"
        if self._entry.regenerates:
            return "Rebuilt automatically"
        return "Does not come back"

    def _fill_tree(self, result) -> None:
        tree = self._tree_widget
        tree.blockSignals(True)
        try:
            tree.clear()
            items = list(result.items or [])
            if not items:
                return
            from modules.cleanup.components.item_rows import (
                FACTS_ROLE, SIZE_ROLE, SAFETY_ROLE, SHARE_ROLE, RowFacts, collapse_rows,
            )
            total = max(1, int(result.total_size or sum(i.size for i in items)))
            level = most_cautious(self.item_safety(i) for i in items)
            n = len(items)
            root = QTreeWidgetItem([f"All {n} location{'s' if n != 1 else ''}",
                                    format_size(result.total_size), "", BADGE.get(level, ""), ""])
            # NOT auto-tristate: Qt would tick every child from the root, an
            # orphaned VM disk included. _on_tick_changed lets the root reach
            # only the rows the scanner selected.
            root.setFlags(root.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            root.setCheckState(COL_PATH, Qt.CheckState.Checked)
            root.setData(COL_SAFETY, SAFETY_ROLE, level)
            root.setData(COL_SHARE, SAFETY_ROLE, level)
            root.setData(COL_SHARE, SHARE_ROLE, 100.0 * self._share)
            root.setToolTip(COL_SHARE, "This category's share of everything reclaimable.")
            root.setTextAlignment(COL_SIZE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            root_tip = self.row_tooltip()
            for column in range(len(TREE_HEADERS)):
                root.setToolTip(column, root_tip)
            root.setData(COL_SIZE, SIZE_ROLE, int(result.total_size))
            tree.addTopLevelItem(root)
            facts_rows = []
            for item in sorted(items, key=lambda i: -int(i.size)):
                facts = RowFacts.from_item(
                    item, catalog_id=self._kb_id, description=self.description(),
                    category=str(self._spec_attr("category", "") or ""),
                    total_bytes=total, catalog_safety=self._base_safety or None)
                # The header carries the category's research subtitle. The
                # compact, uniformly sized path rows remain a single line.
                facts = dataclasses.replace(facts, selected=self.default_tick(item), subtitle="")
                facts_rows.append(facts)
                safety = facts.safety
                child = QTreeWidgetItem([item.path, format_size(item.size), "",
                                         facts.badge_text, facts.verdict])
                for column in range(len(TREE_HEADERS)):
                    child.setToolTip(column, facts.tooltip + f"<p>{escape(item.path)}</p>")
                child.setData(COL_PATH, FACTS_ROLE, facts)
                child.setData(COL_SIZE, SIZE_ROLE, int(item.size))
                # A scanner leaves selected=False on what must never go in a bulk
                # clean (orphaned VM disks, old profiles); Review/Risky rows start
                # unticked too. Only a person's own tick overrides either.
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(COL_PATH, Qt.CheckState.Checked if self.default_tick(item)
                                    else Qt.CheckState.Unchecked)
                child.setData(COL_PATH, Qt.ItemDataRole.UserRole, item)
                child.setData(COL_SAFETY, SAFETY_ROLE, safety)
                child.setData(COL_SHARE, SAFETY_ROLE, safety)
                child.setData(COL_SHARE, SHARE_ROLE,
                              100.0 * self._share * max(0, int(item.size)) / total)
                child.setTextAlignment(COL_SIZE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                root.addChild(child)
            folded = collapse_rows(facts_rows)
            if folded and folded[0].kind == "summary":
                for j in range(root.childCount()):
                    root.child(j).setHidden(True)
                for facts in folded:
                    row = QTreeWidgetItem([facts.label, facts.size_text if facts.size_bytes else "",
                                           "", facts.badge_text, facts.verdict])
                    row.setData(COL_PATH, FACTS_ROLE, facts)
                    for column in range(len(TREE_HEADERS)):
                        row.setToolTip(column, root_tip + "<p>Activate Show all to review each file.</p>")
                    root.addChild(row)
            self._sync_root_tick(root)
            root.setExpanded(True)
        finally:
            tree.blockSignals(False)

    def _activate_row(self, row, column=0):
        from modules.cleanup.components.item_rows import FACTS_ROLE
        facts = row.data(COL_PATH, FACTS_ROLE)
        if facts is None or facts.kind != "show_all":
            return
        root = row.parent()
        for j in range(root.childCount()):
            child = root.child(j)
            child.setHidden(child.data(COL_PATH, Qt.ItemDataRole.UserRole) is None)
        self._fit_tree_height()

    def _on_scan_error(self, err: str):
        self._scanning = False
        self._set_state("")
        self._err_lbl.setText(f"Scan error: {err}")
        self._err_lbl.show()

    # ── Ticks ─────────────────────────────────────────────────────────────

    def _sync_root_tick(self, root) -> None:
        """Root is ticked iff every row that starts ticked is ticked; the rows
        that start unticked neither tick nor untick it."""
        rows = [root.child(j) for j in range(root.childCount())]
        defaults = [c for c in rows
                    if c.data(COL_PATH, Qt.ItemDataRole.UserRole) is not None
                    and self.default_tick(c.data(COL_PATH, Qt.ItemDataRole.UserRole))]
        is_ticked = [c.checkState(COL_PATH) == Qt.CheckState.Checked for c in (defaults or rows)]
        ticked = all(is_ticked) if defaults else any(is_ticked)
        root.setCheckState(COL_PATH, Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)

    def _on_tick_changed(self, tree_item, column) -> None:
        """A click on the root ticks/unticks only the rows that start ticked
        (unticking clears every row); any other row is ticked by its own click."""
        if self._syncing_ticks or column != COL_PATH:
            return
        self._syncing_ticks = True
        try:
            root = tree_item.parent()
            if root is None:
                want = tree_item.checkState(COL_PATH)
                for j in range(tree_item.childCount()):
                    child = tree_item.child(j)
                    item = child.data(COL_PATH, Qt.ItemDataRole.UserRole)
                    if want == Qt.CheckState.Unchecked:
                        child.setCheckState(COL_PATH, Qt.CheckState.Unchecked)
                    elif item is not None and self.default_tick(item):
                        child.setCheckState(COL_PATH, Qt.CheckState.Checked)
                root = tree_item
            self._sync_root_tick(root)
        finally:
            self._syncing_ticks = False

    def _items_to_clean(self) -> list:
        """The ticked rows, as items the deleter will act on.

        The deleter skips selected=False items, so a row the person ticked by
        hand is passed as a COPY marked selected -- the tick is their choice --
        leaving the scan result itself untouched.
        """
        if self._results is None:
            return []
        tree = self._tree
        chosen: list = []
        for i in range(tree.topLevelItemCount()):
            root = tree.topLevelItem(i)
            for j in range(root.childCount()):
                child = root.child(j)
                item = child.data(COL_PATH, Qt.ItemDataRole.UserRole)
                if item is not None and child.checkState(COL_PATH) == Qt.CheckState.Checked:
                    chosen.append(item if item.selected else dataclasses.replace(item, selected=True))
        return chosen

    # ── Clean ─────────────────────────────────────────────────────────────

    def _confirm_and_clean(self) -> None:
        if self._cleaning or not self._results:
            return
        chosen = self._items_to_clean()
        if not chosen:
            QMessageBox.information(
                self, "Nothing ticked",
                f"No row in “{self._group_name}” is ticked. Open the category and tick "
                "the locations to clean; rows rated Review or Risky start unticked.")
            return
        size = sum(max(0, int(i.size)) for i in chosen)
        review = [i for i in chosen if _rank(self.item_safety(i)) > 0]
        text = (f"Clean {len(chosen)} location(s) in “{self._group_name}”, about "
                f"{format_size(size)}?\n\nFolder roots are kept; links are unlinked, never "
                "followed; cloud-only files and files in use are skipped and reported.")
        if review:
            text += (f"\n\n{len(review)} of them are rated Review or Risky and were ticked "
                     "by hand.")
        box = QMessageBox(QMessageBox.Icon.Warning, "Clean this category", text,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._do_clean_all()

    def _do_clean_all(self):
        if self._cleaning or not self._results:
            return

        checked = self._items_to_clean()
        if not checked:
            return

        self._cleaning = True
        self._generation += 1
        generation = self._generation
        self._err_lbl.hide()
        self._set_state("Cleaning…")

        def _run(worker):
            from modules.cleanup import cleanup_scanner as cs
            report, refused, per_item = cs.delete_items_report(
                checked, stop_wuauserv=False, cancelled=lambda: worker.is_cancelled)
            return (*cs.item_counts(refused, per_item), report.freed)

        # CLOSURES connected to worker signals: Qt cannot auto-disconnect
        # them when this widget is destroyed, so each checks it is alive.
        def _done(result):
            if not _alive(self) or generation != self._generation:
                return
            deleted, errors, freed = result
            self._cleaning = False
            msg = f"Cleaned {deleted} · {format_size(freed)} freed"
            if errors:
                self._err_lbl.setText(f"{errors} location(s) could not be fully deleted "
                                      "(in use, refused or protected). See the History tab.")
                self._err_lbl.show()
            self._set_state(msg)
            self.freed_bytes.emit(freed)
            self._do_scan()

        def _err(err: str):
            if not _alive(self) or generation != self._generation:
                return
            self._cleaning = False
            self._set_state("")
            self._err_lbl.setText(f"Clean error: {err}")
            self._err_lbl.show()

        def _cancelled():
            if not _alive(self) or generation != self._generation:
                return
            self._cleaning = False
            self._set_state("")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        w.signals.cancelled.connect(_cancelled)
        self._track_worker(w)
        QThreadPool.globalInstance().start(w)

    # ── Expand / collapse ────────────────────────────────────────────────

    def _toggle_expand(self):
        self._set_expanded(not self._expanded)

    def _set_expanded(self, expanded: bool):
        expanded = bool(expanded)
        changed = expanded != self._expanded
        self._expanded = expanded
        if expanded or self._tree_widget is not None:
            self._tree.setVisible(True)
            self._tree_box.setVisible(expanded)
        if expanded:
            for i in range(self._tree.topLevelItemCount()):
                self._tree.topLevelItem(i).setExpanded(True)
            self._fit_tree_height()
            QTimer.singleShot(0, self._fit_tree_height)
        self._header.update()
        if changed:
            self.expanded_changed.emit(expanded)

    _MAX_TREE_HEIGHT = 420  # px; longer lists cap here and scroll internally

    def _visible_row_count(self) -> int:
        rows = 0
        tree = self._tree
        for i in range(tree.topLevelItemCount()):
            root = tree.topLevelItem(i)
            rows += 1
            if root.isExpanded():
                rows += sum(not root.child(j).isHidden() for j in range(root.childCount()))
        return rows

    def _fit_tree_height(self) -> None:
        """Grow the tree to fit its rows so the outer page scrolls; cap long lists."""
        if not _alive(self) or not self._expanded or self._tree_widget is None:
            return
        tree = self._tree_widget
        rows = self._visible_row_count()
        if rows == 0:
            tree.setFixedHeight(40)
            return
        row_h = tree.sizeHintForRow(0) or 24
        header_h = 0 if tree.header().isHidden() else tree.header().sizeHint().height()
        content_h = header_h + rows * row_h + 2 * tree.frameWidth() + 4
        if content_h <= self._MAX_TREE_HEIGHT:
            tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            tree.setFixedHeight(content_h)
        else:
            tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            tree.setFixedHeight(self._MAX_TREE_HEIGHT)

    def _expand_all(self):
        self._set_expanded(True)

    def _collapse_all(self):
        self._set_expanded(False)

    # ── Menus and details ────────────────────────────────────────────────

    def _paths(self) -> List[str]:
        return [i.path for i in sorted(self.items(), key=lambda i: -int(i.size))]

    def _fill_menu(self) -> None:
        m = self._menu
        m.clear()
        busy = self._scanning or self._cleaning
        a = m.addAction("Scan again")
        a.setToolTip("Measure this category again (a cached result up to 60 s old may be reused).")
        a.setEnabled(not busy and self._scanner_fn is not None)
        a.triggered.connect(self.scan)
        a = m.addAction("Clean this category…")
        a.setEnabled(not busy and bool(self.items()))
        a.triggered.connect(self._confirm_and_clean)
        m.addSeparator()
        a = m.addAction("Collapse" if self._expanded else "Show paths")
        a.triggered.connect(self._toggle_expand)
        a = m.addAction("Details…")
        a.triggered.connect(self.show_details)
        paths = self._paths()
        a = m.addAction("Open folder" if len(paths) <= 1 else "Open largest folder")
        a.setEnabled(bool(paths))
        if paths:
            a.triggered.connect(lambda _=False, p=paths[0]: self._open_folder(p))
        if len(paths) == 1:
            a = m.addAction("Exclude this path…")
            a.triggered.connect(lambda _=False, p=paths[0]: self._exclude(p))
        if self._tab_name:
            m.addSeparator()
            a = m.addAction(f"Review on the {self._tab_name} tab")
            a.triggered.connect(self.clicked.emit)
        for action in m.actions():
            if not action.isSeparator() and not action.toolTip():
                action.setToolTip(action.text() + ": " + self.subtitle())
        m.setToolTipsVisible(True)

    def _show_context_menu(self, pos):
        tree = self._tree
        node = tree.itemAt(pos)
        if not node:
            return
        scan_item = node.data(COL_PATH, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if scan_item is None:
            menu.addAction("Details…").triggered.connect(self.show_details)
        else:
            menu.addAction("Open in Explorer").triggered.connect(
                lambda _=False, p=scan_item.path: self._open_folder(p))
            menu.addAction("Copy path").triggered.connect(
                lambda _=False, p=scan_item.path: self._copy_path(p))
            menu.addAction("Exclude this path…").triggered.connect(
                lambda _=False, p=scan_item.path: self._exclude(p))
            menu.addSeparator()
            menu.addAction("Details…").triggered.connect(self.show_details)
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _open_folder(self, path: str) -> None:
        target = path if os.path.isdir(path) else os.path.dirname(path)
        try:
            os.startfile(target)
        except OSError as exc:
            logger.warning("cannot open %s: %s", target, exc)
            QMessageBox.warning(self, "Open folder", f"Could not open “{target}”:\n{exc}")

    def _copy_path(self, path: str) -> None:
        QApplication.clipboard().setText(path)

    def _exclude(self, path: str) -> None:
        from modules.cleanup.cleanup_scanner import exclusions
        box = QMessageBox(QMessageBox.Icon.Question, "Exclude path",
                          f"Never offer “{path}” (or anything under it) again, on any "
                          "Cleanup tab?",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        if not exclusions.add_exclusion(path):
            QMessageBox.information(self, "Already excluded",
                                    f"“{path}” is already on the exclusion list.")
            return
        logger.info("Cleanup overview: excluded %s from all future scans", path)
        self.scan()

    def detail_facts(self):
        """A RowFacts for the whole category (paths as children)."""
        from modules.cleanup.components.item_rows import RowFacts
        items = sorted(self.items(), key=lambda i: -int(i.size))
        total = self.total_size()
        children = tuple(
            dataclasses.replace(
                RowFacts.from_item(i, i.path, catalog_id=self._kb_id, description=self.description(),
                                   total_bytes=total, catalog_safety=self._base_safety or None),
                selected=self.default_tick(i))
            for i in items)
        level = self.safety()
        first = items[0] if items else None
        return RowFacts(
            label=self._group_name,
            path=first.path if (first is not None and len(items) == 1) else "",
            size_bytes=total,
            safety=level,
            catalog_safety=most_cautious([self._base_safety or "safe"]
                                         + [getattr(i, "safety", "safe") for i in items]),
            catalog_id=self._kb_id,
            category=str(self._spec_attr("category", "") or ""),
            description=self.description(),
            selected=all(child.selected for child in children),
            unselected_reason="Review and Risky rows start unticked; scanner exclusions are also respected.",
            share_percent=self._share * 100,
            subtitle=self.subtitle(),
            verdict=self._verdict(),
            tooltip=self.row_tooltip(),
            is_dir=bool(first.is_dir) if first is not None else True,
            keep_root=bool(getattr(first, "keep_root", True)) if first is not None else True,
            keep_newer_than_hours=int(getattr(first, "keep_newer_than_hours", 0) or 0)
            if first is not None else 0,
            entry=self._entry,
            kind="summary" if len(items) != 1 else "item",
            children=children if len(items) > 1 else (),
        )

    def show_details(self) -> None:
        """The detail pane for this category, in a dialog."""
        from modules.cleanup.components.detail_pane import DetailPane
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{self._group_name} — details")
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        pane = DetailPane(dlg)
        pane.set_row(self.detail_facts())
        pane.setMinimumWidth(420)
        # Keep long unbroken Windows paths from widening the dialog; WP3 owns
        # the pane itself. A scroll container also bounds categories with many paths.
        for label in pane.findChildren(QLabel):
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for button in pane.path_buttons:
            button.setToolTip(button.accessibleName())
        pane.open_folder.connect(self._open_folder)
        pane.copy_path.connect(self._copy_path)
        pane.exclude_path.connect(self._exclude)
        scroll = QScrollArea(dlg)
        scroll.setWidgetResizable(True)
        scroll.setWidget(pane)
        lay.addWidget(scroll)
        dlg.resize(640, 560)
        dlg.show()
        self._details_dialog = dlg
