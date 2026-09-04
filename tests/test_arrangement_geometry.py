r"""Mapping the desktop onto the canvas, and back.

The canvas draws monitors where they really are. Desktop coordinates are not
a friendly space to draw in: the primary is at (0,0) by definition, so any
monitor placed to its left or above it has **negative** coordinates. This
machine's two active displays sit at (0,0) and (2560,0); drag the LG to the
left of the primary and the layout starts at x=-3440.

Naive scaling — dividing by the total width and ignoring the origin — puts
that monitor off the canvas entirely, and the bug only appears once someone
arranges their desk the other way round. So the origin is part of the
transform, and the round trip is tested with it.
"""
import pytest

from modules.monitor_control import _arrangement_geometry as geo


TWO_ACROSS = [(0, 0, 2560, 1440), (2560, 0, 2560, 1440)]
LEFT_OF_PRIMARY = [(-3440, 0, 3440, 1440), (0, 0, 2560, 1440)]
STACKED = [(0, -1440, 2560, 1440), (0, 0, 2560, 1440)]


def test_the_bounding_box_of_a_simple_layout():
    assert geo.bounding_box(TWO_ACROSS) == (0, 0, 5120, 1440)


def test_the_bounding_box_survives_a_negative_origin():
    assert geo.bounding_box(LEFT_OF_PRIMARY) == (-3440, 0, 6000, 1440)


def test_the_bounding_box_survives_a_monitor_above_the_primary():
    assert geo.bounding_box(STACKED) == (0, -1440, 2560, 2880)


def test_an_empty_layout_has_no_bounding_box():
    assert geo.bounding_box([]) is None


def test_the_scale_fits_the_layout_inside_the_canvas():
    transform = geo.fit(TWO_ACROSS, canvas=(600, 400), margin=10)
    for rect in TWO_ACROSS:
        x, y, w, h = geo.to_canvas(rect, transform)
        assert 10 <= x and x + w <= 600 - 10 + 1
        assert 10 <= y and y + h <= 400 - 10 + 1


def test_the_scale_preserves_aspect_ratio():
    transform = geo.fit(TWO_ACROSS, canvas=(600, 400), margin=10)
    _, _, w, h = geo.to_canvas(TWO_ACROSS[0], transform)
    assert w / h == pytest.approx(2560 / 1440, rel=0.02)


def test_a_monitor_left_of_the_primary_lands_on_the_canvas():
    """The case naive scaling gets wrong."""
    transform = geo.fit(LEFT_OF_PRIMARY, canvas=(600, 400), margin=10)
    for rect in LEFT_OF_PRIMARY:
        x, y, w, h = geo.to_canvas(rect, transform)
        assert x >= 0, f"{rect} mapped off the left edge to x={x}"
        assert x + w <= 600, f"{rect} mapped off the right edge"


def test_desktop_to_canvas_and_back_is_a_round_trip():
    transform = geo.fit(LEFT_OF_PRIMARY, canvas=(600, 400), margin=10)
    for point in ((-3440, 0), (0, 0), (2559, 1439), (-1000, 700)):
        canvas_point = geo.to_canvas_point(point, transform)
        assert geo.to_desktop_point(canvas_point, transform) == \
            pytest.approx(point, abs=transform.scale and 1 / transform.scale + 1)


def test_a_single_monitor_still_scales():
    transform = geo.fit([(0, 0, 2560, 1440)], canvas=(600, 400), margin=10)
    x, y, w, h = geo.to_canvas((0, 0, 2560, 1440), transform)
    assert w > 0 and h > 0
    assert w <= 580 and h <= 380


def test_fitting_nothing_gives_no_transform():
    assert geo.fit([], canvas=(600, 400), margin=10) is None


def test_a_zero_sized_canvas_does_not_divide_by_zero():
    assert geo.fit(TWO_ACROSS, canvas=(0, 0), margin=10) is None


# ── snapping, so dragging produces tidy layouts ────────────────────────

def test_a_near_miss_snaps_to_the_neighbour_edge():
    moved = geo.snap((2570, 0, 2560, 1440), [(0, 0, 2560, 1440)], threshold=32)
    assert moved[0] == 2560, "did not snap to the right edge of its neighbour"


def test_a_clear_gap_is_left_alone():
    moved = geo.snap((3000, 0, 2560, 1440), [(0, 0, 2560, 1440)], threshold=32)
    assert moved[0] == 3000


def test_snapping_aligns_tops_as_well_as_edges():
    moved = geo.snap((2560, 12, 2560, 1440), [(0, 0, 2560, 1440)], threshold=32)
    assert moved[1] == 0, "did not align to the neighbour's top"
