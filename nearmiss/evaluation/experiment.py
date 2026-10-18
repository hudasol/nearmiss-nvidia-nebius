"""Executes a compiled plan: every arm (baseline and interventions) under every strategy,
with matched seeds and budgets, then summarises per-arm verdicts."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Executor
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from nearmiss.compiler.spec import ExperimentPlan
from nearmiss.domain.models import StressAxis, StressEnvelope
from nearmiss.evaluation.criteria import Decision, load_criteria
from nearmiss.evaluation.runner import RunRecord, StrategyResult, run_strategy

Verdict = Literal[
    "baseline_unsafe",
    "baseline_no_failure_found",
    "fails_at_nominal",
    "fails_under_stress",
    "no_failure_found",
]

VERDICT_TEXT: dict[str, str] = {
    "baseline_unsafe": "As-is layout produces unsafe runs.",
    "baseline_no_failure_found": "No unsafe run found for the as-is layout within the budget.",
    "fails_at_nominal": "Unsafe even under the replayed incident conditions.",
    "fails_under_stress": "Clears the replayed incident, but fails under at least one stress "
    "condition inside the envelope.",
    "no_failure_found": "No unsafe run found within the budget. This is not proof of safety.",
}


class Cancelled(Exception):
    """Raised when the caller requests cancellation between batches."""


class ArmRun(BaseModel):
    arm_id: str
    strategy: str
    result: StrategyResult


class ArmSummary(BaseModel):
    arm_id: str
    label: str
    is_baseline: bool
    nominal: RunRecord
    decisions: dict[str, Decision]
    unsafe_runs: dict[str, int]
    first_failure_index: dict[str, int | None]
    worst_run: dict[str, RunRecord]
    verdict: Verdict
    verdict_text: str


class ExperimentResult(BaseModel):
    plan_hash: str
    criteria_id: str
    started_at: str
    finished_at: str
    runtime_s: float
    primary_strategy: str
    arm_runs: list[ArmRun]
    summaries: list[ArmSummary]


def _verdict(is_baseline: bool, nominal: RunRecord, decision: Decision) -> Verdict:
    if is_baseline:
        return "baseline_unsafe" if decision == "rejected" else "baseline_no_failure_found"
    if nominal.outcome != "clear":
        return "fails_at_nominal"
    return "fails_under_stress" if decision == "rejected" else "no_failure_found"


def execute_plan(
    plan: ExperimentPlan,
    executor: Executor | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ExperimentResult:
    criteria = load_criteria()
    if criteria.id != plan.criteria_id:
        raise ValueError("plan was compiled against different criteria")
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    done = 0
    arm_runs: list[ArmRun] = []
    primary = plan.strategies[0]
    for arm in plan.arms:
        for strategy in plan.strategies:
            if should_cancel is not None and should_cancel():
                raise Cancelled()
            base_done = done

            def progress(n: int, _budget: int, base: int = base_done) -> None:
                if on_progress is not None:
                    on_progress(base + n, plan.total_runs)

            # Nominal-only plans have no envelope; a zero-width axis keeps one code path.
            env = plan.stress or StressEnvelope(
                axes=[StressAxis(name="pedestrian_start_offset", low=0.0, high=0.0, unit="s")]
            )
            result = run_strategy(
                arm.scenario,
                env,
                strategy,
                plan.budget_per_arm,
                plan.seed,
                criteria=criteria,
                executor=executor,
                on_progress=progress,
            )
            done += len(result.runs)
            arm_runs.append(ArmRun(arm_id=arm.arm_id, strategy=strategy, result=result))

    summaries: list[ArmSummary] = []
    for arm in plan.arms:
        runs = {r.strategy: r.result for r in arm_runs if r.arm_id == arm.arm_id}
        nominal = runs[primary].runs[0]
        verdict = _verdict(arm.is_baseline, nominal, runs[primary].decision)
        summaries.append(
            ArmSummary(
                arm_id=arm.arm_id,
                label=arm.label,
                is_baseline=arm.is_baseline,
                nominal=nominal,
                decisions={k: v.decision for k, v in runs.items()},
                unsafe_runs={k: v.unsafe_runs for k, v in runs.items()},
                first_failure_index={k: v.first_failure_index for k, v in runs.items()},
                worst_run={k: v.runs[v.worst_run_index] for k, v in runs.items()},
                verdict=verdict,
                verdict_text=VERDICT_TEXT[verdict],
            )
        )
    return ExperimentResult(
        plan_hash=plan.plan_hash,
        criteria_id=criteria.id,
        started_at=started.isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        runtime_s=round(time.perf_counter() - t0, 3),
        primary_strategy=primary,
        arm_runs=arm_runs,
        summaries=summaries,
    )
