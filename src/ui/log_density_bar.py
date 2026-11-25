"""Interactive time-density histogram bar for log panes.

Draws a high-performance, dark-themed micro-histogram of log events across
their loaded time window, colored by severity. Clicking any bucket jumps to
that moment or filters the view.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt
from typing import List, Optional, Tuple

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import QPushButton, QToolTip, QWidget

from core.table_ui import set_role
from core.semantic_colors import semantic, chrome
from core.types import LogEntry

DEFAULT_HEIGHT = 34
BUCKET_COUNT = 96


@dataclass(frozen=True)
class TimeBucket:
    start: datetime
    end: datetime
    total: int
    critical: int
    errors: int
    warnings: int
    info: int

    @property
    def error_density(self) -> int:
        return self.critical + self.errors


def _calc_bar_height(count: int, busiest: int, max_h: int) -> int:
    if count <= 0 or busiest <= 0 or max_h <= 0:
        return 0
    return max(int(max_h * sqrt(count / busiest)), 1)


class LogDensityBar(QWidget):
    """Sleek micro-histogram displaying log event density over time."""

    bucket_clicked = pyqtSignal(object, object)       # (datetime_start, datetime_end)
    jump_requested = pyqtSignal(object)               # datetime
    time_range_selected = pyqtSignal(object, object)  # (datetime_start, datetime_end)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._buckets: List[TimeBucket] = []
        self._first_time: Optional[datetime] = None
        self._last_time: Optional[datetime] = None

        # Drag selection state
        self._is_dragging: bool = False
        self._drag_start_x: int = 0
        self._drag_current_x: int = 0
        self._selection_range: Optional[Tuple[datetime, datetime]] = None

        self.setFixedHeight(DEFAULT_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setToolTip("Log Event Timeline: click or drag to filter/jump to a time window.")

        # Overlay Jump to Peak Burst button
        self.peak_burst_btn = QPushButton("🎯 Jump to Peak Burst", self)
        set_role(self.peak_burst_btn, "statusError")
        self.peak_burst_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.peak_burst_btn.setToolTip("Jump to the time bucket with the highest error density")
        self.peak_burst_btn.clicked.connect(self.jump_to_peak_burst)
        self.peak_burst_btn.setVisible(False)
        self._peak_btn = self.peak_burst_btn

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        btn_w = 156
        btn_h = 22
        if self.width() > btn_w + 60:
            self.peak_burst_btn.setGeometry(self.width() - btn_w - 6, (self.height() - btn_h) // 2, btn_w, btn_h)
        else:
            self.peak_burst_btn.setGeometry(0, 0, 0, 0)

    def set_entries(self, entries: List[LogEntry]) -> None:
        """Compute time buckets across valid entries."""
        valid_stamps = [e.timestamp for e in entries if getattr(e, "timestamp", None) and e.timestamp.year >= 2000]
        if not valid_stamps:
            self._buckets = []
            self._first_time = None
            self._last_time = None
            self._selection_range = None
            self.peak_burst_btn.setVisible(False)
            self.update()
            return

        first, last = min(valid_stamps), max(valid_stamps)
        self._first_time = first
        self._last_time = last
        span = (last - first).total_seconds()
        n = BUCKET_COUNT
        width = (span / n) if span > 0 else 0.0

        totals = [0] * n
        crits = [0] * n
        errs = [0] * n
        warns = [0] * n
        infos = [0] * n

        for e in entries:
            t = getattr(e, "timestamp", None)
            if not t or t.year < 2000:
                continue
            if width > 0:
                idx = int((t - first).total_seconds() / width)
                idx = max(0, min(idx, n - 1))
            else:
                idx = 0

            totals[idx] += 1
            lvl = str(getattr(e, "level", "")).lower()
            if lvl == "critical":
                crits[idx] += 1
            elif lvl == "error":
                errs[idx] += 1
            elif lvl == "warning":
                warns[idx] += 1
            else:
                infos[idx] += 1

        made: List[TimeBucket] = []
        for i in range(n):
            b_start = first + timedelta(seconds=width * i)
            b_end = first + timedelta(seconds=width * (i + 1)) if width > 0 else last
            made.append(TimeBucket(
                start=b_start,
                end=b_end,
                total=totals[i],
                critical=crits[i],
                errors=errs[i],
                warnings=warns[i],
                info=infos[i],
            ))
        self._buckets = made

        peak = self.get_peak_burst_bucket()
        has_burst = peak is not None and (peak.error_density > 0 or peak.total > 0)
        self.peak_burst_btn.setVisible(has_burst)
        if peak and peak.error_density > 0:
            self.peak_burst_btn.setToolTip(
                f"Jump to peak error burst at {peak.start.strftime('%Y-%m-%d %H:%M')} "
                f"({peak.error_density} errors/critical)"
            )
        elif peak:
            self.peak_burst_btn.setToolTip(
                f"Jump to busiest bucket at {peak.start.strftime('%Y-%m-%d %H:%M')} "
                f"({peak.total} records)"
            )
        self.update()

    def has_data(self) -> bool:
        return bool(self._buckets and any(b.total > 0 for b in self._buckets))

    def _bucket_at_pos(self, x: int) -> Optional[TimeBucket]:
        if not self._buckets or self.width() <= 0:
            return None
        idx = int(x * len(self._buckets) / self.width())
        idx = max(0, min(idx, len(self._buckets) - 1))
        return self._buckets[idx]

    def format_tooltip(self, bucket: TimeBucket) -> str:
        """Format hover tooltip string: YYYY-MM-DD HH:MM — Total: N, Errors: E, Warnings: W"""
        err_count = bucket.errors + bucket.critical
        return (
            f"{bucket.start.strftime('%Y-%m-%d %H:%M')} — "
            f"Total: {bucket.total}, Errors: {err_count}, Warnings: {bucket.warnings}"
        )

    def get_peak_burst_bucket(self) -> Optional[TimeBucket]:
        """Find the bucket with the highest error density (critical + errors)."""
        if not self._buckets:
            return None
        # 1. Highest error density
        with_errors = [b for b in self._buckets if b.error_density > 0]
        if with_errors:
            return max(with_errors, key=lambda b: (b.error_density, b.total))
        # 2. Fallback to warnings
        with_warns = [b for b in self._buckets if b.warnings > 0]
        if with_warns:
            return max(with_warns, key=lambda b: (b.warnings, b.total))
        # 3. Fallback to busiest total
        with_total = [b for b in self._buckets if b.total > 0]
        if with_total:
            return max(with_total, key=lambda b: b.total)
        return None

    def jump_to_peak_burst(self) -> Optional[TimeBucket]:
        """Immediately scroll to and highlight the time bucket with highest error density."""
        peak = self.get_peak_burst_bucket()
        if peak:
            self._selection_range = (peak.start, peak.end)
            self.update()
            self.jump_requested.emit(peak.start)
        return peak

    def select_time_range(self, start_dt: datetime, end_dt: datetime) -> None:
        """Programmatically select a time range and emit time_range_selected."""
        self._selection_range = (start_dt, end_dt)
        self.update()
        self.time_range_selected.emit(start_dt, end_dt)

    def set_selected_range(self, start_dt: Optional[datetime], end_dt: Optional[datetime]) -> None:
        """Set visual selection range without emitting signals."""
        if start_dt and end_dt:
            self._selection_range = (start_dt, end_dt)
        else:
            self._selection_range = None
        self.update()

    def clear_selection(self) -> None:
        """Clear active visual selection box."""
        self._selection_range = None
        self._is_dragging = False
        self.update()

    @property
    def selected_range(self) -> Optional[Tuple[datetime, datetime]]:
        return self._selection_range

    @property
    def is_peak_burst_visible(self) -> bool:
        return not self.peak_burst_btn.isHidden()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_x = int(event.position().x())
            self._drag_current_x = self._drag_start_x
            self._is_dragging = False
        elif event.button() == Qt.MouseButton.RightButton:
            self.clear_selection()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        curr_x = int(event.position().x())
        if event.buttons() & Qt.MouseButton.LeftButton:
            if not self._is_dragging and abs(curr_x - self._drag_start_x) >= 4:
                self._is_dragging = True
            if self._is_dragging:
                self._drag_current_x = curr_x
                self.update()

                x1 = min(self._drag_start_x, self._drag_current_x)
                x2 = max(self._drag_start_x, self._drag_current_x)
                b1 = self._bucket_at_pos(x1)
                b2 = self._bucket_at_pos(x2)
                if b1 and b2:
                    t1 = min(b1.start, b2.start).strftime("%Y-%m-%d %H:%M")
                    t2 = max(b1.end, b2.end).strftime("%Y-%m-%d %H:%M")
                    QToolTip.showText(self.mapToGlobal(event.position().toPoint()), f"Select: {t1} — {t2}", self)
                return

        bucket = self._bucket_at_pos(curr_x)
        if bucket:
            tip = self.format_tooltip(bucket)
            QToolTip.showText(self.mapToGlobal(event.position().toPoint()), tip, self)
            self.setToolTip(tip)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_dragging:
                x1 = min(self._drag_start_x, self._drag_current_x)
                x2 = max(self._drag_start_x, self._drag_current_x)
                b1 = self._bucket_at_pos(x1)
                b2 = self._bucket_at_pos(x2)
                if b1 and b2:
                    t_start = min(b1.start, b2.start)
                    t_end = max(b1.end, b2.end)
                    self._selection_range = (t_start, t_end)
                    self.time_range_selected.emit(t_start, t_end)
                self._is_dragging = False
                self.update()
            else:
                # Single click
                bucket = self._bucket_at_pos(int(event.position().x()))
                if bucket:
                    self._selection_range = (bucket.start, bucket.end)
                    self.bucket_clicked.emit(bucket.start, bucket.end)
                    self.jump_requested.emit(bucket.start)
                self.update()
        super().mouseReleaseEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and self.has_data():
            pos_x = event.x() if hasattr(event, "x") else (event.pos().x() if hasattr(event, "pos") else 0)
            bucket = self._bucket_at_pos(pos_x)
            if bucket:
                tip = self.format_tooltip(bucket)
                global_pos = (
                    event.globalPos()
                    if hasattr(event, "globalPos")
                    else (
                        event.globalPosition().toPoint()
                        if hasattr(event, "globalPosition")
                        else self.mapToGlobal(event.pos() if hasattr(event, "pos") else QPoint(pos_x, 0))
                    )
                )
                QToolTip.showText(global_pos, tip, self)
                return True
        return super().event(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            w = self.width()
            h = self.height()

            # High-DPI crisp rendering settings
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

            # Background bar
            painter.fillRect(QRect(0, 0, w, h), QColor(chrome("surface_inactive")))

            if not self._buckets or not any(b.total > 0 for b in self._buckets):
                painter.setPen(QColor(chrome("text_muted")))
                font = QFont()
                font.setPointSize(8)
                painter.setFont(font)
                painter.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "No timestamp data for timeline")
                return

            n = len(self._buckets)
            busiest = max(b.total for b in self._buckets) or 1

            # Colors
            col_info = QColor(semantic("info"))
            col_warn = QColor(semantic("warning"))
            col_err = QColor(semantic("error"))
            col_crit = QColor(semantic("error")).darker(110)

            graph_h = h - 12  # leave bottom 12px for time markers

            for idx, b in enumerate(self._buckets):
                if b.total <= 0:
                    continue
                left = int(idx * w / n)
                right = int((idx + 1) * w / n)
                bar_w = max(right - left, 1)

                total_tall = _calc_bar_height(b.total, busiest, graph_h)
                if total_tall <= 0:
                    continue

                curr_y = graph_h - total_tall

                # Proportional sub-bars
                c_tall = int(total_tall * (b.critical / b.total))
                e_tall = int(total_tall * (b.errors / b.total))
                w_tall = int(total_tall * (b.warnings / b.total))
                i_tall = max(total_tall - (c_tall + e_tall + w_tall), 0)

                # Draw Info (bottom of stack)
                if i_tall > 0:
                    painter.fillRect(QRect(left, curr_y + (total_tall - i_tall), bar_w, i_tall), col_info)
                # Draw Warnings
                if w_tall > 0:
                    painter.fillRect(QRect(left, curr_y + c_tall + e_tall, bar_w, w_tall), col_warn)
                # Draw Errors
                if e_tall > 0:
                    painter.fillRect(QRect(left, curr_y + c_tall, bar_w, e_tall), col_err)
                # Draw Critical
                if c_tall > 0:
                    painter.fillRect(QRect(left, curr_y, bar_w, c_tall), col_crit)

            # Draw time range captions at bottom
            painter.setPen(QColor(chrome("text_muted")))
            font = QFont()
            font.setPointSize(7)
            painter.setFont(font)
            if self._first_time and self._last_time:
                t1 = self._first_time.strftime("%m-%d %H:%M")
                t2 = self._last_time.strftime("%m-%d %H:%M")
                painter.drawText(QRect(4, h - 12, 120, 11), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, t1)
                painter.drawText(QRect(w - 124, h - 12, 120, 11), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, t2)

            # Paint Selection Box Overlay
            sel_left = None
            sel_right = None
            if self._is_dragging and w > 0:
                sel_left = max(0, min(self._drag_start_x, self._drag_current_x))
                sel_right = min(w, max(self._drag_start_x, self._drag_current_x))
            elif self._selection_range and self._first_time and self._last_time and w > 0:
                s_dt, e_dt = self._selection_range
                span = (self._last_time - self._first_time).total_seconds()
                if span > 0:
                    f1 = max(0.0, min(1.0, (s_dt - self._first_time).total_seconds() / span))
                    f2 = max(0.0, min(1.0, (e_dt - self._first_time).total_seconds() / span))
                    sel_left = int(min(f1, f2) * w)
                    sel_right = int(max(f1, f2) * w)
                else:
                    sel_left = 0
                    sel_right = w

            if sel_left is not None and sel_right is not None and (sel_right - sel_left) > 0:
                sel_w = max(sel_right - sel_left, 2)
                # Semi-transparent highlight
                painter.fillRect(QRect(sel_left, 0, sel_w, h), QColor(59, 130, 246, 60))
                # Crisp outline
                painter.setPen(QColor(96, 165, 250, 220))
                painter.drawLine(sel_left, 0, sel_left, h - 1)
                painter.drawLine(sel_right - 1, 0, sel_right - 1, h - 1)
                painter.drawLine(sel_left, 0, sel_right - 1, 0)
                painter.drawLine(sel_left, h - 1, sel_right - 1, h - 1)
        finally:
            painter.end()
