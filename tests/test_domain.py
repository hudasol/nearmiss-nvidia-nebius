from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from nearmiss.domain.catalog import reference_scenarios
from nearmiss.domain.models import (
    Intervention,
    Param,
    RelocateObstruction,
    RemoveObstruction,
    RerouteCrossing,
    Scenario,
    StressAxis,
    StressEnvelope,
)
from nearmiss.domain.transforms import (
    TransformError,
    apply_intervention,
    apply_stress,
    crossing_route,
    nominal_stress_point,
)

INTERVENTION = TypeAdapter(Intervention)


def test_reference_scenarios_load_and_are_marked_synthetic() -> None:
    scenarios = reference_scenarios()
    assert scenarios
    for sc in scenarios.values():
        assert sc.schema_version == "nearmiss.scenario/1"
        assert sc.data_origin == "synthetic_reference"


def test_params_keep_provenance(reference: Scenario) -> None:
    assert reference.forklift.cruise_speed.provenance == "estimated"
    assert reference.forklift.braking_decel.provenance == "assumed"
    assert reference.forklift.cruise_speed.unit == "m/s"


def test_scenario_rejects_unknown_fields(reference: Scenario) -> None:
    data = reference.model_dump(mode="json")
    data["forklift"]["turbo"] = True
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_scenario_rejects_duplicate_obstruction_ids(reference: Scenario) -> None:
    data = reference.model_dump(mode="json")
    data["obstructions"].append(data["obstructions"][0])
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_param_rejects_negative_uncertainty() -> None:
    with pytest.raises(ValidationError):
        Param(value=1.0, unit="m", provenance="observed", source="x", uncertainty=-1)


def test_intervention_discriminated_union_parses_each_type() -> None:
    reloc = INTERVENTION.validate_python(
        {
            "type": "relocate_obstruction",
            "id": "a",
            "label": "Move stack",
            "obstruction_id": "stack-a",
            "offset_m": [-3, 0],
        }
    )
    assert isinstance(reloc, RelocateObstruction)
    proc = INTERVENTION.validate_python({"type": "signage", "id": "b", "label": "Sign"})
    assert proc.type == "signage"
    with pytest.raises(ValidationError):
        INTERVENTION.validate_python({"type": "teleport", "id": "c", "label": "x"})


def test_relocation_changes_only_the_target_obstruction(reference: Scenario) -> None:
    iv = RelocateObstruction(id="r", label="r", obstruction_id="stack-a", offset_m=(-3.0, 0.0))
    out, changes = apply_intervention(reference, iv)
    before = reference.obstruction("stack-a")
    after = out.obstruction("stack-a")
    assert before is not None and after is not None
    assert after.geometry.center == (before.geometry.center[0] - 3.0, before.geometry.center[1])
    assert out.obstruction("column-c7") == reference.obstruction("column-c7")
    assert out.forklift == reference.forklift
    assert out.pedestrian == reference.pedestrian
    assert [c.path for c in changes] == ["obstructions[stack-a].geometry.center"]


def test_removal_and_unknown_target(reference: Scenario) -> None:
    out, changes = apply_intervention(
        reference, RemoveObstruction(id="r", label="r", obstruction_id="stack-a")
    )
    assert out.obstruction("stack-a") is None
    assert len(changes) == 1
    with pytest.raises(TransformError):
        apply_intervention(reference, RemoveObstruction(id="r", label="r", obstruction_id="nope"))


def test_reroute_keeps_origin_and_destination(reference: Scenario) -> None:
    route = crossing_route(reference, 30.0)
    assert route[0] == reference.pedestrian.route[0]
    assert route[-1] == reference.pedestrian.route[-1]
    assert (30.0, 3.0) in route and (30.0, 14.0) in route
    out, changes = apply_intervention(
        reference, RerouteCrossing(id="x", label="x", crossing_x_m=30.0)
    )
    assert out.pedestrian.route == route
    assert changes[0].path == "pedestrian.route"


def test_stress_absolute_and_offset_axes(reference: Scenario) -> None:
    out = apply_stress(
        reference,
        {
            "forklift_speed": 3.0,
            "pedestrian_start_offset": 1.5,
            "obstruction_shift_x": 0.5,
        },
    )
    assert out.forklift.cruise_speed.value == 3.0
    assert out.forklift.cruise_speed.provenance == "assumed"
    assert out.pedestrian.start_time.value == pytest.approx(
        reference.pedestrian.start_time.value + 1.5
    )
    stack = out.obstruction("stack-a")
    column = out.obstruction("column-c7")
    assert stack is not None and column is not None
    assert stack.geometry.center[0] == pytest.approx(19.6 + 0.5)
    # Fixed structure is never shifted by placement stress.
    assert column == reference.obstruction("column-c7")


def test_nominal_stress_point_reproduces_scenario(reference: Scenario) -> None:
    axes = ["forklift_speed", "reaction_time", "pedestrian_start_offset"]
    point = nominal_stress_point(reference, axes)
    out = apply_stress(reference, point)
    assert out.forklift.cruise_speed.value == reference.forklift.cruise_speed.value
    assert out.pedestrian.start_time.value == reference.pedestrian.start_time.value


def test_stress_rejects_unknown_axis(reference: Scenario) -> None:
    with pytest.raises(TransformError):
        apply_stress(reference, {"gravity": 1.0})


def test_stress_envelope_validation() -> None:
    with pytest.raises(ValidationError):
        StressAxis(name="forklift_speed", low=3.0, high=2.0, unit="m/s")
    axis = StressAxis(name="forklift_speed", low=2.0, high=3.0, unit="m/s")
    with pytest.raises(ValidationError):
        StressEnvelope(axes=[axis, axis])


def test_reference_scenarios_pass_compiler_geometry_checks() -> None:
    from nearmiss.compiler.compiler import geometry_findings

    for sc in reference_scenarios().values():
        assert geometry_findings(sc, sc.id) == [], sc.id
