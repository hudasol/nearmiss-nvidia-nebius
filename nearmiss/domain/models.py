"""Versioned domain schemas shared by the simulator, compiler, evaluator, API and UI.

Every physical quantity that came from somewhere other than a fixed design constant is a
``Param`` carrying its unit and provenance, so observations, estimates and assumptions
are never silently merged into "exact" values.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nearmiss.domain import geometry as g

SCENARIO_SCHEMA: Final = "nearmiss.scenario/1"
INTERVENTION_SCHEMA: Final = "nearmiss.intervention/1"
STRESS_SCHEMA: Final = "nearmiss.stress/1"

Point = tuple[float, float]
ProvenanceKind = Literal["observed", "estimated", "assumed"]
DataOrigin = Literal["synthetic_reference", "user_defined", "model_assisted"]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Param(Frozen):
    """A physical quantity with unit and provenance."""

    value: float
    unit: str
    provenance: ProvenanceKind
    source: str = Field(min_length=1, description="Where the value came from")
    uncertainty: float | None = Field(
        default=None, ge=0, description="Plus/minus range in the same unit, when known"
    )

    def with_value(self, value: float, provenance: ProvenanceKind, source: str) -> Param:
        return Param(
            value=value,
            unit=self.unit,
            provenance=provenance,
            source=source,
            uncertainty=None,
        )


class Rect(Frozen):
    """Axis-aligned rectangle before rotation; ``length`` lies along local x."""

    center: Point
    length: float = Field(gt=0)
    width: float = Field(gt=0)
    rotation_deg: float = 0.0

    def polygon(self) -> g.Polygon:
        import math

        return g.oriented_rectangle(
            self.center, self.length, self.width, math.radians(self.rotation_deg)
        )


class Obstruction(Frozen):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    label: str
    kind: Literal["pallet_stack", "rack_end", "wall", "equipment"]
    geometry: Rect
    height_m: float = Field(gt=0)
    opaque: bool = True
    movable: bool = Field(
        default=False,
        description="Movable obstructions are shifted by placement stress conditions",
    )
    provenance: ProvenanceKind
    source: str


class LayoutZone(Frozen):
    """Painted or structural zone drawn on the plan. Visual reference only; not physics."""

    id: str
    label: str
    kind: Literal["aisle", "walkway", "crossing", "rack", "dock"]
    polygon: list[Point] = Field(min_length=3)


class ForkliftSpec(Frozen):
    id: str
    label: str
    length_m: float = Field(gt=0.5, le=8, description="Overall length including forks")
    width_m: float = Field(gt=0.5, le=3)
    eye_offset_m: Point = Field(description="Driver eye position in the vehicle frame")
    route: list[Point] = Field(min_length=2)
    cruise_speed: Param
    braking_decel: Param
    max_accel: Param
    reaction_time: Param
    detection_range_m: float = Field(gt=0, le=100)
    field_of_view_deg: float = Field(gt=0, le=360)
    hazard_lateral_margin_m: float = Field(ge=0, le=5)
    prediction_horizon_s: float = Field(gt=0, le=10)
    resume_clear_time_s: float = Field(default=1.0, ge=0)


class PedestrianSpec(Frozen):
    id: str
    label: str
    radius_m: float = Field(gt=0.1, le=1.0)
    route: list[Point] = Field(min_length=2)
    walking_speed: Param
    start_time: Param
    behaviour: Literal["non_yielding"] = Field(
        default="non_yielding",
        description="Pedestrian keeps walking; models the distracted-pedestrian incident type",
    )


class ReferenceMeasurement(Frozen):
    label: str
    value_m: float
    provenance: ProvenanceKind
    source: str


class Calibration(Frozen):
    frame: str = "Site plan frame: x east, y north, metres, origin at south-west corner"
    scale_source: str
    reference_measurements: list[ReferenceMeasurement] = Field(default_factory=list)


class Bounds(Frozen):
    width_m: float = Field(gt=0, le=500)
    height_m: float = Field(gt=0, le=500)

    def contains(self, p: Point) -> bool:
        return 0.0 <= p[0] <= self.width_m and 0.0 <= p[1] <= self.height_m


class SimulationSettings(Frozen):
    dt_s: float = Field(default=0.02, ge=0.002, le=0.1)
    duration_s: float = Field(default=20.0, gt=1, le=120)


class CrossingTemplate(Frozen):
    """How a re-routed crossing is drawn for this site: walk along the approach line,
    cross the aisle at the chosen x, then return along the exit line."""

    approach_y_m: float
    exit_y_m: float
    min_crossing_x_m: float
    max_crossing_x_m: float


class IncidentRecord(Frozen):
    id: str
    occurred_at: str
    location: str
    summary: str
    narrative: str
    reported_closest_approach_m: Param | None = None


class Scenario(Frozen):
    schema_version: Literal["nearmiss.scenario/1"] = SCENARIO_SCHEMA
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    title: str
    description: str
    data_origin: DataOrigin
    bounds: Bounds
    calibration: Calibration
    layout: list[LayoutZone] = Field(default_factory=list)
    obstructions: list[Obstruction] = Field(default_factory=list)
    forklift: ForkliftSpec
    pedestrian: PedestrianSpec
    simulation: SimulationSettings = SimulationSettings()
    crossing_template: CrossingTemplate | None = None
    incident: IncidentRecord | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> Scenario:
        ids = [o.id for o in self.obstructions]
        if len(ids) != len(set(ids)):
            raise ValueError("obstruction ids must be unique")
        return self

    def obstruction(self, obstruction_id: str) -> Obstruction | None:
        return next((o for o in self.obstructions if o.id == obstruction_id), None)


# ---------------------------------------------------------------------------
# Interventions
# ---------------------------------------------------------------------------

InterventionOrigin = Literal["reference", "user", "model"]


class _InterventionBase(Frozen):
    schema_version: Literal["nearmiss.intervention/1"] = INTERVENTION_SCHEMA
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    label: str = Field(min_length=1, max_length=120)
    rationale: str = Field(default="", max_length=2000)
    origin: InterventionOrigin = "user"


class RelocateObstruction(_InterventionBase):
    type: Literal["relocate_obstruction"] = "relocate_obstruction"
    obstruction_id: str
    offset_m: Point = Field(description="Translation (dx, dy) applied to the obstruction")


class RemoveObstruction(_InterventionBase):
    type: Literal["remove_obstruction"] = "remove_obstruction"
    obstruction_id: str


class RerouteCrossing(_InterventionBase):
    type: Literal["reroute_crossing"] = "reroute_crossing"
    crossing_x_m: float = Field(description="New x position where pedestrians cross the aisle")


class ProceduralMeasure(_InterventionBase):
    """Measures such as floor markings or signage. Accepted by the schema so they can be
    explained, but the compiler rejects them: the simulator has no validated model of
    their effect on behaviour."""

    type: Literal["floor_marking", "signage", "training"]
    description: str = ""


Intervention = Annotated[
    RelocateObstruction | RemoveObstruction | RerouteCrossing | ProceduralMeasure,
    Field(discriminator="type"),
]

PHYSICAL_INTERVENTION_TYPES = ("relocate_obstruction", "remove_obstruction", "reroute_crossing")


# ---------------------------------------------------------------------------
# Stress conditions
# ---------------------------------------------------------------------------

StressAxisName = Literal[
    "forklift_speed",
    "pedestrian_speed",
    "pedestrian_start_offset",
    "reaction_time",
    "braking_decel",
    "obstruction_shift_x",
    "obstruction_shift_y",
]

#: Units and whether each axis is an absolute value or an offset from the scenario value.
STRESS_AXIS_META: dict[str, tuple[str, Literal["absolute", "offset"], str]] = {
    "forklift_speed": ("m/s", "absolute", "Forklift travel speed"),
    "pedestrian_speed": ("m/s", "absolute", "Pedestrian walking speed"),
    "pedestrian_start_offset": ("s", "offset", "Pedestrian arrival timing shift"),
    "reaction_time": ("s", "absolute", "Driver perception-reaction time"),
    "braking_decel": ("m/s²", "absolute", "Forklift braking deceleration"),
    "obstruction_shift_x": ("m", "offset", "Movable obstruction placement shift, x"),
    "obstruction_shift_y": ("m", "offset", "Movable obstruction placement shift, y"),
}

#: Physically plausible hard limits for each axis; the compiler rejects ranges outside them.
STRESS_AXIS_LIMITS: dict[str, tuple[float, float]] = {
    "forklift_speed": (0.3, 6.0),
    "pedestrian_speed": (0.3, 3.0),
    "pedestrian_start_offset": (-15.0, 15.0),
    "reaction_time": (0.3, 3.0),
    "braking_decel": (0.5, 5.0),
    "obstruction_shift_x": (-5.0, 5.0),
    "obstruction_shift_y": (-5.0, 5.0),
}


class StressAxis(Frozen):
    name: StressAxisName
    low: float
    high: float
    unit: str

    @model_validator(mode="after")
    def _ordered(self) -> StressAxis:
        if self.high < self.low:
            raise ValueError(f"stress axis {self.name}: high < low")
        return self


class StressEnvelope(Frozen):
    schema_version: Literal["nearmiss.stress/1"] = STRESS_SCHEMA
    axes: list[StressAxis] = Field(min_length=1)

    @field_validator("axes")
    @classmethod
    def _unique(cls, axes: list[StressAxis]) -> list[StressAxis]:
        names = [a.name for a in axes]
        if len(names) != len(set(names)):
            raise ValueError("stress axes must be unique")
        return axes

    def axis(self, name: str) -> StressAxis | None:
        return next((a for a in self.axes if a.name == name), None)


StressPoint = dict[str, float]
