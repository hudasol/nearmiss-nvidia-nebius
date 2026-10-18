"""Reproducible experiment execution: simulate stress points, compute metrics, classify.

The runner is the only place where the simulator, the metrics and the criteria meet.
Every run is recorded with its stress point, configuration hash and trajectory hash, so
any run can be replayed and verified later.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Executor

from pydantic import BaseModel

from nearmiss.domain.models import Scenario, StressEnvelope, StressPoint
from nearmiss.domain.transforms import apply_stress
from nearmiss.evaluation.criteria import (
    Decision,
    EvaluationCriteria,
    RunOutcome,
    classify_run,
    decide,
    load_criteria,
)
from nearmiss.evaluation.metrics import RunMetrics, compute_metrics
from nearmiss.evaluation.strategies import STRATEGIES, Observation
from nearmiss.simulation.engine import Trajectory, simulate


class RunRecord(BaseModel):
    index: int
    batch: int
    stress_point: dict[str, float]
    config_hash: str
    trajectory_hash: str
    metrics: RunMetrics
    outcome: RunOutcome
    margin_m: float
    elapsed_ms: float


class StrategyResult(BaseModel):
    strategy: str
    seed: int
    budget: int
    criteria_id: str
    decision: Decision
    runs: list[RunRecord]
    first_failure_index: int | None
    unsafe_runs: int
    worst_run_index: int
    runtime_s: float


def margin(metrics: RunMetrics, criteria: EvaluationCriteria) -> float:
    if metrics.collision:
        return min(metrics.min_separation_m, 0.0) - criteria.near_miss_threshold_m
    sep = metrics.min_moving_separation_m
    if sep is None:
        sep = metrics.min_separation_m
    return sep - criteria.near_miss_threshold_m


def simulate_point(scenario: Scenario, point: StressPoint) -> tuple[Scenario, Trajectory]:
    stressed = apply_stress(scenario, point)
    return stressed, simulate(stressed)


def execute_point(
    scenario_json: dict[str, object], point: StressPoint
) -> tuple[str, str, dict[str, object], float]:
    """Process-pool friendly: takes and returns plain data."""
    scenario = Scenario.model_validate(scenario_json)
    t0 = time.perf_counter()
    stressed, traj = simulate_point(scenario, point)
    metrics = compute_metrics(stressed, traj)
    elapsed = (time.perf_counter() - t0) * 1000
    return traj.config_hash, traj.trajectory_hash, metrics.model_dump(), elapsed


Mapper = Callable[[list[StressPoint]], list[tuple[str, str, dict[str, object], float]]]


def make_mapper(scenario: Scenario, executor: Executor | None = None) -> Mapper:
    scenario_json = scenario.model_dump(mode="json")

    def run(points: list[StressPoint]) -> list[tuple[str, str, dict[str, object], float]]:
        if executor is None:
            return [execute_point(scenario_json, p) for p in points]
        return list(executor.map(execute_point, [scenario_json] * len(points), points))

    return run


def run_strategy(
    scenario: Scenario,
    envelope: StressEnvelope,
    strategy: str,
    budget: int,
    seed: int,
    criteria: EvaluationCriteria | None = None,
    executor: Executor | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> StrategyResult:
    """Run one strategy to its budget against an (already intervened) scenario."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy '{strategy}'")
    if budget < 1:
        raise ValueError("budget must allow at least the nominal run")
    criteria = criteria or load_criteria()
    mapper = make_mapper(scenario, executor)
    t0 = time.perf_counter()
    gen = STRATEGIES[strategy](scenario, envelope, budget, seed)
    records: list[RunRecord] = []
    observations: list[Observation] = []
    batch_no = 0
    try:
        points = next(gen)
        while True:
            points = points[: budget - len(records)]
            for point, (cfg, th, mdump, ms) in zip(points, mapper(points), strict=True):
                metrics = RunMetrics.model_validate(mdump)
                m = margin(metrics, criteria)
                records.append(
                    RunRecord(
                        index=len(records),
                        batch=batch_no,
                        # Full precision: replaying the stored point must reproduce the hashes.
                        stress_point=dict(point),
                        config_hash=cfg,
                        trajectory_hash=th,
                        metrics=metrics,
                        outcome=classify_run(metrics, criteria),
                        margin_m=round(m, 6),
                        elapsed_ms=round(ms, 3),
                    )
                )
                observations.append(Observation(point=point, margin_m=m))
            if on_progress is not None:
                on_progress(len(records), budget)
            if len(records) >= budget:
                break
            batch_no += 1
            points = gen.send(list(observations))
    except StopIteration:
        pass
    finally:
        gen.close()

    outcomes = [r.outcome for r in records]
    first_failure = next((r.index for r in records if r.outcome != "clear"), None)
    worst = min(records, key=lambda r: r.margin_m)
    return StrategyResult(
        strategy=strategy,
        seed=seed,
        budget=budget,
        criteria_id=criteria.id,
        decision=decide(outcomes),
        runs=records,
        first_failure_index=first_failure,
        unsafe_runs=sum(1 for o in outcomes if o != "clear"),
        worst_run_index=worst.index,
        runtime_s=round(time.perf_counter() - t0, 4),
    )
