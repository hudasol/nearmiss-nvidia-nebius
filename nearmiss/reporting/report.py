"""Evidence reports generated directly from persisted experiment records.

Every number in a report is read from stored runs. Headline sentences are filled from
templates with those numbers; no model writes or edits report content. Model-generated
material (interpretations, candidates) is listed in its own labelled section.
"""

from __future__ import annotations

from typing import Any

from nearmiss.domain.models import STRESS_AXIS_META, Scenario
from nearmiss.evaluation.criteria import load_criteria
from nearmiss.evaluation.experiment import VERDICT_TEXT
from nearmiss.evaluation.metrics import METRIC_DEFINITIONS
from nearmiss.persistence.store import Store, now

REPORT_SCHEMA = "nearmiss.report/1"
STRATEGY_LABEL = {
    "targeted_search": "NearMiss targeted search",
    "conventional_sweep": "Conventional parameter sweep (Latin hypercube)",
}

LIMITATIONS = [
    "Planar (top-down) geometry: obstructions are treated as taller than eye height; mast, "
    "overhead-guard and low-obstruction effects are not modelled.",
    "One forklift and one pedestrian; no reversing, steering, swerving or rear swing.",
    "The pedestrian does not yield to an approaching truck (distracted-pedestrian case) and "
    "takes no evasive action.",
    "Visibility is geometric line of sight within a field of view; lighting, glare and "
    "attention are not modelled.",
    "Reference scenarios are synthetic and illustrative, not measurements from a real site.",
    "A finding of 'no failure found' covers only the executed runs within the stated "
    "envelope; it is not proof of safety.",
    "NearMiss is a simulation-based decision-support tool, not a certified safety system.",
]


def _fmt(v: float | None, unit: str = "", nd: int = 2) -> str:
    return "n/a" if v is None else f"{v:.{nd}f}{(' ' + unit) if unit else ''}"


def _param_rows(sc: Scenario) -> list[dict[str, Any]]:
    rows = []
    for owner, name, p in (
        ("forklift", "cruise_speed", sc.forklift.cruise_speed),
        ("forklift", "braking_decel", sc.forklift.braking_decel),
        ("forklift", "reaction_time", sc.forklift.reaction_time),
        ("forklift", "max_accel", sc.forklift.max_accel),
        ("pedestrian", "walking_speed", sc.pedestrian.walking_speed),
        ("pedestrian", "start_time", sc.pedestrian.start_time),
    ):
        rows.append(
            {
                "parameter": f"{owner}.{name}",
                "value": p.value,
                "unit": p.unit,
                "provenance": p.provenance,
                "source": p.source,
                "uncertainty": p.uncertainty,
            }
        )
    return rows


def _describe_outcome(outcome: str, sep: float | None) -> str:
    if outcome == "collision":
        return "collision" + (f", {abs(sep):.2f} m overlap" if sep is not None and sep < 0 else "")
    return f"{outcome.replace('_', ' ')} at {_fmt(sep, 'm')}"


def _describe_point(point: dict[str, float]) -> str:
    parts = []
    for k, v in point.items():
        unit = STRESS_AXIS_META.get(k, ("", "", k))[0]
        parts.append(f"{STRESS_AXIS_META.get(k, ('', '', k))[2].lower()} {v:.2f} {unit}")
    return ", ".join(parts)


def build_report(store: Store, experiment_id: str) -> dict[str, Any]:
    exp = store.get_experiment(experiment_id)
    if exp is None:
        raise KeyError(experiment_id)
    if exp["status"] != "completed" or exp["plan"] is None:
        raise ValueError(f"experiment is {exp['status']}; reports need a completed experiment")
    plan = exp["plan"]
    summary = exp["result_summary"] or {}
    criteria = load_criteria()
    scenario = Scenario.model_validate(plan["arms"][0]["scenario"])
    sres = {(r["arm_id"], r["strategy"]): r for r in store.strategy_results(experiment_id)}
    runs = store.runs(experiment_id)
    primary = plan["strategies"][0]

    arms_out: list[dict[str, Any]] = []
    failure_cases: list[dict[str, Any]] = []
    headline: list[str] = []
    for arm in plan["arms"]:
        arm_id = arm["arm_id"]
        arm_sum = next((s for s in summary.get("summaries", []) if s["arm_id"] == arm_id), None)
        strategies: dict[str, Any] = {}
        for strat in plan["strategies"]:
            r = sres.get((arm_id, strat))
            if r is None:
                continue
            arm_runs = [x for x in runs if x["arm_id"] == arm_id and x["strategy"] == strat]
            worst = arm_runs[r["worst_run_index"]]
            strategies[strat] = {
                "label": STRATEGY_LABEL.get(strat, strat),
                "decision": r["decision"],
                "runs": len(arm_runs),
                "unsafe_runs": r["unsafe_runs"],
                "collisions": sum(1 for x in arm_runs if x["outcome"] == "collision"),
                "first_failure_index": r["first_failure_index"],
                "runtime_s": r["runtime_s"],
                "worst_run": {
                    "index": worst["idx"],
                    "outcome": worst["outcome"],
                    "min_moving_separation_m": worst["metrics"]["min_moving_separation_m"],
                    "stress_point": worst["stress_point"],
                    "config_hash": worst["config_hash"],
                    "trajectory_hash": worst["trajectory_hash"],
                },
            }
            for x in arm_runs:
                if x["outcome"] != "clear":
                    failure_cases.append(
                        {
                            "arm_id": arm_id,
                            "arm_label": arm["label"],
                            "strategy": strat,
                            "index": x["idx"],
                            "outcome": x["outcome"],
                            "min_moving_separation_m": x["metrics"]["min_moving_separation_m"],
                            "min_ttc_s": x["metrics"]["min_ttc_s"],
                            "stress_point": x["stress_point"],
                            "config_hash": x["config_hash"],
                            "trajectory_hash": x["trajectory_hash"],
                        }
                    )
        nominal = next(
            (
                x
                for x in runs
                if x["arm_id"] == arm_id and x["strategy"] == primary and x["idx"] == 0
            ),
            None,
        )
        verdict = arm_sum["verdict"] if arm_sum else None
        arms_out.append(
            {
                "arm_id": arm_id,
                "label": arm["label"],
                "is_baseline": arm["is_baseline"],
                "intervention": arm["intervention"],
                "changes": arm["changes"],
                "verdict": verdict,
                "verdict_text": VERDICT_TEXT.get(verdict or "", ""),
                "nominal": None
                if nominal is None
                else {
                    "outcome": nominal["outcome"],
                    "min_moving_separation_m": nominal["metrics"]["min_moving_separation_m"],
                    "collision": nominal["metrics"]["collision"],
                    "pet_s": nominal["metrics"]["pet_s"],
                },
                "strategies": strategies,
            }
        )
        # Template headline sentences from stored numbers.
        p = strategies.get(primary)
        if nominal is None or p is None:
            continue
        nsep = nominal["metrics"]["min_moving_separation_m"]
        if arm["is_baseline"]:
            headline.append(
                f"As-is layout: the replayed incident is a {nominal['outcome'].replace('_', ' ')} "
                f"(minimum clearance {_fmt(nsep, 'm')}); {p['unsafe_runs']} of {p['runs']} stress "
                "runs were unsafe."
            )
        elif verdict == "fails_under_stress":
            w = p["worst_run"]
            headline.append(
                f"{arm['label']}: clears the replayed incident (minimum clearance {_fmt(nsep, 'm')}) "
                f"but failed in {p['unsafe_runs']} of {p['runs']} stress runs. Worst: "
                f"{_describe_outcome(w['outcome'], w['min_moving_separation_m'])} with "
                f"{_describe_point(w['stress_point'])}."
            )
        elif verdict == "fails_at_nominal":
            headline.append(
                f"{arm['label']}: does not resolve the replayed incident (minimum clearance "
                f"{_fmt(nsep, 'm')})."
            )
        else:
            headline.append(
                f"{arm['label']}: no unsafe run in {p['runs']} stress runs (minimum clearance at "
                f"nominal {_fmt(nsep, 'm')}). Not proof of safety."
            )
        c = strategies.get("conventional_sweep")
        if c is not None and primary != "conventional_sweep" and c["decision"] != p["decision"]:

            def found(d: dict[str, Any]) -> str:
                return (
                    f"found {d['unsafe_runs']} unsafe run{'s' if d['unsafe_runs'] != 1 else ''}"
                    if d["decision"] == "rejected"
                    else "found no failure"
                )

            headline.append(
                f"  With the same {c['runs']}-run budget, the conventional sweep {found(c)} for "
                f"'{arm['label']}', while targeted search {found(p)}."
            )

    failure_cases.sort(
        key=lambda f: (
            f["min_moving_separation_m"] if f["min_moving_separation_m"] is not None else -9
        )
    )
    model_calls = [
        {
            "id": m["id"],
            "task": m["task"],
            "model": m["model_served"] or m["model_requested"],
            "status": m["status"],
            "created_at": m["created_at"],
            "result": m["result"],
        }
        for m in store.model_calls(scenario_id=scenario.id, limit=10)
        if m["task"] in ("interpret_incident", "propose_interventions")
    ]
    assumed = [r for r in _param_rows(scenario) if r["provenance"] == "assumed"]
    return {
        "report_schema": REPORT_SCHEMA,
        "generated_at": now(),
        "experiment": {
            "id": exp["id"],
            "title": exp["title"],
            "status": exp["status"],
            "created_at": exp["created_at"],
            "finished_at": exp["finished_at"],
            "runtime_s": exp["runtime_s"],
        },
        "provenance": {
            "spec_hash": plan["spec_hash"],
            "plan_hash": plan["plan_hash"],
            "engine_version": plan["engine_version"],
            "metrics_version": plan["metrics_version"],
            "strategy_version": plan["strategy_version"],
            "criteria_id": plan["criteria_id"],
            "seed": plan["seed"],
            "budget_per_arm": plan["budget_per_arm"],
            "strategies": plan["strategies"],
            "total_runs": plan["total_runs"],
        },
        "scenario": {
            "id": scenario.id,
            "title": scenario.title,
            "data_origin": scenario.data_origin,
            "description": scenario.description,
            "incident": None
            if scenario.incident is None
            else scenario.incident.model_dump(mode="json"),
            "parameters": _param_rows(scenario),
            "calibration": scenario.calibration.model_dump(mode="json"),
        },
        "claim": {"kind": plan["claim"], "text": plan["claim_text"]},
        "criteria": {
            "id": criteria.id,
            "threshold_m": criteria.near_miss_threshold_m,
            "rule": criteria.intervention_rule,
            "rationale": criteria.rationale,
            "outcomes": criteria.run_outcomes,
        },
        "metrics": [d.model_dump() for d in METRIC_DEFINITIONS.values()],
        "stress_envelope": None
        if plan["stress"] is None
        else [
            {
                **a,
                "description": STRESS_AXIS_META[a["name"]][2],
                "kind": STRESS_AXIS_META[a["name"]][1],
            }
            for a in plan["stress"]["axes"]
        ],
        "headline": headline,
        "arms": arms_out,
        "failure_cases": failure_cases[:25],
        "failure_case_count": len(failure_cases),
        "assumptions": assumed,
        "limitations": LIMITATIONS,
        "model_generated_content": model_calls,
        "compiler_findings": exp["compilation"].get("findings", []),
    }


def report_markdown(rep: dict[str, Any]) -> str:
    e, pv, sc = rep["experiment"], rep["provenance"], rep["scenario"]
    lines = [
        f"# NearMiss evidence report — {e['title']}",
        "",
        f"Experiment `{e['id']}` · completed {e['finished_at']} · report generated {rep['generated_at']}",
        "",
        "> Simulation-based decision support. Not a certified safety assessment. "
        f"Scenario data origin: **{sc['data_origin'].replace('_', ' ')}**.",
        "",
        "## Findings",
        "",
        *[f"- {h}" if not h.startswith("  ") else f"  - {h.strip()}" for h in rep["headline"]],
        "",
        "## Claim tested",
        "",
        rep["claim"]["text"],
        "",
        f"Acceptance rule ({rep['criteria']['id']}): {rep['criteria']['rule']} "
        f"Near-miss threshold: {rep['criteria']['threshold_m']} m.",
        "",
        "## Scenario",
        "",
        f"**{sc['title']}** (`{sc['id']}`)",
        "",
        sc["description"],
        "",
        "| Parameter | Value | Unit | Provenance | Source |",
        "| --- | --- | --- | --- | --- |",
        *[
            f"| {p['parameter']} | {p['value']} | {p['unit']} | {p['provenance']} | {p['source']} |"
            for p in sc["parameters"]
        ],
        "",
    ]
    if rep["stress_envelope"]:
        lines += [
            "## Stress envelope",
            "",
            "| Axis | Range | Unit | Applied as |",
            "| --- | --- | --- | --- |",
            *[
                f"| {a['description']} | {a['low']} – {a['high']} | {a['unit']} | {a['kind']} |"
                for a in rep["stress_envelope"]
            ],
            "",
        ]
    lines += ["## Results by arm", ""]
    for arm in rep["arms"]:
        lines += [f"### {arm['label']}", ""]
        if arm["changes"]:
            lines += [f"Changed: {', '.join('`' + c['path'] + '`' for c in arm['changes'])}", ""]
        if arm["nominal"]:
            lines += [
                f"Replayed incident: {arm['nominal']['outcome'].replace('_', ' ')}, minimum clearance "
                f"{_fmt(arm['nominal']['min_moving_separation_m'], 'm')}.",
                "",
            ]
        lines += [
            "| Workflow | Decision | Unsafe runs | Collisions | Worst clearance |",
            "| --- | --- | --- | --- | --- |",
        ]
        for s in arm["strategies"].values():
            lines.append(
                f"| {s['label']} | {s['decision']} | {s['unsafe_runs']} / {s['runs']} | {s['collisions']} | "
                f"{_fmt(s['worst_run']['min_moving_separation_m'], 'm')} |"
            )
        lines += ["", f"Verdict: {arm['verdict_text']}", ""]
    if rep["failure_cases"]:
        lines += [
            "## Failure cases (worst first)",
            "",
            "| Arm | Workflow | Run | Outcome | Clearance | Conditions | Config hash |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for f in rep["failure_cases"][:12]:
            lines.append(
                f"| {f['arm_label']} | {f['strategy']} | {f['index']} | {f['outcome']} | "
                f"{_fmt(f['min_moving_separation_m'], 'm')} | {_describe_point(f['stress_point'])} | "
                f"`{f['config_hash'][:12]}` |"
            )
        lines.append("")
    lines += [
        "## Metric definitions",
        "",
        *[f"- **{m['name']}** [{m['unit']}]: {m['description']}" for m in rep["metrics"]],
        "",
        "## Assumptions",
        "",
        *[
            f"- {a['parameter']} = {a['value']} {a['unit']}: {a['source']}"
            for a in rep["assumptions"]
        ],
        "",
        "## Limitations",
        "",
        *[f"- {x}" for x in rep["limitations"]],
        "",
        "## Model-generated content (not used to decide outcomes)",
        "",
    ]
    if rep["model_generated_content"]:
        lines += [
            f"- {m['task']} · {m['model']} · {m['status']} · {m['created_at']}"
            for m in rep["model_generated_content"]
        ]
    else:
        lines.append("- None for this scenario.")
    lines += [
        "",
        "## Reproducibility",
        "",
        f"- Spec hash `{pv['spec_hash']}`; plan hash `{pv['plan_hash']}`",
        f"- Engine {pv['engine_version']}, metrics {pv['metrics_version']}, strategies "
        f"{pv['strategy_version']}, criteria {pv['criteria_id']}",
        f"- Seed {pv['seed']}, {pv['budget_per_arm']} runs per arm per workflow, {pv['total_runs']} runs in total",
        "- Every run can be replayed from its stored configuration; the replay endpoint "
        "recomputes the trajectory and checks its hash.",
        "",
    ]
    return "\n".join(lines)
