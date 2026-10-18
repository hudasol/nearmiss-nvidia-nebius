import math

import pytest

from nearmiss.domain import geometry as gm


def test_oriented_rectangle_is_counter_clockwise_with_expected_corners() -> None:
    poly = gm.oriented_rectangle((0.0, 0.0), 4.0, 2.0, 0.0)
    assert gm.polygon_area(poly) == pytest.approx(8.0)
    assert sorted(poly) == sorted([(2.0, 1.0), (-2.0, 1.0), (-2.0, -1.0), (2.0, -1.0)])


def test_rotated_rectangle_keeps_area_and_rotates_corners() -> None:
    poly = gm.oriented_rectangle((1.0, 1.0), 4.0, 2.0, math.pi / 2)
    assert gm.polygon_area(poly) == pytest.approx(8.0)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    assert min(xs) == pytest.approx(0.0) and max(xs) == pytest.approx(2.0)
    assert min(ys) == pytest.approx(-1.0) and max(ys) == pytest.approx(3.0)


@pytest.mark.parametrize(
    ("disc", "expected"),
    [
        ((5.0, 0.0), 5.0 - 2.0 - 0.5),  # ahead of the front face
        ((0.0, 3.0), 3.0 - 1.0 - 0.5),  # beside the flank
        ((5.0, 4.0), math.hypot(3.0, 3.0) - 0.5),  # off a corner
        ((2.5, 0.0), 0.0),  # touching the front face exactly
        ((0.0, 0.0), -1.0 - 0.5),  # centre inside: nearest edge 1 m away
    ],
)
def test_rectangle_disc_separation_cases(disc: gm.Point, expected: float) -> None:
    got = gm.rectangle_disc_separation((0.0, 0.0), 4.0, 2.0, 0.0, disc, 0.5)
    assert got == pytest.approx(expected)


def test_rectangle_disc_separation_respects_heading() -> None:
    # Facing north, the front face is at y = +2.
    got = gm.rectangle_disc_separation((0.0, 0.0), 4.0, 2.0, math.pi / 2, (0.0, 3.0), 0.5)
    assert got == pytest.approx(0.5)


def test_point_in_polygon_including_edges() -> None:
    sq = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    assert gm.point_in_polygon((1.0, 1.0), sq)
    assert gm.point_in_polygon((2.0, 1.0), sq)
    assert not gm.point_in_polygon((2.1, 1.0), sq)


def test_sight_line_blocking() -> None:
    wall = gm.oriented_rectangle((5.0, 0.0), 1.0, 4.0, 0.0)
    assert gm.segment_blocked_by_polygon((0.0, 0.0), (10.0, 0.0), wall)
    assert not gm.segment_blocked_by_polygon((0.0, 3.0), (10.0, 3.0), wall)
    # Grazing the corner counts as blocked (conservative for visibility).
    assert gm.segment_blocked_by_polygon((0.0, 2.0), (10.0, 2.0), wall)
    # A segment entirely inside the polygon is blocked.
    assert gm.segment_blocked_by_polygon((4.9, -0.5), (5.1, 0.5), wall)


def test_polygons_intersect_separating_axis() -> None:
    a = gm.oriented_rectangle((0.0, 0.0), 2.0, 2.0, 0.0)
    b = gm.oriented_rectangle((1.9, 0.0), 2.0, 2.0, math.pi / 4)
    c = gm.oriented_rectangle((4.0, 0.0), 2.0, 2.0, math.pi / 4)
    assert gm.polygons_intersect(a, b)
    assert not gm.polygons_intersect(a, c)


def test_polyline_pose_and_clamping() -> None:
    line = gm.Polyline([(0.0, 0.0), (3.0, 0.0), (3.0, 4.0)])
    assert line.length == pytest.approx(7.0)
    pos, heading = line.pose_at(5.0)
    assert pos == pytest.approx((3.0, 2.0))
    assert heading == pytest.approx(math.pi / 2)
    assert line.pose_at(-1.0)[0] == pytest.approx((0.0, 0.0))
    assert line.pose_at(99.0)[0] == pytest.approx((3.0, 4.0))


def test_polyline_rejects_degenerate_routes() -> None:
    with pytest.raises(ValueError):
        gm.Polyline([(0.0, 0.0)])
    with pytest.raises(ValueError):
        gm.Polyline([(0.0, 0.0), (0.0, 0.0), (1.0, 0.0)])


def test_route_intersection_arc_lengths() -> None:
    a = gm.Polyline([(0.0, 5.0), (10.0, 5.0)])
    b = gm.Polyline([(4.0, 0.0), (4.0, 10.0)])
    hit = a.first_intersection(b)
    assert hit is not None
    point, s_a, s_b = hit
    assert point == pytest.approx((4.0, 5.0))
    assert s_a == pytest.approx(4.0)
    assert s_b == pytest.approx(5.0)
    assert a.first_intersection(gm.Polyline([(20.0, 0.0), (20.0, 10.0)])) is None
