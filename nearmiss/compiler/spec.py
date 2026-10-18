"""Experiment specification (compiler input) and compiled plan (compiler output)."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from nearmiss.domain.models import Intervention, Scenario, StressEnvelope
from nearmiss.domain.transforms import ParameterChange

EXPERIMENT_SCHEMA: Final = "nearmiss.experiment/1"
PLAN_SCHEMA: Final = "nearmiss.plan/1"

Claim = Literal["robust_clearance", "nominal_improvement"]
StrategyName = Literal["targeted_search", "conventional_sweep"]

CLAIM_TEXT: dict[str, str] = {
    "robust_clearance": (
        "The intervention keeps the moving-forklift clearance at or above the criteria "
        "threshold, with no contact, for every condition in the stress envelope."
    ),
    "nominal_improvement": (
        "The intervention increases clearance under the replayed incident conditions. "
        "Says nothing about other conditions."
    ),
}

#: Scenario parameters an arm may override. Any override makes the arm differ from the
#: baseline in more than the intervention, which the compiler reports as confounding.
OVERRIDABLE_PARAMETERS = (
    "forklift.cruise_speed",
    "forklift.reaction_time",
    "forklift.braking_decel",
    "pedestrian.walking_speed",
    "pedestrian.start_time",
)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InterventionArmSpec(_Model):
    intervention: Intervention
    parameter_overrides: dict[str, float] = Field(default_factory=dict)


class ExperimentSpec(_Model):
    schema_version: Literal["nearmiss.experiment/1"] = EXPERIMENT_SCHEMA
    title: str = Field(min_length=1, max_length=200)
    scenario_id: str
    arms: list[InterventionArmSpec] = Field(min_length=1, max_length=6)
    stress: StressEnvelope | None = None
    claim: Claim = "robust_clearance"
    metrics: list[str] = Field(default_factory=lambda: ["min_moving_separation_m", "collision"])
    strategy: StrategyName = "targeted_search"
    compare_conventional: bool = True
    budget_per_arm: int = Field(default=48, ge=1, le=512)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    criteria_id: str = "nearmiss.criteria/1"


Severity = Literal["error", "warning", "info"]


class Finding(_Model):
    code: str
    severity: Severity
    path: str
    message: str
    hint: str = ""
    correctable: bool = False


class PlanArm(_Model):
    arm_id: str
    label: str
    is_baseline: bool
    intervention: Intervention | None
    scenario: Scenario
    scenario_hash: str
    changes: list[ParameterChange]


class ExperimentPlan(_Model):
    schema_version: Literal["nearmiss.plan/1"] = PLAN_SCHEMA
    spec_hash: str
    plan_hash: str
    scenario_id: str
    claim: Claim
    claim_text: str
    criteria_id: str
    engine_version: str
    metrics_version: str
    strategy_version: str
    stress: StressEnvelope | None
    strategies: list[StrategyName]
    budget_per_arm: int
    seed: int
    arms: list[PlanArm]
    total_runs: int


CompileStatus = Literal["accepted", "rejected"]


class CompilationResult(_Model):
    status: CompileStatus
    findings: list[Finding]
    original_spec: dict[str, object]
    corrected_spec: dict[str, object] | None = None
    corrected_spec_status: CompileStatus | None = None
    plan: ExperimentPlan | None = None
