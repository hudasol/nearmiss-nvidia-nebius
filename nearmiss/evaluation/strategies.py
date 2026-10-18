"""Stress-test design strategies that decide *which* conditions to simulate.

Both strategies get the same envelope, the same intervention, the same simulator, the
same metrics and the same run budget. They differ only in how they choose stress points.

``conventional_sweep`` — the credible conventional workflow: the nominal replay plus a
Latin hypercube sweep across the whole envelope.

``targeted_search`` — NearMiss' physics-targeted search:

1. *Kinematic corners.* Stopping distance ``v·t_r + v²/2a`` increases monotonically with
   speed and reaction time and decreases with braking deceleration, so the worst stopping
   corner is fixed analytically. Pedestrian speed and obstruction placement have no
   monotone effect, so both of their extremes are tested at that corner.
2. *Arrival alignment.* A forklift–pedestrian conflict only exists when the pedestrian
   enters the forklift's lane during the truck's stopping time. For each configuration
   the pedestrian start offset is solved from the route geometry so that the pedestrian
   reaches the lane between 1 s after and ``t_r + v/a`` before the truck's front reaches
   the crossing. A uniform sweep spends most runs on timings where the actors never meet.
3. *Adaptive falsification.* Remaining runs perturb the lowest-margin configurations
   found so far, searching locally for a separation below the threshold.

The strategies never see reference labels and never decide outcomes; the evaluator
classifies every run from its metrics.
"""

from __future__ import annotations

import random
from collections.abc import Generator
from dataclasses import dataclass

from nearmiss.domain import geometry as gm
from nearmiss.domain.models import Scenario, StressEnvelope, StressPoint
from nearmiss.domain.transforms import apply_stress, nominal_stress_point
from nearmiss.evaluation.sampling import latin_hypercube

STRATEGY_VERSION = "nearmiss-strategies/1.0.0"

#: Budget split for the targeted search (fractions of the non-nominal budget).
CORNER_FRACTION = 0.25
ALIGNED_FRACTION = 0.40
ADAPTIVE_TOP_K = 3
ADAPTIVE_SIGMA_FRACTION = 0.10
ADAPTIVE_LEAD_SIGMA_S = 0.25
#: The pedestrian may reach the lane up to this long *after* the truck front (flank case).
LEAD_AFTER_S = 1.0


@dataclass(frozen=True)
class Observation:
    point: StressPoint
    margin_m: float  # decision-metric value minus threshold; negative = unsafe


StrategyGen = Generator[list[StressPoint], list[Observation], None]


def _clamp(envelope: StressEnvelope, point: StressPoint) -> StressPoint:
    out: StressPoint = {}
    for a in envelope.axes:
        v = point.get(a.name, (a.low + a.high) / 2)
        out[a.name] = min(a.high, max(a.low, v))
    return out


def conventional_sweep(
    scenario: Scenario, envelope: StressEnvelope, budget: int, seed: int
) -> StrategyGen:
    names = [a.name for a in envelope.axes]
    batch = [_clamp(envelope, nominal_stress_point(scenario, names))]
    batch += latin_hypercube(envelope, budget - 1, seed)
    yield batch


@dataclass(frozen=True)
class ConflictTiming:
    """Arrival times at zero start offset, from route geometry and stressed speeds."""

    forklift_front_s: float
    pedestrian_lane_s: float
    stopping_time_s: float


def conflict_timing(scenario: Scenario, point: StressPoint) -> ConflictTiming | None:
    stressed = apply_stress(
        scenario, {k: v for k, v in point.items() if k != "pedestrian_start_offset"}
    )
    fk, ped = stressed.forklift, stressed.pedestrian
    hit = gm.Polyline(fk.route).first_intersection(gm.Polyline(ped.route))
    if hit is None:
        return None
    _, s_fk, s_ped = hit
    v = fk.cruise_speed.value
    t_front = max(0.0, s_fk - fk.length_m / 2.0 - ped.radius_m) / v
    lane_edge = fk.width_m / 2.0 + ped.radius_m
    t_lane = ped.start_time.value + max(0.0, s_ped - lane_edge) / ped.walking_speed.value
    t_stop = fk.reaction_time.value + v / fk.braking_decel.value
    return ConflictTiming(
        forklift_front_s=t_front, pedestrian_lane_s=t_lane, stopping_time_s=t_stop
    )


def _aligned_offset(
    scenario: Scenario, envelope: StressEnvelope, point: StressPoint, lead_fraction: float
) -> StressPoint:
    """Set the start offset so the pedestrian enters the lane at the given lead."""
    if envelope.axis("pedestrian_start_offset") is None:
        return point
    timing = conflict_timing(scenario, point)
    if timing is None:
        return point
    lead = -LEAD_AFTER_S + lead_fraction * (timing.stopping_time_s + LEAD_AFTER_S)
    offset = timing.forklift_front_s - lead - timing.pedestrian_lane_s
    out = dict(point)
    out["pedestrian_start_offset"] = offset
    return _clamp(envelope, out)


def _lead_fraction_of(scenario: Scenario, point: StressPoint) -> float:
    timing = conflict_timing(scenario, point)
    if timing is None:
        return 0.5
    t_lane = timing.pedestrian_lane_s + point.get("pedestrian_start_offset", 0.0)
    lead = timing.forklift_front_s - t_lane
    return (lead + LEAD_AFTER_S) / (timing.stopping_time_s + LEAD_AFTER_S)


def _corner_configs(envelope: StressEnvelope, base: StressPoint) -> list[StressPoint]:
    worst = dict(base)
    for name, end in (
        ("forklift_speed", "high"),
        ("reaction_time", "high"),
        ("braking_decel", "low"),
    ):
        axis = envelope.axis(name)
        if axis is not None:
            worst[name] = axis.high if end == "high" else axis.low
    ped_axis = envelope.axis("pedestrian_speed")
    ped_values = [ped_axis.low, ped_axis.high] if ped_axis else [None]
    sx_axis = envelope.axis("obstruction_shift_x")
    sx_values = [sx_axis.low, 0.0, sx_axis.high] if sx_axis else [None]
    configs: list[StressPoint] = []
    for sx in sx_values:
        for pv in ped_values:
            c = dict(worst)
            if pv is not None:
                c["pedestrian_speed"] = pv
            if sx is not None:
                c["obstruction_shift_x"] = sx
            configs.append(c)
    return configs


def targeted_search(
    scenario: Scenario, envelope: StressEnvelope, budget: int, seed: int
) -> StrategyGen:
    rng = random.Random(seed)
    names = [a.name for a in envelope.axes]
    nominal = _clamp(envelope, nominal_stress_point(scenario, names))
    remaining = budget - 1
    n_corner = round(remaining * CORNER_FRACTION)
    n_aligned = round(remaining * ALIGNED_FRACTION)

    # Phase 1: nominal replay plus kinematic corners at two alignment leads each.
    batch = [nominal]
    corners = _corner_configs(envelope, nominal)
    leads = (0.35, 0.75)
    corner_points: list[StressPoint] = []
    for c in corners:
        for lead in leads:
            jitter = rng.uniform(-0.1, 0.1)
            corner_points.append(_aligned_offset(scenario, envelope, c, lead + jitter))
    batch += corner_points[:n_corner]
    n_aligned += max(0, n_corner - len(corner_points))
    history = yield batch

    # Phase 2: alignment-targeted space-filling sample of the other axes.
    free = latin_hypercube(envelope, n_aligned, seed + 7919)
    batch = [_aligned_offset(scenario, envelope, p, rng.random()) for p in free]
    if batch:
        history = yield batch

    # Phase 3: adaptive falsification around the lowest-margin configurations.
    used = len(history)
    while used < budget:
        ranked = sorted(history, key=lambda o: o.margin_m)
        seeds: list[StressPoint] = []
        for obs in ranked:
            if obs.point not in seeds:
                seeds.append(obs.point)
            if len(seeds) == ADAPTIVE_TOP_K:
                break
        batch = []
        for parent in seeds:
            if used + len(batch) >= budget:
                break
            child = dict(parent)
            for a in envelope.axes:
                if a.name == "pedestrian_start_offset":
                    continue
                child[a.name] = rng.gauss(
                    parent[a.name], ADAPTIVE_SIGMA_FRACTION * (a.high - a.low)
                )
            child = _clamp(envelope, child)
            lead = _lead_fraction_of(scenario, parent)
            lead_sigma = ADAPTIVE_LEAD_SIGMA_S / max(
                1e-6,
                (conflict_timing(scenario, child) or ConflictTiming(0, 0, 1)).stopping_time_s
                + LEAD_AFTER_S,
            )
            batch.append(_aligned_offset(scenario, envelope, child, rng.gauss(lead, lead_sigma)))
        if not batch:
            break
        history = yield batch
        used = len(history)


STRATEGIES = {
    "conventional_sweep": conventional_sweep,
    "targeted_search": targeted_search,
}
