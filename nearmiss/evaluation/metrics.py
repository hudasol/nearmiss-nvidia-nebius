"""Safety metrics computed independently from a recorded trajectory.

The evaluator only reads poses and footprint dimensions. It never uses the engine's
internal flags (visibility, hazard, driver state) to decide outcomes, so a defect in the
engine's decision logic cannot mark its own run as safe.

Metric definitions (units in brackets):

* ``min_separation_m`` [m] — minimum over time of the clearance between the forklift's
  rectangular footprint and the pedestrian's disc. Zero or negative means contact.
* ``min_moving_separation_m`` [m] — the same clearance, minimised only over instants when
  the forklift is moving (speed >= ``MOVING_SPEED_MPS``) and the pedestrian is not behind
  the forklift's rear face (it is ahead of or alongside the truck). A pedestrian standing
  next to a truck that has already stopped, or behind a truck driving away, is not exposed
  to the forward-travel hazard this scenario studies. The safety decision uses this
  metric. ``None`` if no instant qualifies.
* ``collision`` — true when the footprints touch or overlap at any instant
  (``min_separation_m <= 0``).
* ``min_ttc_s`` [s] — minimum time-to-collision. At each 0.1 s sample, both actors are
  extrapolated at their current velocities (forklift along its heading, pedestrian along
  its finite-difference velocity) for up to ``TTC_HORIZON_S``; TTC is the first
  extrapolated time at which the footprints touch. ``None`` when no projection touches.
  Meaningful only while both actors move; it ignores braking and route curvature.
* ``pet_s`` [s] — post-encroachment time at the conflict zone: the gap between one actor
  leaving the zone and the other entering it. ``0`` when they occupy it at the same time;
  ``None`` when one of them never enters (for example, the forklift stops short).
* ``forklift_entered_conflict_zone`` — whether the forklift footprint ever overlapped the
  conflict zone.
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from nearmiss.domain import geometry as gm
from nearmiss.domain.models import Scenario
from nearmiss.simulation.engine import Trajectory

METRICS_VERSION = "nearmiss-metrics/1.0.0"
TTC_HORIZON_S = 4.0
TTC_SAMPLE_S = 0.1
MOVING_SPEED_MPS = 0.1


class ConflictZone(BaseModel):
    center: tuple[float, float]
    heading: float
    length: float
    width: float

    def polygon(self) -> gm.Polygon:
        return gm.oriented_rectangle(self.center, self.length, self.width, self.heading)


class RunMetrics(BaseModel):
    metrics_version: str = METRICS_VERSION
    min_separation_m: float
    t_min_separation_s: float
    min_moving_separation_m: float | None
    t_min_moving_separation_s: float | None
    collision: bool
    first_contact_s: float | None
    min_ttc_s: float | None
    pet_s: float | None
    forklift_entered_conflict_zone: bool
    pedestrian_entered_conflict_zone: bool
    forklift_peak_speed_mps: float
    forklift_speed_at_min_sep_mps: float


def conflict_zone(scenario: Scenario) -> ConflictZone | None:
    """Rectangle around the point where the pedestrian route crosses the forklift route.

    Along the forklift route it spans the pedestrian's diameter plus 0.5 m; across it, the
    forklift's width plus the pedestrian's diameter plus 0.5 m. Any contact between the
    actors near the crossing implies both overlap this zone.
    """
    fk_route = gm.Polyline(scenario.forklift.route)
    ped_route = gm.Polyline(scenario.pedestrian.route)
    hit = fk_route.first_intersection(ped_route)
    if hit is None:
        return None
    point, s_fk, _ = hit
    _, heading = fk_route.pose_at(s_fk)
    r = scenario.pedestrian.radius_m
    return ConflictZone(
        center=point,
        heading=heading,
        length=2 * r + 0.5,
        width=scenario.forklift.width_m + 2 * r + 0.5,
    )


def _separation(scenario: Scenario, fx: float, fy: float, fh: float, px: float, py: float) -> float:
    fk = scenario.forklift
    return gm.rectangle_disc_separation(
        (fx, fy), fk.length_m, fk.width_m, fh, (px, py), scenario.pedestrian.radius_m
    )


def _occupancy_interval(flags: list[bool], t: list[float]) -> tuple[float, float] | None:
    idx = [i for i, f in enumerate(flags) if f]
    if not idx:
        return None
    return t[idx[0]], t[idx[-1]]


def compute_metrics(scenario: Scenario, traj: Trajectory) -> RunMetrics:
    fk = scenario.forklift
    radius = scenario.pedestrian.radius_m
    n = len(traj.t)

    seps = [
        _separation(
            scenario,
            traj.forklift_x[i],
            traj.forklift_y[i],
            traj.forklift_heading[i],
            traj.pedestrian_x[i],
            traj.pedestrian_y[i],
        )
        for i in range(n)
    ]
    i_min = min(range(n), key=lambda i: seps[i])
    moving = [
        i
        for i in range(n)
        if traj.forklift_speed[i] >= MOVING_SPEED_MPS
        and gm.to_local(
            (traj.pedestrian_x[i], traj.pedestrian_y[i]),
            (traj.forklift_x[i], traj.forklift_y[i]),
            traj.forklift_heading[i],
        )[0]
        >= -fk.length_m / 2.0
    ]
    i_mov = min(moving, key=lambda i: seps[i]) if moving else None
    first_contact = next((traj.t[i] for i in range(n) if seps[i] <= 0.0), None)

    # Time-to-collision at 0.1 s samples with constant-velocity extrapolation.
    stride = max(1, round(TTC_SAMPLE_S / traj.dt))
    min_ttc: float | None = None
    n_proj = round(TTC_HORIZON_S / TTC_SAMPLE_S)
    for i in range(stride, n, stride):
        if seps[i] <= 0.0:
            min_ttc = 0.0
            break
        v = traj.forklift_speed[i]
        h = traj.forklift_heading[i]
        fvx, fvy = v * math.cos(h), v * math.sin(h)
        pvx = (traj.pedestrian_x[i] - traj.pedestrian_x[i - stride]) / (stride * traj.dt)
        pvy = (traj.pedestrian_y[i] - traj.pedestrian_y[i - stride]) / (stride * traj.dt)
        if v < 1e-6 and abs(pvx) < 1e-6 and abs(pvy) < 1e-6:
            continue
        for k in range(1, n_proj + 1):
            tau = k * TTC_SAMPLE_S
            if min_ttc is not None and tau >= min_ttc:
                break
            s = _separation(
                scenario,
                traj.forklift_x[i] + fvx * tau,
                traj.forklift_y[i] + fvy * tau,
                h,
                traj.pedestrian_x[i] + pvx * tau,
                traj.pedestrian_y[i] + pvy * tau,
            )
            if s <= 0.0:
                min_ttc = tau
                break

    # Post-encroachment time at the conflict zone.
    zone = conflict_zone(scenario)
    pet: float | None = None
    fk_in_zone = False
    ped_in_zone = False
    if zone is not None:
        zpoly = zone.polygon()
        reach = math.hypot(fk.length_m, fk.width_m) / 2 + math.hypot(zone.length, zone.width) / 2
        fk_flags = [
            math.hypot(traj.forklift_x[i] - zone.center[0], traj.forklift_y[i] - zone.center[1])
            <= reach
            and gm.polygons_intersect(
                gm.oriented_rectangle(
                    (traj.forklift_x[i], traj.forklift_y[i]),
                    fk.length_m,
                    fk.width_m,
                    traj.forklift_heading[i],
                ),
                zpoly,
            )
            for i in range(n)
        ]
        ped_flags = [
            gm.rectangle_disc_separation(
                zone.center,
                zone.length,
                zone.width,
                zone.heading,
                (traj.pedestrian_x[i], traj.pedestrian_y[i]),
                radius,
            )
            <= 0.0
            for i in range(n)
        ]
        fk_iv = _occupancy_interval(fk_flags, traj.t)
        ped_iv = _occupancy_interval(ped_flags, traj.t)
        fk_in_zone = fk_iv is not None
        ped_in_zone = ped_iv is not None
        if fk_iv is not None and ped_iv is not None:
            if any(a and b for a, b in zip(fk_flags, ped_flags, strict=True)):
                pet = 0.0
            elif fk_iv[1] < ped_iv[0]:
                pet = ped_iv[0] - fk_iv[1]
            else:
                pet = fk_iv[0] - ped_iv[1]

    return RunMetrics(
        min_separation_m=round(seps[i_min], 6),
        t_min_separation_s=traj.t[i_min],
        min_moving_separation_m=None if i_mov is None else round(seps[i_mov], 6),
        t_min_moving_separation_s=None if i_mov is None else traj.t[i_mov],
        collision=seps[i_min] <= 0.0,
        first_contact_s=first_contact,
        min_ttc_s=None if min_ttc is None else round(min_ttc, 6),
        pet_s=None if pet is None else round(pet, 6),
        forklift_entered_conflict_zone=fk_in_zone,
        pedestrian_entered_conflict_zone=ped_in_zone,
        forklift_peak_speed_mps=round(max(traj.forklift_speed), 6),
        forklift_speed_at_min_sep_mps=round(traj.forklift_speed[i_min], 6),
    )


class MetricDefinition(BaseModel):
    name: str
    unit: str
    description: str
    supports_clearance_claim: bool


#: Registry used by the compiler to check that requested metrics exist and can support
#: the experiment's stated claim.
METRIC_DEFINITIONS: dict[str, MetricDefinition] = {
    d.name: d
    for d in (
        MetricDefinition(
            name="min_moving_separation_m",
            unit="m",
            description="Minimum clearance between the moving forklift and an exposed pedestrian",
            supports_clearance_claim=True,
        ),
        MetricDefinition(
            name="collision",
            unit="boolean",
            description="Footprints touch or overlap at any instant",
            supports_clearance_claim=True,
        ),
        MetricDefinition(
            name="min_separation_m",
            unit="m",
            description="Minimum clearance at any instant, including stationary truck",
            supports_clearance_claim=False,
        ),
        MetricDefinition(
            name="min_ttc_s",
            unit="s",
            description="Constant-velocity time-to-collision (ignores braking)",
            supports_clearance_claim=False,
        ),
        MetricDefinition(
            name="pet_s",
            unit="s",
            description="Post-encroachment time at the route-crossing conflict zone",
            supports_clearance_claim=False,
        ),
    )
}
