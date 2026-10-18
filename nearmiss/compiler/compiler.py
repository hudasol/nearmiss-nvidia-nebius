"""Safety Change Compiler: validates an experiment specification and compiles it into an
executable, reproducible plan — or rejects it with structured, actionable findings.

The compiler is deterministic domain code. It never calls a model and never runs the
simulator; physical checks use geometry and closed-form kinematics only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from nearmiss.compiler.spec import (
    CLAIM_TEXT,
    OVERRIDABLE_PARAMETERS,
    CompilationResult,
    ExperimentPlan,
    ExperimentSpec,
    Finding,
    InterventionArmSpec,
    PlanArm,
    StrategyName,
)
from nearmiss.domain import geometry as gm
from nearmiss.domain.models import (
    STRESS_AXIS_LIMITS,
    STRESS_AXIS_META,
    Intervention,
    ProceduralMeasure,
    RelocateObstruction,
    RemoveObstruction,
    RerouteCrossing,
    Scenario,
    StressAxis,
    StressEnvelope,
)
from nearmiss.domain.transforms import ParameterChange, TransformError, apply_intervention
from nearmiss.evaluation.criteria import load_criteria
from nearmiss.evaluation.metrics import METRIC_DEFINITIONS, METRICS_VERSION
from nearmiss.evaluation.strategies import LEAD_AFTER_S, STRATEGY_VERSION
from nearmiss.simulation.engine import ENGINE_VERSION, canonical_json, scenario_hash, sha256_hex

ScenarioLookup = Callable[[str], Scenario | None]

#: Axes a robustness claim must vary: they govern arrival timing and stopping distance.
REQUIRED_AXES_ROBUST = (
    "forklift_speed",
    "pedestrian_speed",
    "pedestrian_start_offset",
    "reaction_time",
)
RECOMMENDED_AXES = ("braking_decel",)
MIN_BUDGET = {"targeted_search": 16, "conventional_sweep": 16}


def default_axis(name: str, scenario: Scenario) -> StressAxis:
    """Site-agnostic default range for an axis, centred on the scenario's value."""
    unit = STRESS_AXIS_META[name][0]
    fk = scenario.forklift
    ranges: dict[str, tuple[float, float]] = {
        "forklift_speed": (fk.cruise_speed.value - 0.8, fk.cruise_speed.value + 0.7),
        "pedestrian_speed": (0.9, 1.8),
        "pedestrian_start_offset": (-6.0, 6.0),
        "reaction_time": (0.75, 1.5),
        "braking_decel": (fk.braking_decel.value - 0.5, fk.braking_decel.value + 0.5),
        "obstruction_shift_x": (-1.0, 1.0),
        "obstruction_shift_y": (-0.5, 0.5),
    }
    lo_lim, hi_lim = STRESS_AXIS_LIMITS[name]
    lo, hi = ranges[name]
    return StressAxis(
        name=name, low=round(max(lo_lim, lo), 3), high=round(min(hi_lim, hi), 3), unit=unit
    )


# ---------------------------------------------------------------------------
# Independent scenario diff
# ---------------------------------------------------------------------------


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) and "id" in x for x in obj):
        for x in obj:
            out.update(_flatten(x, f"{prefix}[{x['id']}]"))
    else:
        out[prefix] = obj
    return out


def scenario_diff(before: Scenario, after: Scenario) -> list[ParameterChange]:
    """Every leaf that differs between two scenarios (lists of objects keyed by id)."""
    a = _flatten(before.model_dump(mode="json"))
    b = _flatten(after.model_dump(mode="json"))
    changes: list[ParameterChange] = []
    for path in sorted(set(a) | set(b)):
        if a.get(path, None) != b.get(path, None) or (path in a) != (path in b):
            changes.append(ParameterChange(path=path, before=a.get(path), after=b.get(path)))
    return changes


def _allowed_prefixes(iv: Intervention) -> tuple[str, ...]:
    if isinstance(iv, RelocateObstruction):
        return (f"obstructions[{iv.obstruction_id}].geometry.center",)
    if isinstance(iv, RemoveObstruction):
        return (f"obstructions[{iv.obstruction_id}]",)
    if isinstance(iv, RerouteCrossing):
        return ("pedestrian.route",)
    return ()


# ---------------------------------------------------------------------------
# Geometry checks on a resolved scenario
# ---------------------------------------------------------------------------


def geometry_findings(sc: Scenario, path: str) -> list[Finding]:
    out: list[Finding] = []
    fk, ped = sc.forklift, sc.pedestrian
    for label, route in (("forklift.route", fk.route), ("pedestrian.route", ped.route)):
        for i, p in enumerate(route):
            if not sc.bounds.contains(p):
                out.append(
                    Finding(
                        code="geometry_out_of_bounds",
                        severity="error",
                        path=f"{path}.{label}[{i}]",
                        message=f"Route point {p} lies outside the {sc.bounds.width_m} × "
                        f"{sc.bounds.height_m} m scenario bounds.",
                        hint="Move the point inside the calibrated plan extract.",
                    )
                )
    for o in sc.obstructions:
        poly = o.geometry.polygon()
        if not all(sc.bounds.contains(v) for v in poly):
            out.append(
                Finding(
                    code="geometry_out_of_bounds",
                    severity="error",
                    path=f"{path}.obstructions[{o.id}]",
                    message=f"Obstruction '{o.label}' extends outside the scenario bounds.",
                    hint="Keep obstructions inside the plan extract.",
                )
            )
        for label, route, clearance in (
            ("forklift", fk.route, fk.width_m / 2.0),
            ("pedestrian", ped.route, ped.radius_m),
        ):
            for i in range(len(route) - 1):
                d = gm.segment_polygon_distance(route[i], route[i + 1], poly)
                if d < clearance - 1e-9:
                    out.append(
                        Finding(
                            code="route_intersects_obstruction",
                            severity="error",
                            path=f"{path}.obstructions[{o.id}]",
                            message=f"The {label} route segment {i} passes {d:.2f} m from "
                            f"'{o.label}'; the {label} needs {clearance:.2f} m clearance.",
                            hint="An actor cannot pass through a solid obstruction. Move the "
                            "obstruction or the route.",
                        )
                    )
                    break
    for i in range(len(sc.obstructions)):
        for j in range(i + 1, len(sc.obstructions)):
            a, b = sc.obstructions[i], sc.obstructions[j]
            if gm.polygons_intersect(a.geometry.polygon(), b.geometry.polygon()):
                out.append(
                    Finding(
                        code="obstructions_overlap",
                        severity="error",
                        path=f"{path}.obstructions[{a.id}]",
                        message=f"'{a.label}' overlaps '{b.label}'.",
                        hint="Two solid objects cannot occupy the same floor area.",
                    )
                )
    if gm.Polyline(fk.route).first_intersection(gm.Polyline(ped.route)) is None:
        out.append(
            Finding(
                code="routes_do_not_cross",
                severity="error",
                path=f"{path}.pedestrian.route",
                message="The pedestrian route never crosses the forklift route, so the "
                "experiment has no crossing conflict to evaluate.",
                hint="Check the route geometry; NearMiss evaluates crossing conflicts.",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Kinematic reachability of the conflict window
# ---------------------------------------------------------------------------


def required_offset_interval(sc: Scenario, envelope: StressEnvelope) -> tuple[float, float] | None:
    """Start-offset interval in which the pedestrian reaches the lane during the truck's
    stopping time, for some combination of speed extremes in the envelope."""
    hit = gm.Polyline(sc.forklift.route).first_intersection(gm.Polyline(sc.pedestrian.route))
    if hit is None:
        return None
    _, s_fk, s_ped = hit
    fk, ped = sc.forklift, sc.pedestrian

    def values(name: str, nominal: float) -> list[float]:
        ax = envelope.axis(name)
        return [ax.low, ax.high] if ax else [nominal]

    lo, hi = float("inf"), float("-inf")
    for v in values("forklift_speed", fk.cruise_speed.value):
        for vp in values("pedestrian_speed", ped.walking_speed.value):
            for tr in values("reaction_time", fk.reaction_time.value):
                for a in values("braking_decel", fk.braking_decel.value):
                    t_front = max(0.0, s_fk - fk.length_m / 2.0 - ped.radius_m) / v
                    lane = fk.width_m / 2.0 + ped.radius_m
                    t_lane = ped.start_time.value + max(0.0, s_ped - lane) / vp
                    t_stop = tr + v / a
                    # lead = t_front - (t_lane + offset) must lie in [-LEAD_AFTER_S, t_stop]
                    lo = min(lo, t_front - t_stop - t_lane)
                    hi = max(hi, t_front + LEAD_AFTER_S - t_lane)
    return lo, hi


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def _schema_findings(err: ValidationError) -> list[Finding]:
    out = []
    for e in err.errors():
        loc = ".".join(str(x) for x in e["loc"])
        code = "schema_version_unsupported" if loc.endswith("schema_version") else "schema_invalid"
        out.append(
            Finding(
                code=code,
                severity="error",
                path=loc or "$",
                message=e["msg"],
                hint="Fix the field to match the nearmiss.experiment/1 schema.",
            )
        )
    return out


def compile_experiment(raw: dict[str, Any], lookup: ScenarioLookup) -> CompilationResult:
    original = dict(raw)
    try:
        spec = ExperimentSpec.model_validate(raw)
    except ValidationError as err:
        return CompilationResult(
            status="rejected", findings=_schema_findings(err), original_spec=original
        )

    findings, corrected = _validate(spec, lookup)
    errors = [f for f in findings if f.severity == "error"]
    if not errors:
        scenario = lookup(spec.scenario_id)
        assert scenario is not None
        plan = build_plan(spec, scenario)
        return CompilationResult(
            status="accepted", findings=findings, original_spec=original, plan=plan
        )

    corrected_dict: dict[str, object] | None = None
    corrected_status = None
    if corrected is not None and all(f.correctable for f in errors):
        corrected_dict = corrected.model_dump(mode="json")
        re_findings, _ = _validate(corrected, lookup)
        corrected_status = (
            "rejected" if any(f.severity == "error" for f in re_findings) else "accepted"
        )
    return CompilationResult(
        status="rejected",
        findings=findings,
        original_spec=original,
        corrected_spec=corrected_dict,
        corrected_spec_status=corrected_status,
    )


def _validate(
    spec: ExperimentSpec, lookup: ScenarioLookup
) -> tuple[list[Finding], ExperimentSpec | None]:
    f: list[Finding] = []
    scenario = lookup(spec.scenario_id)
    if scenario is None:
        f.append(
            Finding(
                code="unknown_scenario",
                severity="error",
                path="scenario_id",
                message=f"No scenario with id '{spec.scenario_id}'.",
                hint="Choose a reference scenario or save the scenario first.",
            )
        )
        return f, None

    criteria = load_criteria()
    if spec.criteria_id != criteria.id:
        f.append(
            Finding(
                code="criteria_mismatch",
                severity="error",
                path="criteria_id",
                message=f"Experiments must use the pre-declared criteria '{criteria.id}'; "
                f"got '{spec.criteria_id}'. Acceptance criteria cannot be edited per experiment.",
                hint=f"Set criteria_id to '{criteria.id}'.",
                correctable=True,
            )
        )

    f += [
        Finding(
            code=x.code,
            severity=x.severity,
            path=f"scenario.{x.path}",
            message=x.message,
            hint=x.hint,
        )
        for x in geometry_findings(scenario, "scenario")
        if x.severity == "error"
    ]

    # --- arms -------------------------------------------------------------------
    kept_arms: list[InterventionArmSpec] = []
    seen_ids: set[str] = set()
    for i, arm in enumerate(spec.arms):
        iv = arm.intervention
        p = f"arms[{i}]"
        arm_ok = True
        if iv.id in seen_ids:
            f.append(
                Finding(
                    code="duplicate_intervention_id",
                    severity="error",
                    path=f"{p}.intervention.id",
                    message=f"Intervention id '{iv.id}' is used twice.",
                    hint="Give each intervention a unique id.",
                )
            )
        seen_ids.add(iv.id)

        if isinstance(iv, ProceduralMeasure):
            f.append(
                Finding(
                    code="unsupported_intervention_effect",
                    severity="error",
                    path=f"{p}.intervention.type",
                    message=f"'{iv.type}' changes behaviour, not physical geometry. The simulator "
                    "has no validated model of how it changes driver or pedestrian behaviour, so "
                    "any simulated benefit would be an assumption, not a result.",
                    hint="Evaluate it with a site trial. NearMiss can test relocating or removing "
                    "obstructions and rerouting the crossing.",
                    correctable=len(spec.arms) > 1,
                )
            )
            continue

        unknown = [k for k in arm.parameter_overrides if k not in OVERRIDABLE_PARAMETERS]
        for k in unknown:
            f.append(
                Finding(
                    code="unknown_override",
                    severity="error",
                    path=f"{p}.parameter_overrides.{k}",
                    message=f"'{k}' is not an overridable scenario parameter.",
                    hint=f"Overridable parameters: {', '.join(OVERRIDABLE_PARAMETERS)}.",
                    correctable=True,
                )
            )
        if arm.parameter_overrides and not unknown:
            f.append(
                Finding(
                    code="confounded_override",
                    severity="error",
                    path=f"{p}.parameter_overrides",
                    message="This arm changes "
                    + ", ".join(sorted(arm.parameter_overrides))
                    + " as well as the intervention. Any difference from the baseline could come "
                    "from either change, so the result cannot be attributed to the intervention.",
                    hint="Test one change per arm. To evaluate a different operating condition, "
                    "add it to the stress envelope so baseline and intervention see it equally.",
                    correctable=True,
                )
            )

        try:
            resolved, declared = apply_intervention(scenario, iv)
        except TransformError as exc:
            code = (
                "unknown_obstruction"
                if "obstruction" in str(exc)
                else "intervention_not_applicable"
            )
            f.append(
                Finding(
                    code=code,
                    severity="error",
                    path=f"{p}.intervention",
                    message=str(exc),
                    hint="Reference an obstruction id that exists in the scenario.",
                )
            )
            continue

        if isinstance(iv, RerouteCrossing) and scenario.crossing_template is not None:
            tpl = scenario.crossing_template
            if not tpl.min_crossing_x_m <= iv.crossing_x_m <= tpl.max_crossing_x_m:
                f.append(
                    Finding(
                        code="crossing_out_of_range",
                        severity="error",
                        path=f"{p}.intervention.crossing_x_m",
                        message=f"Crossing x = {iv.crossing_x_m} m is outside the site's feasible "
                        f"crossing range {tpl.min_crossing_x_m}–{tpl.max_crossing_x_m} m.",
                        hint="Choose a crossing position inside the walkable range.",
                    )
                )
                arm_ok = False

        actual = scenario_diff(scenario, resolved)
        if not actual:
            f.append(
                Finding(
                    code="intervention_no_effect",
                    severity="error",
                    path=f"{p}.intervention",
                    message="The intervention leaves the scenario unchanged, so it cannot be tested.",
                    hint="Use a non-zero offset or a different crossing position.",
                )
            )
            arm_ok = False
        allowed = _allowed_prefixes(iv)
        stray = [c for c in actual if not any(c.path.startswith(a) for a in allowed)]
        for c in stray:
            f.append(
                Finding(
                    code="unintended_change",
                    severity="error",
                    path=f"{p}.intervention",
                    message=f"Applying the intervention also changed '{c.path}', which it does not "
                    "declare as its target.",
                    hint="An intervention must change only its declared target.",
                )
            )
        del declared

        geo = geometry_findings(resolved, f"{p}.resolved")
        f += geo
        if geo:
            arm_ok = False
        if arm_ok:
            kept_arms.append(InterventionArmSpec(intervention=iv))

    # --- stress envelope --------------------------------------------------------
    corrected_axes: list[StressAxis] = []
    env = spec.stress
    if spec.claim == "robust_clearance":
        if env is None:
            f.append(
                Finding(
                    code="missing_stress_envelope",
                    severity="error",
                    path="stress",
                    message="A robustness claim needs a stress envelope; a single replay cannot "
                    "show that clearance holds under other conditions.",
                    hint="Add a stress envelope, or change the claim to 'nominal_improvement'.",
                    correctable=True,
                )
            )
        present = {a.name for a in env.axes} if env else set()
        for name in REQUIRED_AXES_ROBUST:
            if name not in present and env is not None:
                f.append(
                    Finding(
                        code="missing_stress_axis",
                        severity="error",
                        path="stress.axes",
                        message=f"'{name}' ({STRESS_AXIS_META[name][2].lower()}) must vary for a "
                        "robustness claim: it governs arrival timing or stopping distance.",
                        hint=f"Add '{name}' with a range that reflects site operations.",
                        correctable=True,
                    )
                )
        for name in RECOMMENDED_AXES:
            if name not in present and env is not None:
                f.append(
                    Finding(
                        code="recommended_stress_axis",
                        severity="warning",
                        path="stress.axes",
                        message=f"'{name}' is not varied; results assume the scenario's value "
                        "throughout.",
                        hint=f"Consider adding '{name}'.",
                    )
                )
        has_movable = any(o.movable for o in scenario.obstructions)
        relocates = any(isinstance(a.intervention, RelocateObstruction) for a in spec.arms)
        if relocates and has_movable and "obstruction_shift_x" not in present and env is not None:
            f.append(
                Finding(
                    code="missing_stress_axis",
                    severity="error",
                    path="stress.axes",
                    message="Relocating a movable obstruction is only robust if it holds when "
                    "the obstruction is placed slightly differently, but 'obstruction_shift_x' "
                    "is not varied.",
                    hint="Add 'obstruction_shift_x' (for example ±1 m).",
                    correctable=True,
                )
            )
        corrected_axes = list(env.axes) if env else []
        for name in REQUIRED_AXES_ROBUST:
            if name not in {a.name for a in corrected_axes}:
                corrected_axes.append(default_axis(name, scenario))
        if (
            relocates
            and has_movable
            and "obstruction_shift_x" not in {a.name for a in corrected_axes}
        ):
            corrected_axes.append(default_axis("obstruction_shift_x", scenario))

    if env is not None:
        nominal = {
            "forklift_speed": scenario.forklift.cruise_speed.value,
            "pedestrian_speed": scenario.pedestrian.walking_speed.value,
            "pedestrian_start_offset": 0.0,
            "reaction_time": scenario.forklift.reaction_time.value,
            "braking_decel": scenario.forklift.braking_decel.value,
            "obstruction_shift_x": 0.0,
            "obstruction_shift_y": 0.0,
        }
        for i, a in enumerate(env.axes):
            lo_lim, hi_lim = STRESS_AXIS_LIMITS[a.name]
            unit = STRESS_AXIS_META[a.name][0]
            if a.unit != unit:
                f.append(
                    Finding(
                        code="stress_axis_unit",
                        severity="error",
                        path=f"stress.axes[{i}].unit",
                        message=f"'{a.name}' is measured in {unit}, not '{a.unit}'.",
                        hint=f"Use {unit}.",
                        correctable=True,
                    )
                )
            if a.low < lo_lim or a.high > hi_lim:
                f.append(
                    Finding(
                        code="stress_axis_out_of_limits",
                        severity="error",
                        path=f"stress.axes[{i}]",
                        message=f"'{a.name}' range {a.low}–{a.high} {unit} exceeds the physically "
                        f"plausible limits {lo_lim}–{hi_lim} {unit}.",
                        hint="Results outside these limits would extrapolate the model beyond "
                        "its stated validity.",
                        correctable=True,
                    )
                )
            if a.high == a.low:
                f.append(
                    Finding(
                        code="stress_axis_degenerate",
                        severity="warning",
                        path=f"stress.axes[{i}]",
                        message=f"'{a.name}' has zero range, so it is fixed rather than varied.",
                    )
                )
            if not a.low <= nominal[a.name] <= a.high:
                f.append(
                    Finding(
                        code="envelope_excludes_nominal",
                        severity="warning",
                        path=f"stress.axes[{i}]",
                        message=f"'{a.name}' range {a.low}–{a.high} {unit} excludes the replayed "
                        f"incident value {nominal[a.name]:.2f} {unit}.",
                        hint="The envelope should normally include the conditions of the incident.",
                    )
                )
            if a.name.startswith("obstruction_shift") and not any(
                o.movable for o in scenario.obstructions
            ):
                f.append(
                    Finding(
                        code="stress_axis_no_effect",
                        severity="warning",
                        path=f"stress.axes[{i}]",
                        message=f"'{a.name}' has no effect: the scenario has no movable obstructions.",
                    )
                )
        corrected_axes = [
            a.model_copy(
                update={
                    "low": max(STRESS_AXIS_LIMITS[a.name][0], a.low),
                    "high": min(STRESS_AXIS_LIMITS[a.name][1], a.high),
                    "unit": STRESS_AXIS_META[a.name][0],
                }
            )
            for a in corrected_axes
        ]

        # Kinematic reachability of the conflict window for every arm.
        offset_axis = env.axis("pedestrian_start_offset")
        if offset_axis is not None and spec.claim == "robust_clearance":
            arm_scenarios: list[tuple[str, Scenario]] = [("baseline", scenario)]
            for i, arm in enumerate(spec.arms):
                if isinstance(arm.intervention, ProceduralMeasure):
                    continue
                try:
                    arm_scenarios.append(
                        (f"arms[{i}]", apply_intervention(scenario, arm.intervention)[0])
                    )
                except TransformError:
                    continue
            for path, sc in arm_scenarios:
                req = required_offset_interval(sc, env)
                if req is None:
                    continue
                lo, hi = req
                if hi < offset_axis.low or lo > offset_axis.high:
                    f.append(
                        Finding(
                            code="timing_unreachable",
                            severity="error",
                            path=f"{path}",
                            message=f"Within this envelope the pedestrian can never reach the "
                            f"forklift lane while the truck is inside its stopping distance at "
                            f"this crossing: that needs a start offset in {lo:.1f}…{hi:.1f} s, "
                            f"but pedestrian_start_offset only spans {offset_axis.low:.1f}…"
                            f"{offset_axis.high:.1f} s. A clean result would reflect the timing "
                            "window, not the intervention.",
                            hint="Widen pedestrian_start_offset to include the conflict window.",
                            correctable=True,
                        )
                    )
                    corrected_axes = [
                        a.model_copy(
                            update={
                                "low": max(STRESS_AXIS_LIMITS[a.name][0], min(a.low, lo)),
                                "high": min(STRESS_AXIS_LIMITS[a.name][1], max(a.high, hi)),
                            }
                        )
                        if a.name == "pedestrian_start_offset"
                        else a
                        for a in corrected_axes
                    ]

    # --- metrics and claim ------------------------------------------------------
    corrected_metrics = list(spec.metrics)
    for m in spec.metrics:
        if m not in METRIC_DEFINITIONS:
            f.append(
                Finding(
                    code="unknown_metric",
                    severity="error",
                    path="metrics",
                    message=f"Unknown metric '{m}'.",
                    hint=f"Available: {', '.join(METRIC_DEFINITIONS)}.",
                    correctable=True,
                )
            )
            corrected_metrics.remove(m)
    needed = (
        ["min_moving_separation_m", "collision"]
        if spec.claim == "robust_clearance"
        else ["min_moving_separation_m"]
    )
    missing = [m for m in needed if m not in spec.metrics]
    if missing:
        offered = [m for m in spec.metrics if m in METRIC_DEFINITIONS]
        f.append(
            Finding(
                code="claim_unsupported_by_metrics",
                severity="error",
                path="metrics",
                message=f"The claim '{spec.claim}' is about clearance and contact, but the "
                f"experiment measures only {', '.join(offered) or 'nothing'}. "
                + (
                    "Time-to-collision ignores braking and post-encroachment time measures "
                    "timing gaps, so neither can show that clearance was maintained."
                    if offered
                    else ""
                ),
                hint=f"Include {', '.join(missing)}.",
                correctable=True,
            )
        )
        corrected_metrics += missing

    if spec.claim == "robust_clearance" and spec.budget_per_arm < MIN_BUDGET[spec.strategy]:
        f.append(
            Finding(
                code="budget_too_small",
                severity="error",
                path="budget_per_arm",
                message=f"{spec.budget_per_arm} runs per arm cannot cover the nominal replay, the "
                f"kinematic corners and the timing window; the minimum is "
                f"{MIN_BUDGET[spec.strategy]}.",
                hint=f"Use at least {MIN_BUDGET[spec.strategy]} runs per arm.",
                correctable=True,
            )
        )

    # --- corrected specification ------------------------------------------------
    corrected: ExperimentSpec | None = None
    if kept_arms:
        update: dict[str, Any] = {
            "arms": kept_arms,
            "metrics": corrected_metrics,
            "criteria_id": criteria.id,
            "budget_per_arm": max(
                spec.budget_per_arm,
                MIN_BUDGET[spec.strategy] if spec.claim == "robust_clearance" else 1,
            ),
        }
        if spec.claim == "robust_clearance" and corrected_axes:
            update["stress"] = StressEnvelope(axes=corrected_axes)
        corrected = spec.model_copy(update=update)
    return f, corrected


def build_plan(spec: ExperimentSpec, scenario: Scenario) -> ExperimentPlan:
    arms = [
        PlanArm(
            arm_id="baseline",
            label="As-is (no intervention)",
            is_baseline=True,
            intervention=None,
            scenario=scenario,
            scenario_hash=scenario_hash(scenario),
            changes=[],
        )
    ]
    for arm in spec.arms:
        resolved, _ = apply_intervention(scenario, arm.intervention)
        arms.append(
            PlanArm(
                arm_id=arm.intervention.id,
                label=arm.intervention.label,
                is_baseline=False,
                intervention=arm.intervention,
                scenario=resolved,
                scenario_hash=scenario_hash(resolved),
                changes=scenario_diff(scenario, resolved),
            )
        )
    strategies: list[StrategyName] = [spec.strategy]
    if spec.compare_conventional and spec.strategy != "conventional_sweep":
        strategies.append("conventional_sweep")
    budget = spec.budget_per_arm if spec.claim == "robust_clearance" else 1
    spec_hash = sha256_hex(canonical_json(spec.model_dump(mode="json")))
    body = {
        "spec_hash": spec_hash,
        "arms": [a.scenario_hash for a in arms],
        "strategies": strategies,
        "budget": budget,
        "seed": spec.seed,
        "engine": ENGINE_VERSION,
        "metrics": METRICS_VERSION,
        "strategy_version": STRATEGY_VERSION,
    }
    return ExperimentPlan(
        spec_hash=spec_hash,
        plan_hash=sha256_hex(canonical_json(body)),
        scenario_id=scenario.id,
        claim=spec.claim,
        claim_text=CLAIM_TEXT[spec.claim],
        criteria_id=spec.criteria_id,
        engine_version=ENGINE_VERSION,
        metrics_version=METRICS_VERSION,
        strategy_version=STRATEGY_VERSION,
        stress=spec.stress if spec.claim == "robust_clearance" else None,
        strategies=strategies if spec.claim == "robust_clearance" else ["targeted_search"],
        budget_per_arm=budget,
        seed=spec.seed,
        arms=arms,
        total_runs=len(arms)
        * (len(strategies) if spec.claim == "robust_clearance" else 1)
        * budget,
    )
