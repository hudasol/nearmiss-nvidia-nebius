from __future__ import annotations

from typing import Any

import pytest

from nearmiss.compiler.compiler import compile_experiment, scenario_diff
from nearmiss.domain.catalog import get_reference_scenario, reference_scenarios
from nearmiss.domain.models import Scenario

SCENARIO_ID = "dc-aisle4-crossing"

FULL_AXES = [
    {"name": "forklift_speed", "low": 1.8, "high": 3.3, "unit": "m/s"},
    {"name": "pedestrian_speed", "low": 0.9, "high": 1.8, "unit": "m/s"},
    {"name": "pedestrian_start_offset", "low": -6.0, "high": 6.0, "unit": "s"},
    {"name": "reaction_time", "low": 0.75, "high": 1.5, "unit": "s"},
    {"name": "braking_decel", "low": 1.5, "high": 2.5, "unit": "m/s²"},
    {"name": "obstruction_shift_x", "low": -1.0, "high": 1.0, "unit": "m"},
]

RELOCATE = {
    "type": "relocate_obstruction",
    "id": "move-stack-3m",
    "label": "Move stack A 3 m west",
    "obstruction_id": "stack-a",
    "offset_m": [-3.0, 0.0],
}
REROUTE = {
    "type": "reroute_crossing",
    "id": "cross-x30",
    "label": "Cross at x 30",
    "crossing_x_m": 30.0,
}


def lookup(scenario_id: str) -> Scenario | None:
    return reference_scenarios().get(scenario_id)


def spec(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "nearmiss.experiment/1",
        "title": "Stack relocation vs reroute",
        "scenario_id": SCENARIO_ID,
        "arms": [{"intervention": RELOCATE}, {"intervention": REROUTE}],
        "stress": {"axes": FULL_AXES},
        "claim": "robust_clearance",
        "metrics": ["min_moving_separation_m", "collision"],
        "budget_per_arm": 32,
        "seed": 3,
    }
    base.update(overrides)
    return base


def codes(result: Any) -> set[str]:
    return {f.code for f in result.findings if f.severity == "error"}


def test_valid_spec_compiles_to_plan() -> None:
    r = compile_experiment(spec(), lookup)
    assert r.status == "accepted", r.findings
    plan = r.plan
    assert plan is not None
    assert [a.arm_id for a in plan.arms] == ["baseline", "move-stack-3m", "cross-x30"]
    assert plan.arms[0].is_baseline and plan.arms[0].changes == []
    assert [c.path for c in plan.arms[1].changes] == ["obstructions[stack-a].geometry.center"]
    assert [c.path for c in plan.arms[2].changes] == ["pedestrian.route"]
    assert plan.strategies == ["targeted_search", "conventional_sweep"]
    assert plan.total_runs == 3 * 2 * 32
    # Same spec → same plan hash (reproducible record).
    again = compile_experiment(spec(), lookup)
    assert again.plan is not None and again.plan.plan_hash == plan.plan_hash


def test_schema_version_and_shape_errors_are_structured() -> None:
    r = compile_experiment(spec(schema_version="nearmiss.experiment/0"), lookup)
    assert r.status == "rejected"
    assert "schema_version_unsupported" in codes(r)
    r = compile_experiment(spec(arms=[]), lookup)
    assert "schema_invalid" in codes(r)


def test_unknown_scenario() -> None:
    r = compile_experiment(spec(scenario_id="nowhere"), lookup)
    assert codes(r) == {"unknown_scenario"}


def test_procedural_measure_rejected_with_corrected_spec() -> None:
    sign = {"type": "signage", "id": "sign", "label": "Add a 'look left' sign"}
    r = compile_experiment(spec(arms=[{"intervention": RELOCATE}, {"intervention": sign}]), lookup)
    assert r.status == "rejected"
    assert "unsupported_intervention_effect" in codes(r)
    assert r.corrected_spec is not None
    assert [a["intervention"]["id"] for a in r.corrected_spec["arms"]] == ["move-stack-3m"]  # type: ignore[index]
    assert r.corrected_spec_status == "accepted"


def test_confounded_override_is_rejected() -> None:
    arm = {"intervention": RELOCATE, "parameter_overrides": {"forklift.cruise_speed": 1.8}}
    r = compile_experiment(spec(arms=[arm]), lookup)
    assert "confounded_override" in codes(r)
    assert r.corrected_spec is not None
    assert r.corrected_spec["arms"][0]["parameter_overrides"] == {}  # type: ignore[index]
    assert r.corrected_spec_status == "accepted"


def test_unknown_override_parameter() -> None:
    arm = {"intervention": RELOCATE, "parameter_overrides": {"gravity": 9.0}}
    r = compile_experiment(spec(arms=[arm]), lookup)
    assert "unknown_override" in codes(r)


def test_missing_required_stress_axis() -> None:
    axes = [a for a in FULL_AXES if a["name"] != "pedestrian_start_offset"]
    r = compile_experiment(spec(stress={"axes": axes}), lookup)
    assert "missing_stress_axis" in codes(r)
    assert r.corrected_spec is not None
    names = [a["name"] for a in r.corrected_spec["stress"]["axes"]]  # type: ignore[index]
    assert "pedestrian_start_offset" in names
    assert r.corrected_spec_status == "accepted"


def test_relocation_requires_placement_variation() -> None:
    axes = [a for a in FULL_AXES if a["name"] != "obstruction_shift_x"]
    r = compile_experiment(spec(arms=[{"intervention": RELOCATE}], stress={"axes": axes}), lookup)
    assert "missing_stress_axis" in codes(r)


def test_missing_envelope_for_robust_claim() -> None:
    r = compile_experiment(spec(stress=None), lookup)
    assert "missing_stress_envelope" in codes(r)


def test_nominal_claim_needs_no_envelope() -> None:
    r = compile_experiment(spec(stress=None, claim="nominal_improvement"), lookup)
    assert r.status == "accepted"
    assert r.plan is not None and r.plan.total_runs == 3


def test_relocation_onto_forklift_route_is_rejected() -> None:
    onto_aisle = dict(RELOCATE, id="into-aisle", offset_m=[0.0, 3.0])
    r = compile_experiment(spec(arms=[{"intervention": onto_aisle}]), lookup)
    assert "route_intersects_obstruction" in codes(r)
    assert r.corrected_spec is None


def test_unknown_obstruction() -> None:
    bad = dict(RELOCATE, obstruction_id="stack-z")
    r = compile_experiment(spec(arms=[{"intervention": bad}]), lookup)
    assert "unknown_obstruction" in codes(r)


def test_no_op_intervention() -> None:
    noop = dict(RELOCATE, id="noop", offset_m=[0.0, 0.0])
    r = compile_experiment(spec(arms=[{"intervention": noop}]), lookup)
    assert "intervention_no_effect" in codes(r)


def test_reroute_outside_walkable_range() -> None:
    far = dict(REROUTE, id="far", crossing_x_m=43.0)
    r = compile_experiment(spec(arms=[{"intervention": far}]), lookup)
    assert "crossing_out_of_range" in codes(r)


def test_metrics_that_cannot_support_the_claim() -> None:
    r = compile_experiment(spec(metrics=["min_ttc_s", "pet_s"]), lookup)
    assert "claim_unsupported_by_metrics" in codes(r)
    r = compile_experiment(spec(metrics=["min_moving_separation_m", "collision", "vibes"]), lookup)
    assert "unknown_metric" in codes(r)


def test_timing_window_unreachable_for_rerouted_crossing() -> None:
    narrow = [
        {"name": "forklift_speed", "low": 2.5, "high": 2.7, "unit": "m/s"},
        {"name": "pedestrian_speed", "low": 1.2, "high": 1.4, "unit": "m/s"},
        {"name": "pedestrian_start_offset", "low": -1.0, "high": 1.0, "unit": "s"},
        {"name": "reaction_time", "low": 0.9, "high": 1.1, "unit": "s"},
    ]
    far = dict(REROUTE, id="cross-x36", crossing_x_m=36.0)
    r = compile_experiment(spec(arms=[{"intervention": far}], stress={"axes": narrow}), lookup)
    assert "timing_unreachable" in codes(r)
    msg = next(f for f in r.findings if f.code == "timing_unreachable")
    assert msg.path == "arms[0]"
    assert r.corrected_spec is not None and r.corrected_spec_status == "accepted"
    offset = next(
        a
        for a in r.corrected_spec["stress"]["axes"]
        if a["name"] == "pedestrian_start_offset"  # type: ignore[index]
    )
    assert offset["low"] < -1.0


def test_axis_outside_physical_limits_and_wrong_unit() -> None:
    axes = [dict(a) for a in FULL_AXES]
    axes[0] = {"name": "forklift_speed", "low": 1.8, "high": 9.0, "unit": "km/h"}
    r = compile_experiment(spec(stress={"axes": axes}), lookup)
    assert {"stress_axis_out_of_limits", "stress_axis_unit"} <= codes(r)
    assert r.corrected_spec is not None
    fs = r.corrected_spec["stress"]["axes"][0]  # type: ignore[index]
    assert fs["high"] == 6.0 and fs["unit"] == "m/s"


def test_envelope_excluding_incident_is_a_warning() -> None:
    axes = [dict(a) for a in FULL_AXES]
    axes[0] = {"name": "forklift_speed", "low": 1.0, "high": 2.0, "unit": "m/s"}
    r = compile_experiment(spec(stress={"axes": axes}), lookup)
    assert r.status == "accepted"
    assert any(f.code == "envelope_excludes_nominal" for f in r.findings)


def test_criteria_cannot_be_swapped() -> None:
    r = compile_experiment(spec(criteria_id="lenient/1"), lookup)
    assert "criteria_mismatch" in codes(r)


def test_budget_minimum() -> None:
    r = compile_experiment(spec(budget_per_arm=4), lookup)
    assert "budget_too_small" in codes(r)


def test_invalid_scenario_geometry_rejected() -> None:
    sc = get_reference_scenario(SCENARIO_ID)
    data = sc.model_dump(mode="json")
    data["obstructions"][0]["geometry"]["center"] = [19.6, 8.5]  # on the forklift route
    broken = Scenario.model_validate(data)

    def broken_lookup(_id: str) -> Scenario | None:
        return broken

    r = compile_experiment(spec(arms=[{"intervention": REROUTE}]), broken_lookup)
    assert "route_intersects_obstruction" in codes(r)


@pytest.mark.parametrize("x", [12.0, 27.0, 30.0])
def test_scenario_diff_detects_only_route_change_for_reroute(x: float) -> None:
    from nearmiss.domain.models import RerouteCrossing
    from nearmiss.domain.transforms import apply_intervention

    sc = get_reference_scenario(SCENARIO_ID)
    out, _ = apply_intervention(sc, RerouteCrossing(id="r", label="r", crossing_x_m=x))
    assert [c.path for c in scenario_diff(sc, out)] == ["pedestrian.route"]
