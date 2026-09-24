r"""Desktop coordinates to canvas pixels, and back.

Kept apart from the widget that draws with it, so the arithmetic can be
tested with no display — the same split the rest of this module uses.

The reason it is worth testing at all: the primary monitor is at (0,0) **by
definition**, so anything to its left or above it has negative coordinates.
Scaling by the total width while ignoring the origin works perfectly for a
left-to-right desk and puts a monitor off the canvas the moment someone
arranges theirs the other way round.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Rect = Tuple[int, int, int, int]        # x, y, width, height, in desktop space
Point = Tuple[float, float]


@dataclass(frozen=True)
class Transform:
    """How to get from desktop space to canvas space."""

    origin_x: int
    origin_y: int
    scale: float
    offset_x: float
    offset_y: float


def bounding_box(rects: Sequence[Rect]) -> Optional[Rect]:
    """The rectangle containing every monitor, or None for no monitors.

    Returned as (x, y, width, height) with x and y possibly negative — that
    origin is the part callers forget.
    """
    if not rects:
        return None
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    return (left, top, right - left, bottom - top)


def fit(rects: Sequence[Rect], canvas: Tuple[int, int],
        margin: int = 10) -> Optional[Transform]:
    """A transform that centres the whole layout inside `canvas`.

    None when there is nothing to draw or nowhere to draw it — callers check
    rather than dividing by zero.
    """
    bounds = bounding_box(rects)
    if bounds is None:
        return None
    canvas_w, canvas_h = canvas
    usable_w = canvas_w - 2 * margin
    usable_h = canvas_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        return None

    _, _, width, height = bounds
    if width <= 0 or height <= 0:
        return None

    scale = min(usable_w / width, usable_h / height)
    drawn_w = width * scale
    drawn_h = height * scale
    return Transform(
        origin_x=bounds[0],
        origin_y=bounds[1],
        scale=scale,
        offset_x=margin + (usable_w - drawn_w) / 2,
        offset_y=margin + (usable_h - drawn_h) / 2,
    )


def to_canvas(rect: Rect, transform: Transform) -> Tuple[float, float, float, float]:
    x, y, w, h = rect
    return (
        (x - transform.origin_x) * transform.scale + transform.offset_x,
        (y - transform.origin_y) * transform.scale + transform.offset_y,
        w * transform.scale,
        h * transform.scale,
    )


def to_canvas_point(point: Point, transform: Transform) -> Point:
    x, y = point
    return ((x - transform.origin_x) * transform.scale + transform.offset_x,
            (y - transform.origin_y) * transform.scale + transform.offset_y)


def to_desktop_point(point: Point, transform: Transform) -> Point:
    x, y = point
    return ((x - transform.offset_x) / transform.scale + transform.origin_x,
            (y - transform.offset_y) / transform.scale + transform.origin_y)


def snap(moved: Rect, others: Sequence[Rect], threshold: int = 32) -> Rect:
    """Pull a dragged monitor onto its neighbours' edges.

    Windows leaves gaps and overlaps alone; they make the mouse behave
    strangely at the seam. Snapping both axes independently means a monitor
    can align its top edge without being forced to touch horizontally.
    """
    x, y, w, h = moved
    best_dx = None
    best_dy = None

    for ox, oy, ow, oh in others:
        for candidate in (ox + ow, ox - w, ox):
            delta = candidate - x
            if abs(delta) <= threshold and (best_dx is None
                                            or abs(delta) < abs(best_dx)):
                best_dx = delta
        for candidate in (oy, oy + oh, oy - h):
            delta = candidate - y
            if abs(delta) <= threshold and (best_dy is None
                                            or abs(delta) < abs(best_dy)):
                best_dy = delta

    return (x + (best_dx or 0), y + (best_dy or 0), w, h)


def overlap(a: Rect, b: Rect) -> bool:
    """True when two rectangles share area. Touching edges do not count."""
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def resolve_overlaps(moved: Rect, others: Sequence[Rect]) -> Rect:
    """Where a dropped monitor really lands: the nearest slot that is clear.

    Windows will not lay two displays over the same area, so a drop onto
    another monitor has to mean "next to it". Every edge of every other
    monitor offers a slot (left of it, right of it, above, below); the slots
    that overlap nothing are the candidates and the nearest one wins.
    Choosing among clear slots -- rather than pushing clear of one monitor at
    a time -- cannot end up under a shorter neighbour or bounce between two.

    Vertical moves cost triple. Monitors sit side by side far more often than
    stacked, and a horizontal drag onto a neighbour that previewed a landing
    BELOW it (a few pixels cheaper) read as the map misbehaving; a genuinely
    downward drop is still much nearer vertically than sideways.

    Returns `moved` unchanged when it already overlaps nothing, and also when
    no clear slot exists -- the caller re-checks with `overlap`.
    """
    x, y, w, h = moved
    if not any(overlap(moved, o) for o in others):
        return moved
    candidates = []
    for ox, oy, ow, oh in others:
        candidates += [(ox - w, y, abs(ox - w - x)),
                       (ox + ow, y, abs(ox + ow - x)),
                       (x, oy - h, 3 * abs(oy - h - y)),
                       (x, oy + oh, 3 * abs(oy + oh - y))]
    clear = [(cx, cy, cost) for cx, cy, cost in candidates
             if not any(overlap((cx, cy, w, h), o) for o in others)]
    if not clear:
        return moved
    cx, cy, _cost = min(clear, key=lambda c: c[2])
    return (cx, cy, w, h)


def normalise_to_primary(layout: dict, primary_id) -> dict:
    """Shift a whole layout so the primary monitor sits at (0, 0).

    `layout` maps a monitor id to its (x, y, w, h). Windows defines the primary
    as the display at the origin, so dragging the primary anywhere else is only
    expressible as "everyone else moves the other way". Returns new (x, y, w,
    h) tuples; an unknown `primary_id` shifts nothing.
    """
    if primary_id not in layout:
        return dict(layout)
    dx, dy = layout[primary_id][0], layout[primary_id][1]
    return {key: (r[0] - dx, r[1] - dy, r[2], r[3])
            for key, r in layout.items()}


def rects_from_monitors(monitors) -> List[Rect]:
    """Desktop rectangles for the active monitors in a topology.

    Inactive monitors have no position and no size, so they are not on the
    map — the canvas parks them beside it instead.
    """
    rects = []
    for monitor in monitors:
        if not monitor.active or not monitor.position or not monitor.resolution:
            continue
        rects.append((monitor.position[0], monitor.position[1],
                      monitor.resolution[0], monitor.resolution[1]))
    return rects
