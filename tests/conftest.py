from __future__ import annotations

import pytest

from nearmiss.domain.catalog import get_reference_scenario
from nearmiss.domain.models import Scenario

REFERENCE_ID = "dc-aisle4-crossing"


@pytest.fixture
def reference() -> Scenario:
    return get_reference_scenario(REFERENCE_ID)
