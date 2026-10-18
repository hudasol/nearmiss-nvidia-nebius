"""Pre-registered benchmark: physics-targeted search vs conventional parameter sweep.

Ground-truth labels come from a dense Halton reference sweep that neither strategy uses.
A case is labelled UNSAFE if any reference run is a collision or near miss under the
pre-declared criteria, and NO-FAILURE-FOUND otherwise (the reference sweep is a lower
bound on unsafety: a failure region smaller than its resolution can be missed).

Run: ``python -m nearmiss.evaluation.benchmark --workers 15``
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
import time
from concurrent.futures import Executor, ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter

from nearmiss.compiler.compiler import compile_experiment, default_axis
from nearmiss.domain.catalog import REPO_ROOT, reference_scenarios
from nearmiss.domain.models import STRESS_AXIS_META, Intervention, Scenario, StressEnvelope
from nearmiss.domain.transforms import apply_intervention
from nearmiss.evaluation.criteria import classify_run, load_criteria
from nearmiss.evaluation.metrics import METRICS_VERSION, RunMetrics
from nearmiss.evaluation.runner import execute_point, run_strategy
from nearmiss.evaluation.sampling import halton
from nearmiss.evaluation.strategies import STRATEGY_VERSION
from nearmiss.simulation.engine import ENGINE_VERSION

CASES_PATH = REPO_ROOT / "data" / "benchmark" / "cases.v1.json"
RESULTS_PATH = REPO_ROOT / "data" / "benchmark" / "results.v1.json"
INTERVENTION = TypeAdapter(Intervention)
STRATEGIES = ("targeted_search", "conventional_sweep", "nominal_only")
Label = Literal["unsafe", "no_failure_found"]


class OracleResult(BaseModel):
    points: int
    unsafe_runs: int
    unsafe_fraction: float
    label: Label
    worst_margin_m: float
    worst_point: dict[str, float]
    runtime_s: float


class SeedOutcome(BaseModel):
    seed: int
    decision: Literal["accepted", "rejected"]
    first_failure_index: int | None
    unsafe_runs: int
    worst_margin_m: float
    runtime_s: float
    failing_point: dict[str, float] | None


class CaseResult(BaseModel):
    case_id: str
    scenario_id: str
    role: Literal["development", "held_out"]
    category: Literal["as_is", "negative_control", "candidate"]
    intervention: dict[str, Any] | None
    nominal_outcome: str
    nominal_moving_separation_m: float | None
    oracle: OracleResult
    strategies: dict[str, list[SeedOutcome]]
    oracle_misses: list[dict[str, Any]]


def scenario_envelope(scenario: Scenario, interventions: list[dict[str, Any]]) -> StressEnvelope:
    """Default envelope for all seven axes; the start-offset range is widened to the
    compiler's corrected range if any case would otherwise be timing-unreachable."""
    env = StressEnvelope(axes=[default_axis(n, scenario) for n in STRESS_AXIS_META])
    lo = env.axis("pedestrian_start_offset").low  # type: ignore[union-attr]
    hi = env.axis("pedestrian_start_offset").high  # type: ignore[union-attr]
    for iv in interventions:
        spec = {
            "title": "benchmark envelope check",
            "scenario_id": scenario.id,
            "arms": [{"intervention": iv}],
            "stress": env.model_dump(mode="json"),
            "budget_per_arm": 48,
        }
        result = compile_experiment(spec, lambda _id: scenario)
        errors = [f for f in result.findings if f.severity == "error"]
        if not errors:
            continue
        if all(f.code == "timing_unreachable" for f in errors) and result.corrected_spec:
            corrected = StressEnvelope.model_validate(result.corrected_spec["stress"])
            axis = corrected.axis("pedestrian_start_offset")
            assert axis is not None
            lo, hi = min(lo, axis.low), max(hi, axis.high)
            continue
        raise ValueError(f"{scenario.id}/{iv['id']} does not compile: {[f.code for f in errors]}")
    return StressEnvelope(
        axes=[
            a.model_copy(update={"low": lo, "high": hi}) if a.name == "pedestrian_start_offset" else a
            for a in env.axes
        ]
    )


def _oracle(
    scenario: Scenario, envelope: StressEnvelope, n: int, skip: int, executor: Executor
) -> OracleResult:
    criteria = load_criteria()
    pts = halton(envelope, n, skip=skip)
    t0 = time.perf_counter()
    sj = scenario.model_dump(mode="json")
    rows = list(executor.map(execute_point, [sj] * len(pts), pts, chunksize=64))
    unsafe = 0
    worst = (math.inf, pts[0])
    for p, (_, _, mdump, _) in zip(pts, rows, strict=True):
        m = RunMetrics.model_validate(mdump)
        if classify_run(m, criteria) != "clear":
            unsafe += 1
        sep = m.min_separation_m if m.collision else (m.min_moving_separation_m or m.min_separation_m)
        if sep < worst[0]:
            worst = (sep, p)
    return OracleResult(
        points=n,
        unsafe_runs=unsafe,
        unsafe_fraction=round(unsafe / n, 6),
        label="unsafe" if unsafe else "no_failure_found",
        worst_margin_m=round(worst[0] - criteria.near_miss_threshold_m, 6),
        worst_point={k: round(v, 6) for k, v in worst[1].items()},
        runtime_s=round(time.perf_counter() - t0, 3),
    )


def _git_commit() -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.CalledProcessError):
        return None


def run_benchmark(
    workers: int,
    seeds: list[int] | None = None,
    oracle_points: int | None = None,
    only: list[str] | None = None,
) -> dict[str, Any]:
    cfg = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    budget = int(cfg["budget_per_evaluation"])
    seeds = seeds if seeds is not None else list(cfg["seeds"])
    n_oracle = oracle_points or int(cfg["oracle"]["points"])
    skip = int(cfg["oracle"]["skip"])
    criteria = load_criteria()
    scenarios = reference_scenarios()
    started = datetime.now(UTC)
    t_start = time.perf_counter()
    cases: list[CaseResult] = []
    envelopes: dict[str, Any] = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for group in cfg["scenarios"]:
            if only and group["scenario_id"] not in only:
                continue
            base = scenarios[group["scenario_id"]]
            ivs = [c["intervention"] for c in group["cases"] if c["intervention"]]
            envelope = scenario_envelope(base, ivs)
            envelopes[base.id] = envelope.model_dump(mode="json")
            for c in group["cases"]:
                iv = INTERVENTION.validate_python(c["intervention"]) if c["intervention"] else None
                sc = apply_intervention(base, iv)[0] if iv else base
                print(f"[{time.perf_counter() - t_start:7.1f}s] {c['case_id']}: oracle…", flush=True)
                oracle = _oracle(sc, envelope, n_oracle, skip, ex)
                per_strategy: dict[str, list[SeedOutcome]] = {s: [] for s in STRATEGIES}
                misses: list[dict[str, Any]] = []
                nominal = None
                for seed in seeds:
                    for strat in STRATEGIES:
                        name = "conventional_sweep" if strat == "nominal_only" else strat
                        b = 1 if strat == "nominal_only" else budget
                        if strat == "nominal_only" and seed != seeds[0]:
                            per_strategy[strat].append(per_strategy[strat][0].model_copy(update={"seed": seed}))
                            continue
                        r = run_strategy(sc, envelope, name, b, seed, criteria=criteria, executor=ex)
                        nominal = nominal or r.runs[0]
                        fail = (
                            r.runs[r.first_failure_index].stress_point
                            if r.first_failure_index is not None
                            else None
                        )
                        per_strategy[strat].append(
                            SeedOutcome(
                                seed=seed,
                                decision=r.decision,
                                first_failure_index=r.first_failure_index,
                                unsafe_runs=r.unsafe_runs,
                                worst_margin_m=r.runs[r.worst_run_index].margin_m,
                                runtime_s=r.runtime_s,
                                failing_point=fail,
                            )
                        )
                        if r.decision == "rejected" and oracle.label == "no_failure_found" and fail:
                            misses.append({"strategy": strat, "seed": seed, "failing_point": fail})
                assert nominal is not None
                cases.append(
                    CaseResult(
                        case_id=c["case_id"],
                        scenario_id=base.id,
                        role=group["role"],
                        category=c["category"],
                        intervention=c["intervention"],
                        nominal_outcome=nominal.outcome,
                        nominal_moving_separation_m=nominal.metrics.min_moving_separation_m,
                        oracle=oracle,
                        strategies=per_strategy,
                        oracle_misses=misses,
                    )
                )
                tgt = sum(o.decision == "rejected" for o in per_strategy["targeted_search"])
                cnv = sum(o.decision == "rejected" for o in per_strategy["conventional_sweep"])
                print(
                    f"           label={oracle.label} ({oracle.unsafe_runs}/{n_oracle}) "
                    f"targeted rejects {tgt}/{len(seeds)}, conventional rejects {cnv}/{len(seeds)}",
                    flush=True,
                )

    return {
        "benchmark_id": cfg["id"],
        "generated_at": started.isoformat(),
        "runtime_s": round(time.perf_counter() - t_start, 1),
        "provenance": {
            "git_commit": _git_commit(),
            "engine_version": ENGINE_VERSION,
            "metrics_version": METRICS_VERSION,
            "strategy_version": STRATEGY_VERSION,
            "criteria_id": criteria.id,
            "near_miss_threshold_m": criteria.near_miss_threshold_m,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "workers": workers,
        },
        "config": {
            "budget_per_evaluation": budget,
            "seeds": seeds,
            "oracle_points": n_oracle,
            "oracle_skip": skip,
        },
        "envelopes": envelopes,
        "cases": [c.model_dump(mode="json") for c in cases],
        "summary": summarise(cases),
    }


def _rate(num: int, den: int) -> dict[str, Any]:
    if den == 0:
        return {"value": None, "num": 0, "den": 0, "wilson95": None}
    p = num / den
    z = 1.96
    centre = (p + z * z / (2 * den)) / (1 + z * z / den)
    half = z * math.sqrt(p * (1 - p) / den + z * z / (4 * den * den)) / (1 + z * z / den)
    return {
        "value": round(p, 4),
        "num": num,
        "den": den,
        "wilson95": [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)],
    }


def summarise(cases: list[CaseResult]) -> dict[str, Any]:
    """Balanced metrics over (case, seed) evaluations, by subset."""
    subsets = {
        "all_interventions": [c for c in cases if c.category != "as_is"],
        "held_out_interventions": [c for c in cases if c.category != "as_is" and c.role == "held_out"],
        "development_interventions": [
            c for c in cases if c.category != "as_is" and c.role == "development"
        ],
        "as_is_cases": [c for c in cases if c.category == "as_is"],
    }
    out: dict[str, Any] = {}
    for name, subset in subsets.items():
        unsafe = [c for c in subset if c.oracle.label == "unsafe"]
        safe = [c for c in subset if c.oracle.label == "no_failure_found"]
        entry: dict[str, Any] = {"cases": len(subset), "unsafe_cases": len(unsafe), "safe_cases": len(safe)}
        for strat in STRATEGIES:
            rej_unsafe = sum(o.decision == "rejected" for c in unsafe for o in c.strategies[strat])
            n_unsafe = sum(len(c.strategies[strat]) for c in unsafe)
            acc_safe = sum(o.decision == "accepted" for c in safe for o in c.strategies[strat])
            n_safe = sum(len(c.strategies[strat]) for c in safe)
            ffs = sorted(
                o.first_failure_index
                for c in unsafe
                for o in c.strategies[strat]
                if o.first_failure_index is not None
            )
            runtimes = [o.runtime_s for c in subset for o in c.strategies[strat]]
            entry[strat] = {
                "unsafe_intervention_recall": _rate(rej_unsafe, n_unsafe),
                "false_acceptance_rate": _rate(n_unsafe - rej_unsafe, n_unsafe),
                "effective_intervention_acceptance_rate": _rate(acc_safe, n_safe),
                "false_rejection_rate": _rate(n_safe - acc_safe, n_safe),
                "median_runs_to_first_failure": (ffs[len(ffs) // 2] + 1) if ffs else None,
                "mean_runtime_s_per_evaluation": round(sum(runtimes) / len(runtimes), 4)
                if runtimes
                else None,
            }
        out[name] = entry
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seeds", type=int, default=None, help="use seeds 0..N-1 (default: config)")
    ap.add_argument("--oracle-points", type=int, default=None)
    ap.add_argument("--only", nargs="*", default=None, help="scenario ids to include")
    ap.add_argument("--out", type=Path, default=RESULTS_PATH)
    args = ap.parse_args(argv)
    seeds = list(range(args.seeds)) if args.seeds is not None else None
    result = run_benchmark(args.workers, seeds, args.oracle_points, args.only)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out} in {result['runtime_s']} s")


if __name__ == "__main__":
    main()
