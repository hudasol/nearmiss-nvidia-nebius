"""Versioned, pre-declared evaluation criteria and run classification.

The criteria live in ``data/criteria`` and are loaded read-only. Neither the model
integration nor the stress strategies can change them.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nearmiss.domain.catalog import REPO_ROOT
from nearmiss.evaluation.metrics import RunMetrics

CRITERIA_PATH = REPO_ROOT / "data" / "criteria" / "evaluation-criteria.v1.json"

RunOutcome = Literal["collision", "near_miss", "clear"]
Decision = Literal["accepted", "rejected"]


class EvaluationCriteria(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    title: str
    decision_metric: Literal["min_moving_separation_m"]
    near_miss_threshold_m: float = Field(gt=0)
    run_outcomes: dict[str, str]
    intervention_rule: str
    rationale: str


@lru_cache(maxsize=1)
def load_criteria() -> EvaluationCriteria:
    with CRITERIA_PATH.open(encoding="utf-8") as fh:
        return EvaluationCriteria.model_validate(json.load(fh))


def classify_run(metrics: RunMetrics, criteria: EvaluationCriteria) -> RunOutcome:
    if metrics.collision:
        return "collision"
    sep = metrics.min_moving_separation_m
    if sep is not None and sep < criteria.near_miss_threshold_m:
        return "near_miss"
    return "clear"


def decide(outcomes: list[RunOutcome]) -> Decision:
    """Reject if any executed run is unsafe."""
    return "rejected" if any(o != "clear" for o in outcomes) else "accepted"
