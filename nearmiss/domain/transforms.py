"""Pure scenario transformations: applying interventions and stress conditions.

Each transformation returns a new ``Scenario`` plus a list of ``ParameterChange`` records
so the compiler can verify that only the intended variables changed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from nearmiss.domain.models import (
    STRESS_AXIS_META,
    Intervention,
    Point,
    ProceduralMeasure,
    RelocateObstruction,
    RemoveObstruction,
    RerouteCrossing,
    Scenario,
    StressPoint,
)


class ParameterChange(BaseModel):
    path: str
    before: Any
    after: Any


class TransformError(ValueError):
    """Raised when a transformation cannot be applied to the given scenario."""


def crossing_route(scenario: Scenario, crossing_x: float) -> list[Point]:
    """Pedestrian route that keeps the original origin and destination but crosses the
    forklift aisle at ``crossing_x`` using the site's crossing template."""
    tpl = scenario.crossing_template
    if tpl is None:
        raise TransformError(f"scenario {scenario.id} has no crossing template")
    start = scenario.pedestrian.route[0]
    end = scenario.pedestrian.route[-1]
    raw: list[Point] = [
        start,
        (start[0], tpl.approach_y_m),
        (crossing_x, tpl.approach_y_m),
        (crossing_x, tpl.exit_y_m),
        (end[0], tpl.exit_y_m),
        end,
    ]
    route: list[Point] = []
    for p in raw:
        if not route or abs(p[0] - route[-1][0]) > 1e-9 or abs(p[1] - route[-1][1]) > 1e-9:
            route.append((round(p[0], 6), round(p[1], 6)))
    # Drop interior points that are collinear with their neighbours.
    simplified: list[Point] = [route[0]]
    for i in range(1, len(route) - 1):
        a, b, c = simplified[-1], route[i], route[i + 1]
        if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) > 1e-9:
            simplified.append(b)
    simplified.append(route[-1])
    return simplified


def apply_intervention(
    scenario: Scenario, intervention: Intervention
) -> tuple[Scenario, list[ParameterChange]]:
    """Return the modified scenario and the parameter changes the intervention made."""
    if isinstance(intervention, RelocateObstruction):
        obs = scenario.obstruction(intervention.obstruction_id)
        if obs is None:
            raise TransformError(f"unknown obstruction '{intervention.obstruction_id}'")
        dx, dy = intervention.offset_m
        new_center = (
            round(obs.geometry.center[0] + dx, 6),
            round(obs.geometry.center[1] + dy, 6),
        )
        new_obs = obs.model_copy(
            update={"geometry": obs.geometry.model_copy(update={"center": new_center})}
        )
        obstructions = [new_obs if o.id == obs.id else o for o in scenario.obstructions]
        changes = [
            ParameterChange(
                path=f"obstructions[{obs.id}].geometry.center",
                before=list(obs.geometry.center),
                after=list(new_center),
            )
        ]
        return scenario.model_copy(update={"obstructions": obstructions}), changes

    if isinstance(intervention, RemoveObstruction):
        obs = scenario.obstruction(intervention.obstruction_id)
        if obs is None:
            raise TransformError(f"unknown obstruction '{intervention.obstruction_id}'")
        obstructions = [o for o in scenario.obstructions if o.id != obs.id]
        changes = [ParameterChange(path=f"obstructions[{obs.id}]", before="present", after=None)]
        return scenario.model_copy(update={"obstructions": obstructions}), changes

    if isinstance(intervention, RerouteCrossing):
        new_route = crossing_route(scenario, intervention.crossing_x_m)
        ped = scenario.pedestrian.model_copy(update={"route": new_route})
        changes = [
            ParameterChange(
                path="pedestrian.route",
                before=[list(p) for p in scenario.pedestrian.route],
                after=[list(p) for p in new_route],
            )
        ]
        return scenario.model_copy(update={"pedestrian": ped}), changes

    if isinstance(intervention, ProceduralMeasure):
        # No modelled physical effect: the scenario is unchanged.
        return scenario, []

    raise TransformError(f"unsupported intervention type: {type(intervention).__name__}")


def nominal_stress_point(scenario: Scenario, axes: list[str]) -> StressPoint:
    """The stress point that reproduces the scenario exactly."""
    values: dict[str, float] = {
        "forklift_speed": scenario.forklift.cruise_speed.value,
        "pedestrian_speed": scenario.pedestrian.walking_speed.value,
        "pedestrian_start_offset": 0.0,
        "reaction_time": scenario.forklift.reaction_time.value,
        "braking_decel": scenario.forklift.braking_decel.value,
        "obstruction_shift_x": 0.0,
        "obstruction_shift_y": 0.0,
    }
    return {a: values[a] for a in axes}


def apply_stress(scenario: Scenario, point: StressPoint) -> Scenario:
    """Apply a stress point. Absolute axes replace a value; offset axes shift it.

    Stress values are tagged ``assumed`` with source ``stress condition`` so that a
    stressed scenario can never be mistaken for the observed incident.
    """
    unknown = set(point) - set(STRESS_AXIS_META)
    if unknown:
        raise TransformError(f"unknown stress axes: {sorted(unknown)}")
    fk = scenario.forklift
    ped = scenario.pedestrian
    src = "stress condition"
    fk_update: dict[str, Any] = {}
    ped_update: dict[str, Any] = {}
    if "forklift_speed" in point:
        fk_update["cruise_speed"] = fk.cruise_speed.with_value(
            point["forklift_speed"], "assumed", src
        )
    if "reaction_time" in point:
        fk_update["reaction_time"] = fk.reaction_time.with_value(
            point["reaction_time"], "assumed", src
        )
    if "braking_decel" in point:
        fk_update["braking_decel"] = fk.braking_decel.with_value(
            point["braking_decel"], "assumed", src
        )
    if "pedestrian_speed" in point:
        ped_update["walking_speed"] = ped.walking_speed.with_value(
            point["pedestrian_speed"], "assumed", src
        )
    if "pedestrian_start_offset" in point:
        ped_update["start_time"] = ped.start_time.with_value(
            ped.start_time.value + point["pedestrian_start_offset"], "assumed", src
        )
    obstructions = scenario.obstructions
    sx = point.get("obstruction_shift_x", 0.0)
    sy = point.get("obstruction_shift_y", 0.0)
    if sx or sy:
        obstructions = [
            o.model_copy(
                update={
                    "geometry": o.geometry.model_copy(
                        update={"center": (o.geometry.center[0] + sx, o.geometry.center[1] + sy)}
                    )
                }
            )
            if o.movable
            else o
            for o in obstructions
        ]
    return scenario.model_copy(
        update={
            "forklift": fk.model_copy(update=fk_update),
            "pedestrian": ped.model_copy(update=ped_update),
            "obstructions": obstructions,
        }
    )
