from __future__ import annotations

import math

import pytest

from nearmiss.domain import geometry as gm
from nearmiss.domain.models import RemoveObstruction, Scenario
from nearmiss.domain.transforms import apply_intervention, apply_stress
from nearmiss.evaluation.metrics import compute_metrics
from nearmiss.simulation.engine import Trajectory, simulate, trajectory_hash


def _event(traj: Trajectory, kind: str) -> float | None:
    return next((e.t for e in traj.events if e.kind == kind), None)


def _index(traj: Trajectory, t: float) -> int:
    return min(range(len(traj.t)), key=lambda i: abs(traj.t[i] - t))


def test_replay_is_deterministic(reference: Scenario) -> None:
    a = simulate(reference)
    b = simulate(reference)
    assert a.trajectory_hash == b.trajectory_hash
    assert a.forklift_x == b.forklift_x
    assert trajectory_hash(a) == a.trajectory_hash


def test_config_change_changes_hashes(reference: Scenario) -> None:
    a = simulate(reference)
    b = simulate(apply_stress(reference, {"forklift_speed": 2.7}))
    assert a.config_hash != b.config_hash
    assert a.trajectory_hash != b.trajectory_hash


def test_reaction_delay_and_braking_kinematics(reference: Scenario) -> None:
    traj = simulate(reference)
    t_hazard = _event(traj, "hazard_detected")
    t_brake = _event(traj, "braking_started")
    t_stop = _event(traj, "forklift_stopped")
    assert t_hazard is not None and t_brake is not None and t_stop is not None
    reaction = reference.forklift.reaction_time.value
    decel = reference.forklift.braking_decel.value
    dt = traj.dt
    assert t_brake - t_hazard == pytest.approx(reaction, abs=dt + 1e-9)

    i_b = _index(traj, t_brake)
    i_s = _index(traj, t_stop)
    v_b = traj.forklift_speed[i_b]
    # Constant deceleration: distance v^2/2a and duration v/a (to within one step).
    travelled = traj.forklift_x[i_s] - traj.forklift_x[i_b]
    assert travelled == pytest.approx(v_b * v_b / (2 * decel), abs=1e-6)
    assert t_stop - t_brake == pytest.approx(v_b / decel, abs=dt + 1e-9)


def test_speed_is_bounded_and_non_negative(reference: Scenario) -> None:
    traj = simulate(apply_stress(reference, {"forklift_speed": 3.2}))
    assert max(traj.forklift_speed) <= 3.2 + 1e-12
    assert min(traj.forklift_speed) >= 0.0


def test_obstruction_hides_pedestrian_until_it_emerges(reference: Scenario) -> None:
    traj = simulate(reference)
    # Frames where the pedestrian is walking behind the stack (y between 3 and 5.5 m)
    # while the forklift is still well west of the stack.
    hidden = [
        i
        for i in range(len(traj.t))
        if 3.0 < traj.pedestrian_y[i] < 5.5 and traj.forklift_x[i] < 17.0
    ]
    assert hidden
    assert not any(traj.pedestrian_visible[i] for i in hidden)

    clear, _ = apply_intervention(
        reference, RemoveObstruction(id="rm", label="rm", obstruction_id="stack-a")
    )
    traj_clear = simulate(clear)
    assert all(traj_clear.pedestrian_visible[i] for i in hidden)


def test_removing_the_obstruction_gives_earlier_braking_and_more_clearance(
    reference: Scenario,
) -> None:
    base = simulate(reference)
    clear, _ = apply_intervention(
        reference, RemoveObstruction(id="rm", label="rm", obstruction_id="stack-a")
    )
    improved = simulate(clear)
    t_base = _event(base, "hazard_detected")
    t_clear = _event(improved, "hazard_detected")
    assert t_base is not None and t_clear is not None
    assert t_clear < t_base
    m_base = compute_metrics(reference, base)
    m_clear = compute_metrics(clear, improved)
    assert m_base.min_moving_separation_m is not None
    assert m_clear.min_moving_separation_m is not None
    assert m_clear.min_moving_separation_m > m_base.min_moving_separation_m + 1.0


def test_timestep_convergence(reference: Scenario) -> None:
    seps = []
    for dt in (0.005, 0.01, 0.02):
        sc = reference.model_copy(
            update={"simulation": reference.simulation.model_copy(update={"dt_s": dt})}
        )
        m = compute_metrics(sc, simulate(sc))
        assert m.min_moving_separation_m is not None
        seps.append(m.min_moving_separation_m)
    # Halving the step changes the decision metric by far less than its 0.5 m threshold.
    assert abs(seps[0] - seps[1]) < 0.05
    assert abs(seps[1] - seps[2]) < 0.1


def test_pedestrian_never_walks_into_a_stationary_forklift(reference: Scenario) -> None:
    for offset in (-0.6, 0.0, 0.6, 1.2):
        sc = apply_stress(reference, {"pedestrian_start_offset": offset})
        traj = simulate(sc)
        fk = sc.forklift
        if compute_metrics(sc, traj).collision:
            continue
        for i in range(len(traj.t)):
            if traj.forklift_speed[i] == 0.0:
                sep = gm.rectangle_disc_separation(
                    (traj.forklift_x[i], traj.forklift_y[i]),
                    fk.length_m,
                    fk.width_m,
                    traj.forklift_heading[i],
                    (traj.pedestrian_x[i], traj.pedestrian_y[i]),
                    sc.pedestrian.radius_m,
                )
                assert sep > 0.0


def test_pedestrian_reaches_destination_when_forklift_stops(reference: Scenario) -> None:
    sc = apply_stress(reference, {"pedestrian_start_offset": 0.6})
    traj = simulate(sc)
    end = sc.pedestrian.route[-1]
    assert math.hypot(traj.pedestrian_x[-1] - end[0], traj.pedestrian_y[-1] - end[1]) < 1e-6


def test_pedestrian_not_observable_before_start(reference: Scenario) -> None:
    traj = simulate(reference)
    start = reference.pedestrian.start_time.value
    assert not any(v for t, v in zip(traj.t, traj.pedestrian_visible, strict=True) if t < start)


def test_downsample_keeps_last_frame(reference: Scenario) -> None:
    traj = simulate(reference)
    small = traj.downsample(5)
    assert small.t[0] == traj.t[0]
    assert small.t[-1] == traj.t[-1]
    assert len(small.t) == len(small.forklift_x) == len(small.hazard)


def test_pedestrian_at_front_corner_walks_around_stopped_truck(reference: Scenario) -> None:
    """Regression: a pedestrian stopped within the stand-off of the truck's front corner
    used to freeze for the rest of the run because every step was refused."""
    from nearmiss.domain.models import RelocateObstruction

    moved, _ = apply_intervention(
        reference,
        RelocateObstruction(id="r3", label="r3", obstruction_id="stack-a", offset_m=(-3.0, 0.0)),
    )
    sc = apply_stress(
        moved,
        {
            "forklift_speed": 2.505,
            "pedestrian_speed": 1.31,
            "pedestrian_start_offset": 0.935,
            "reaction_time": 1.385,
            "braking_decel": 2.034,
            "obstruction_shift_x": 0.904,
            "obstruction_shift_y": 0.414,
        },
    )
    traj = simulate(sc)
    detours = [e for e in traj.events if e.kind == "pedestrian_detour"]
    assert len(detours) <= 2
    end = sc.pedestrian.route[-1]
    assert math.hypot(traj.pedestrian_x[-1] - end[0], traj.pedestrian_y[-1] - end[1]) < 1e-6
