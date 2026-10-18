"""Loads the documented reference scenarios shipped in ``data/scenarios``."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from nearmiss.domain.models import Scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO_ROOT / "data" / "scenarios"


def load_scenario_file(path: Path) -> Scenario:
    with path.open(encoding="utf-8") as fh:
        return Scenario.model_validate(json.load(fh))


@lru_cache(maxsize=1)
def reference_scenarios() -> dict[str, Scenario]:
    scenarios: dict[str, Scenario] = {}
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        sc = load_scenario_file(path)
        if sc.id in scenarios:
            raise ValueError(f"duplicate scenario id {sc.id} in {path.name}")
        scenarios[sc.id] = sc
    return scenarios


def get_reference_scenario(scenario_id: str) -> Scenario:
    try:
        return reference_scenarios()[scenario_id]
    except KeyError as exc:
        raise KeyError(f"unknown reference scenario '{scenario_id}'") from exc
