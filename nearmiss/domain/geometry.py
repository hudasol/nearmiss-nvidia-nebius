"""Planar geometry primitives used by the simulator, the compiler and the evaluator.

Conventions (shared by every layer):
- Units are metres and radians.
- World frame: x points east, y points north, origin at the south-west corner of the
  scenario bounds. Headings are measured counter-clockwise from +x.
- Polygons are lists of vertices in counter-clockwise order, without a repeated
  closing vertex.

All functions are pure and deterministic so that replays are bit-identical.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]
Polygon = list[Point]

EPS = 1e-12


def sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def norm(a: Point) -> float:
    return math.hypot(a[0], a[1])


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def rotate(p: Point, angle: float) -> Point:
    c, s = math.cos(angle), math.sin(angle)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def to_local(p: Point, origin: Point, heading: float) -> Point:
    """Express world point ``p`` in a frame at ``origin`` rotated by ``heading``."""
    return rotate(sub(p, origin), -heading)


def to_world(p: Point, origin: Point, heading: float) -> Point:
    r = rotate(p, heading)
    return (r[0] + origin[0], r[1] + origin[1])


def oriented_rectangle(center: Point, length: float, width: float, heading: float) -> Polygon:
    """Corners of a rectangle of ``length`` along ``heading`` and ``width`` across it."""
    hl, hw = length / 2.0, width / 2.0
    # Front-left, rear-left, rear-right, front-right: counter-clockwise.
    local = [(hl, hw), (-hl, hw), (-hl, -hw), (hl, -hw)]
    return [to_world(p, center, heading) for p in local]


def polygon_area(poly: Sequence[Point]) -> float:
    """Signed area (positive for counter-clockwise polygons)."""
    a = 0.0
    n = len(poly)
    for i in range(n):
        a += cross(poly[i], poly[(i + 1) % n])
    return a / 2.0


def _ray_cast_inside(p: Point, poly: Sequence[Point]) -> bool:
    inside = False
    n = len(poly)
    x, y = p
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside


def point_in_polygon(p: Point, poly: Sequence[Point]) -> bool:
    """Even-odd rule; points exactly on an edge count as inside."""
    if _ray_cast_inside(p, poly):
        return True
    return point_polygon_boundary_distance(p, poly) <= 1e-9


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ab = sub(b, a)
    denom = dot(ab, ab)
    if denom < EPS:
        return distance(p, a)
    t = max(0.0, min(1.0, dot(sub(p, a), ab) / denom))
    return distance(p, (a[0] + t * ab[0], a[1] + t * ab[1]))


def point_polygon_boundary_distance(p: Point, poly: Sequence[Point]) -> float:
    n = len(poly)
    return min(point_segment_distance(p, poly[i], poly[(i + 1) % n]) for i in range(n))


def point_polygon_distance(p: Point, poly: Sequence[Point]) -> float:
    """Euclidean distance from ``p`` to a filled polygon (0 when inside)."""
    if point_in_polygon(p, poly):
        return 0.0
    return point_polygon_boundary_distance(p, poly)


def rectangle_disc_separation(
    center: Point, length: float, width: float, heading: float, disc_center: Point, radius: float
) -> float:
    """Signed clearance between an oriented rectangle and a disc.

    Positive values are the gap in metres; zero or negative values mean the footprints
    touch or overlap (the negative value is the penetration depth along the closest axis
    for discs whose centre lies outside the rectangle, and minus the distance to the
    nearest edge plus the radius when the centre is inside).
    """
    lx, ly = to_local(disc_center, center, heading)
    hl, hw = length / 2.0, width / 2.0
    dx = abs(lx) - hl
    dy = abs(ly) - hw
    if dx <= 0.0 and dy <= 0.0:
        # Disc centre inside the rectangle.
        return max(dx, dy) - radius
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    return outside - radius


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    """True when closed segments p1p2 and q1q2 share at least one point."""
    d1 = cross(sub(q2, q1), sub(p1, q1))
    d2 = cross(sub(q2, q1), sub(p2, q1))
    d3 = cross(sub(p2, p1), sub(q1, p1))
    d4 = cross(sub(p2, p1), sub(q2, p1))
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True

    def on_segment(a: Point, b: Point, c: Point, d: float) -> bool:
        return abs(d) <= 1e-12 and (
            min(a[0], b[0]) - 1e-12 <= c[0] <= max(a[0], b[0]) + 1e-12
            and min(a[1], b[1]) - 1e-12 <= c[1] <= max(a[1], b[1]) + 1e-12
        )

    return (
        on_segment(q1, q2, p1, d1)
        or on_segment(q1, q2, p2, d2)
        or on_segment(p1, p2, q1, d3)
        or on_segment(p1, p2, q2, d4)
    )


def segment_blocked_by_polygon(a: Point, b: Point, poly: Sequence[Point]) -> bool:
    """True when the open sight line a→b passes through the polygon's interior or edges."""
    n = len(poly)
    for i in range(n):
        if segments_intersect(a, b, poly[i], poly[(i + 1) % n]):
            return True
    # No edge crossing: the segment is either fully inside or fully outside.
    return _ray_cast_inside(a, poly)


def polygons_intersect(a: Sequence[Point], b: Sequence[Point]) -> bool:
    """Separating-axis test for convex polygons (touching counts as intersecting)."""
    for poly in (a, b):
        n = len(poly)
        for i in range(n):
            e = sub(poly[(i + 1) % n], poly[i])
            axis = (-e[1], e[0])
            amin = min(dot(axis, p) for p in a)
            amax = max(dot(axis, p) for p in a)
            bmin = min(dot(axis, p) for p in b)
            bmax = max(dot(axis, p) for p in b)
            if amax < bmin - 1e-12 or bmax < amin - 1e-12:
                return False
    return True


def segment_polygon_distance(a: Point, b: Point, poly: Sequence[Point]) -> float:
    """Minimum distance between a segment and a filled convex or simple polygon."""
    if segment_blocked_by_polygon(a, b, poly) or point_in_polygon(a, poly):
        return 0.0
    n = len(poly)
    best = min(point_segment_distance(a, poly[i], poly[(i + 1) % n]) for i in range(n))
    best = min(best, min(point_segment_distance(b, poly[i], poly[(i + 1) % n]) for i in range(n)))
    best = min(best, min(point_segment_distance(v, a, b) for v in poly))
    return best


def polyline_length(points: Sequence[Point]) -> float:
    return sum(distance(points[i], points[i + 1]) for i in range(len(points) - 1))


def segment_intersection_point(p1: Point, p2: Point, q1: Point, q2: Point) -> Point | None:
    """Intersection point of two segments, or None when they do not cross."""
    r = sub(p2, p1)
    s = sub(q2, q1)
    denom = cross(r, s)
    if abs(denom) < EPS:
        return None
    qp = sub(q1, p1)
    t = cross(qp, s) / denom
    u = cross(qp, r) / denom
    if -1e-12 <= t <= 1 + 1e-12 and -1e-12 <= u <= 1 + 1e-12:
        return (p1[0] + t * r[0], p1[1] + t * r[1])
    return None


class Polyline:
    """Arc-length parameterised route used for deterministic path following."""

    def __init__(self, points: Sequence[Point]) -> None:
        if len(points) < 2:
            raise ValueError("a route needs at least two points")
        self.points: list[Point] = [(float(x), float(y)) for x, y in points]
        self.cumulative: list[float] = [0.0]
        for i in range(len(self.points) - 1):
            seg = distance(self.points[i], self.points[i + 1])
            if seg < 1e-9:
                raise ValueError("route contains a zero-length segment")
            self.cumulative.append(self.cumulative[-1] + seg)
        self.length = self.cumulative[-1]

    def _segment_index(self, s: float) -> int:
        # Routes have a handful of segments, so a linear scan is fast and exact.
        for i in range(len(self.points) - 1):
            if s <= self.cumulative[i + 1]:
                return i
        return len(self.points) - 2

    def pose_at(self, s: float) -> tuple[Point, float]:
        """Position and heading at arc length ``s`` (clamped to the route)."""
        s = max(0.0, min(self.length, s))
        i = self._segment_index(s)
        a, b = self.points[i], self.points[i + 1]
        seg_len = self.cumulative[i + 1] - self.cumulative[i]
        t = (s - self.cumulative[i]) / seg_len
        pos = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        heading = math.atan2(b[1] - a[1], b[0] - a[0])
        return pos, heading

    def first_intersection(self, other: Polyline) -> tuple[Point, float, float] | None:
        """First crossing with ``other``: (point, arc length on self, arc length on other)."""
        for i in range(len(self.points) - 1):
            for j in range(len(other.points) - 1):
                p = segment_intersection_point(
                    self.points[i], self.points[i + 1], other.points[j], other.points[j + 1]
                )
                if p is not None:
                    s_self = self.cumulative[i] + distance(self.points[i], p)
                    s_other = other.cumulative[j] + distance(other.points[j], p)
                    return p, s_self, s_other
        return None
