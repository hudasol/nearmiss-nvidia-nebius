from __future__ import annotations

import pytest

from nearmiss.domain.models import (
    RelocateObstruction,
    Scenario,
    StressAxis,
    StressEnvelope,
)
from nearmiss.domain.transforms import apply_intervention, apply_stress
from nearmiss.evaluation.criteria import classify_run, decide, load_criteria
from nearmiss.evaluation.metrics import RunMetrics
from nearmiss.evaluation.runner import run_strategy
from nearmiss.evaluation.sampling import halton, latin_hypercube
from nearmiss.evaluation.strategies import (
    LEAD_AFTER_S,
    _aligned_offset,
    conflict_timing,
    conventional_sweep,
    targeted_search,
)

ENVELOPE = StressEnvelope(
    axes=[
        StressAxis(name="forklift_speed", low=1.8, high=3.3, unit="m/s"),
        StressAxis(name="pedestrian_speed", low=0.9, high=1.8, unit="m/s"),
        StressAxis(name="pedestrian_start_offset", low=-6.0, high=6.0, unit="s"),
        StressAxis(name="reaction_time", low=0.75, high=1.5, unit="s"),
        StressAxis(name="braking_decel", low=1.5, high=2.5, unit="m/s²"),
        StressAxis(name="obstruction_shift_x", low=-1.0, high=1.0, unit="m"),
        StressAxis(name="obstruction_shift_y", low=-0.5, high=0.5, unit="m"),
    ]
)


def _metrics(**kw: object) -> RunMetrics:
    base: dict[str, object] = {
        "min_separation_m": 1.0,
        "t_min_separation_s": 1.0,
        "min_moving_separation_m": 1.0,
        "t_min_moving_separation_s": 1.0,
        "collision": False,
        "first_contact_s": None,
        "min_ttc_s": None,
        "pet_s": None,
        "forklift_entered_conflict_zone": False,
        "pedestrian_entered_conflict_zone": False,
        "forklift_peak_speed_mps": 2.0,
        "forklift_speed_at_min_sep_mps": 0.0,
    }
    base.update(kw)
    return RunMetrics.model_validate(base)


def test_criteria_load_and_classify() -> None:
    c = load_criteria()
    assert c.near_miss_threshold_m == 0.5
    assert classify_run(_metrics(), c) == "clear"
    assert classify_run(_metrics(min_moving_separation_m=0.49), c) == "near_miss"
    assert classify_run(_metrics(min_moving_separation_m=0.5), c) == "clear"
    assert classify_run(_metrics(collision=True, min_separation_m=-0.1), c) == "collision"
    # A close approach to a stationary truck alone is not a near miss.
    assert classify_run(_metrics(min_separation_m=0.2, min_moving_separation_m=None), c) == "clear"
    assert decide(["clear", "clear"]) == "accepted"
    assert decide(["clear", "near_miss"]) == "rejected"


def test_latin_hypercube_is_stratified_and_seeded() -> None:
    n = 16
    pts = latin_hypercube(ENVELOPE, n, seed=3)
    assert pts == latin_hypercube(ENVELOPE, n, seed=3)
    assert pts != latin_hypercube(ENVELOPE, n, seed=4)
    for axis in ENVELOPE.axes:
        strata = sorted(int((p[axis.name] - axis.low) / (axis.high - axis.low) * n) for p in pts)
        assert strata == list(range(n))


def test_halton_within_bounds_and_deterministic() -> None:
    pts = halton(ENVELOPE, 64)
    assert pts == halton(ENVELOPE, 64)
    for p in pts:
        for axis in ENVELOPE.axes:
            assert axis.low <= p[axis.name] <= axis.high


def test_conventional_sweep_starts_with_nominal(reference: Scenario) -> None:
    gen = conventional_sweep(reference, ENVELOPE, 10, seed=1)
    batch = next(gen)
    assert len(batch) == 10
    assert batch[0]["forklift_speed"] == reference.forklift.cruise_speed.value
    assert batch[0]["pedestrian_start_offset"] == 0.0


def test_aligned_offset_places_pedestrian_in_stopping_window(reference: Scenario) -> None:
    point = {
        "forklift_speed": 3.0,
        "pedestrian_speed": 1.2,
        "pedestrian_start_offset": 0.0,
        "reaction_time": 1.2,
        "braking_decel": 2.0,
        "obstruction_shift_x": 0.0,
        "obstruction_shift_y": 0.0,
    }
    for frac in (0.0, 0.5, 1.0):
        aligned = _aligned_offset(reference, ENVELOPE, point, frac)
        timing = conflict_timing(apply_stress(reference, aligned), {})
        assert timing is not None
        lead = timing.forklift_front_s - timing.pedestrian_lane_s
        expected = -LEAD_AFTER_S + frac * (timing.stopping_time_s + LEAD_AFTER_S)
        assert lead == pytest.approx(expected, abs=1e-6)


def test_targeted_search_respects_budget_and_bounds(reference: Scenario) -> None:
    result = run_strategy(reference, ENVELOPE, "targeted_search", 24, seed=5)
    assert len(result.runs) == 24
    assert result.runs[0].stress_point["pedestrian_start_offset"] == 0.0
    for r in result.runs:
        for axis in ENVELOPE.axes:
            assert axis.low - 1e-9 <= r.stress_point[axis.name] <= axis.high + 1e-9
    again = run_strategy(reference, ENVELOPE, "targeted_search", 24, seed=5)
    assert [r.config_hash for r in again.runs] == [r.config_hash for r in result.runs]


def test_targeted_search_generator_protocol(reference: Scenario) -> None:
    gen = targeted_search(reference, ENVELOPE, 12, seed=0)
    first = next(gen)
    assert first[0]["pedestrian_start_offset"] == 0.0
    assert 1 < len(first) <= 12


def test_decision_matches_run_outcomes(reference: Scenario) -> None:
    result = run_strategy(reference, ENVELOPE, "conventional_sweep", 12, seed=2)
    unsafe = [r for r in result.runs if r.outcome != "clear"]
    assert result.unsafe_runs == len(unsafe)
    assert result.decision == ("rejected" if unsafe else "accepted")
    if unsafe:
        assert result.first_failure_index == unsafe[0].index


def test_small_relocation_looks_effective_at_nominal_but_fails_under_stress(
    reference: Scenario,
) -> None:
    """The headline behaviour, produced by simulation rather than asserted by hand:
    moving the stack 3 m west clears the replayed incident, yet the targeted stress
    search finds conditions inside the envelope where the change does not hold."""
    moved, _ = apply_intervention(
        reference,
        RelocateObstruction(id="r3", label="r3", obstruction_id="stack-a", offset_m=(-3.0, 0.0)),
    )
    # The search is stochastic: at budget 48 it rejected this change for 19 of 20 seeds
    # in exploration (seed 0 was the exception). The benchmark measures the rate; this
    # test pins two seeds as a regression check.
    for seed in (1, 2):
        result = run_strategy(moved, ENVELOPE, "targeted_search", 48, seed=seed)
        nominal = result.runs[0]
        assert nominal.outcome == "clear"
        assert nominal.metrics.min_moving_separation_m is not None
        assert nominal.metrics.min_moving_separation_m > 2.0
        assert result.decision == "rejected"
        worst = result.runs[result.worst_run_index]
        assert worst.outcome in ("near_miss", "collision")
