from __future__ import annotations

import pytest

from nearmiss.compiler.compiler import compile_experiment
from nearmiss.domain.catalog import reference_scenarios
from nearmiss.evaluation.experiment import Cancelled, execute_plan

from .test_compiler import FULL_AXES, RELOCATE, REROUTE


def _plan(**kw: object):  # type: ignore[no-untyped-def]
    spec = {
        "title": "e2e",
        "scenario_id": "dc-aisle4-crossing",
        "arms": [{"intervention": RELOCATE}, {"intervention": REROUTE}],
        "stress": {"axes": FULL_AXES},
        "budget_per_arm": 16,
        "seed": 1,
    }
    spec.update(kw)
    result = compile_experiment(spec, reference_scenarios().get)
    assert result.status == "accepted", result.findings
    assert result.plan is not None
    return result.plan


def test_execute_plan_runs_every_arm_under_matched_conditions() -> None:
    plan = _plan()
    progress: list[int] = []
    result = execute_plan(plan, on_progress=lambda n, total: progress.append(n))
    assert len(result.arm_runs) == 3 * 2
    assert progress[-1] == plan.total_runs
    # Matched conditions: the conventional sweep uses the same LHS points for every arm.
    sweeps = [r.result for r in result.arm_runs if r.strategy == "conventional_sweep"]
    points = [[run.stress_point for run in s.runs] for s in sweeps]
    assert points[0] == points[1] == points[2]
    by_id = {s.arm_id: s for s in result.summaries}
    baseline = by_id["baseline"]
    assert baseline.is_baseline
    # The replayed incident itself is a near miss.
    assert baseline.nominal.outcome == "near_miss"
    assert baseline.verdict == "baseline_unsafe"
    moved = by_id["move-stack-3m"]
    assert moved.nominal.outcome == "clear"
    assert moved.verdict in ("fails_under_stress", "no_failure_found")


def test_nominal_claim_executes_single_replays() -> None:
    plan = _plan(stress=None, claim="nominal_improvement")
    result = execute_plan(plan)
    assert all(len(r.result.runs) == 1 for r in result.arm_runs)
    assert {s.arm_id: s.nominal.outcome for s in result.summaries}["baseline"] == "near_miss"


def test_cancellation_between_batches() -> None:
    plan = _plan()
    with pytest.raises(Cancelled):
        execute_plan(plan, should_cancel=lambda: True)
