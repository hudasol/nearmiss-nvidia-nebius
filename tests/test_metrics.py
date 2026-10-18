"""Metric tests on hand-built trajectories with known answers."""

from __future__ import annotations

import pytest

from nearmiss.domain.models import Scenario
from nearmiss.evaluation.metrics import compute_metrics, conflict_zone
from nearmiss.simulation.engine import Trajectory

DT = 0.1


def _traj(
    scenario: Scenario,
    fk: list[tuple[float, float, float]],
    ped: list[tuple[float, float]],
) -> Trajectory:
    """fk: (x, y, speed) per frame heading east; ped: (x, y) per frame."""
    n = len(fk)
    assert len(ped) == n
    return Trajectory(
        engine_version="test",
        scenario_id=scenario.id,
        config_hash="test",
        dt=DT,
        t=[round(i * DT, 9) for i in range(n)],
        forklift_x=[f[0] for f in fk],
        forklift_y=[f[1] for f in fk],
        forklift_heading=[0.0] * n,
        forklift_speed=[f[2] for f in fk],
        driver_state=["cruise"] * n,
        pedestrian_x=[p[0] for p in ped],
        pedestrian_y=[p[1] for p in ped],
        pedestrian_visible=[True] * n,
        hazard=[False] * n,
        events=[],
    )


def test_min_separation_of_stationary_pair(reference: Scenario) -> None:
    # Forklift 3.3 x 1.15 centred at (10, 9); pedestrian disc r=0.3 at (13, 9).
    traj = _traj(reference, [(10.0, 9.0, 0.0)] * 5, [(13.0, 9.0)] * 5)
    m = compute_metrics(reference, traj)
    assert m.min_separation_m == pytest.approx(3.0 - 1.65 - 0.3)
    assert not m.collision
    # Stationary forklift: no instant qualifies for the moving-separation metric.
    assert m.min_moving_separation_m is None
    assert m.min_ttc_s is None


def test_collision_detected_and_first_contact_time(reference: Scenario) -> None:
    fk = [(10.0 + 2.0 * i * DT, 9.0, 2.0) for i in range(20)]
    ped = [(13.0, 9.0)] * 20
    m = compute_metrics(reference, _traj(reference, fk, ped))
    assert m.collision
    # Front face reaches the disc when 10 + 0.2 i + 1.65 >= 12.7, i.e. i = 6 (t = 0.6 s).
    assert m.first_contact_s == pytest.approx(0.6)
    assert m.min_moving_separation_m is not None and m.min_moving_separation_m < 0


def test_ttc_constant_velocity_closing(reference: Scenario) -> None:
    # Gap 2.0 m at t = 0 closing at 2 m/s. TTC is evaluated at t = 0.1 s (gap 1.8 m,
    # TTC 0.9 s) and t = 0.2 s (gap 1.6 m, TTC 0.8 s), on a 0.1 s projection grid.
    fk = [(10.0 + 2.0 * i * DT, 9.0, 2.0) for i in range(3)]
    ped = [(13.95, 9.0)] * 3
    m = compute_metrics(reference, _traj(reference, fk, ped))
    assert m.min_ttc_s == pytest.approx(0.8, abs=0.1 + 1e-9)


def test_moving_separation_ignores_pedestrian_behind_the_truck(reference: Scenario) -> None:
    # The truck drives away east from a pedestrian standing 0.2 m behind its rear face.
    fk = [(10.0 + 1.0 * i * DT, 9.0, 1.0) for i in range(10)]
    ped = [(10.0 - 1.65 - 0.3 - 0.2, 9.0)] * 10
    m = compute_metrics(reference, _traj(reference, fk, ped))
    assert m.min_separation_m == pytest.approx(0.2)
    assert m.min_moving_separation_m is None


def test_conflict_zone_at_route_crossing(reference: Scenario) -> None:
    zone = conflict_zone(reference)
    assert zone is not None
    assert zone.center == pytest.approx((23.0, 9.0))
    assert zone.length == pytest.approx(2 * 0.3 + 0.5)
    assert zone.width == pytest.approx(1.15 + 2 * 0.3 + 0.5)


def test_post_encroachment_time(reference: Scenario) -> None:
    # Forklift sweeps through the zone (x 22.45..23.55 plus its 1.65 m half-length)
    # early; the pedestrian enters the zone later.
    n = 120
    fk = [(15.0 + 3.0 * i * DT, 9.0, 3.0) for i in range(n)]
    ped = [(23.0, min(9.0, 4.0 + 1.0 * i * DT)) for i in range(n)]
    m = compute_metrics(reference, _traj(reference, fk, ped))
    assert m.forklift_entered_conflict_zone and m.pedestrian_entered_conflict_zone
    # Last frame with the forklift touching the zone: rear face 15 + 0.3 i - 1.65 <= 23.55
    # -> i = 34 (t = 3.4 s). First frame with the pedestrian's disc touching the zone edge
    # y = 9 - 1.125: 4 + 0.1 i + 0.3 >= 7.875 -> i = 36 (t = 3.6 s). PET = 0.2 s.
    assert m.pet_s == pytest.approx(0.2, abs=1e-6)


def test_pet_none_when_forklift_stops_short(reference: Scenario) -> None:
    fk = [(15.0, 9.0, 0.0)] * 50
    ped = [(23.0, 4.0 + 0.2 * i) for i in range(50)]
    m = compute_metrics(reference, _traj(reference, fk, ped))
    assert not m.forklift_entered_conflict_zone
    assert m.pet_s is None
