"""API and persistence integration tests (in-process, temporary SQLite database)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from nearmiss.api.app import create_app
from nearmiss.integrations.nebius import NebiusConfig, TokenFactoryClient

from .test_compiler import FULL_AXES, RELOCATE


def fake_client_factory(content: str | None) -> Any:
    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "nvidia/Nemotron-3_5-Lightning"}]})
        return httpx.Response(
            200,
            json={
                "model": "nvidia/Nemotron-3_5-Lightning",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    def factory() -> TokenFactoryClient:
        if content is None:
            return TokenFactoryClient(NebiusConfig(api_key=None))
        return TokenFactoryClient(NebiusConfig(api_key="k"), transport=httpx.MockTransport(handle))

    return factory


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        db_path=str(tmp_path / "t.sqlite3"), workers=0, client_factory=fake_client_factory(None)
    )
    with TestClient(app) as c:
        yield c


def spec(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "API test",
        "scenario_id": "dc-aisle4-crossing",
        "arms": [{"intervention": RELOCATE}],
        "stress": {"axes": FULL_AXES},
        "budget_per_arm": 16,
        "seed": 4,
    }
    base.update(kw)
    return base


def wait_done(c: TestClient, eid: str, timeout: float = 90) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        exp: dict[str, Any] = c.get(f"/api/v1/experiments/{eid}").json()
        if exp["status"] in ("completed", "failed", "cancelled"):
            return exp
        time.sleep(0.2)
    raise AssertionError("experiment did not finish")


def test_health_and_reference(client: TestClient) -> None:
    h = client.get("/api/v1/health").json()
    assert h["status"] == "ok" and h["database"] is True
    ref = client.get("/api/v1/reference").json()
    assert ref["criteria"]["near_miss_threshold_m"] == 0.5
    assert {m["name"] for m in ref["metrics"]} >= {"min_moving_separation_m", "collision"}


def test_scenarios_and_replay(client: TestClient) -> None:
    items = client.get("/api/v1/scenarios").json()
    assert {s["id"] for s in items} >= {"dc-aisle4-crossing", "mz-stair-foot"}
    assert all(s["data_origin"] == "synthetic_reference" for s in items)
    detail = client.get("/api/v1/scenarios/dc-aisle4-crossing").json()
    assert detail["geometry_findings"] == []
    assert len(detail["default_envelope"]) == 7
    replay = client.get("/api/v1/scenarios/dc-aisle4-crossing/replay").json()
    assert replay["outcome"] == "near_miss"
    t = replay["trajectory"]["t"]
    assert len(t) == len(replay["separation_m"]) == len(replay["trajectory"]["forklift_x"])
    assert abs((t[1] - t[0]) - 0.1) < 1e-9
    assert client.get("/api/v1/scenarios/nope").status_code == 404


def test_simulate_with_intervention_and_validation(client: TestClient) -> None:
    r = client.post(
        "/api/v1/simulate", json={"scenario_id": "dc-aisle4-crossing", "intervention": RELOCATE}
    )
    assert r.status_code == 200 and r.json()["outcome"] == "clear"
    bad = client.post(
        "/api/v1/simulate",
        json={"scenario_id": "dc-aisle4-crossing", "intervention": dict(RELOCATE, offset_m=[0, 3])},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_geometry"
    out = client.post(
        "/api/v1/simulate",
        json={"scenario_id": "dc-aisle4-crossing", "stress_point": {"forklift_speed": 50}},
    )
    assert out.status_code == 422 and out.json()["error"]["code"] == "stress_out_of_limits"
    shape = client.post("/api/v1/simulate", json={"scenario_id": 5})
    assert shape.status_code == 422 and shape.json()["error"]["code"] == "invalid_request"


def test_compile_endpoint_rejects_invalid_spec(client: TestClient) -> None:
    r = client.post("/api/v1/experiments/compile", json=spec(metrics=["min_ttc_s"])).json()
    assert r["status"] == "rejected"
    assert any(f["code"] == "claim_unsupported_by_metrics" for f in r["findings"])


def test_experiment_lifecycle_persistence_and_replay(client: TestClient) -> None:
    created = client.post("/api/v1/experiments", json=spec())
    assert created.status_code == 201
    eid = created.json()["experiment"]["id"]
    assert created.json()["experiment"]["status"] in ("queued", "running")
    exp = wait_done(client, eid)
    assert exp["status"] == "completed", exp.get("error")
    assert exp["progress_done"] == exp["progress_total"] == 2 * 2 * 16
    assert len(exp["strategy_results"]) == 4
    runs = client.get(
        f"/api/v1/experiments/{eid}/runs",
        params={"arm": "move-stack-3m", "strategy": "targeted_search"},
    ).json()
    assert len(runs) == 16
    # Reproducibility: replaying a stored run reproduces its configuration and trajectory hashes.
    worst = min(runs, key=lambda r: r["margin_m"])
    idx = worst["idx"]
    rep = client.get(
        f"/api/v1/experiments/{eid}/runs/move-stack-3m/targeted_search/{idx}/replay"
    ).json()
    assert rep["verification"]["config_hash_match"] is True
    assert rep["verification"]["trajectory_hash_match"] is True
    assert rep["outcome"] == worst["outcome"]
    report = client.get(f"/api/v1/experiments/{eid}/report").json()
    assert report["report_schema"] == "nearmiss.report/1"
    assert report["provenance"]["plan_hash"] == exp["plan_hash"]
    assert report["headline"]
    md = client.get(f"/api/v1/experiments/{eid}/report.md")
    assert md.status_code == 200 and "# NearMiss evidence report" in md.text
    assert client.post(f"/api/v1/experiments/{eid}/cancel").status_code == 409


def test_rejected_experiment_is_persisted_not_queued(client: TestClient) -> None:
    sign = {"type": "signage", "id": "s", "label": "Sign"}
    r = client.post("/api/v1/experiments", json=spec(arms=[{"intervention": sign}]))
    assert r.status_code == 201
    exp = r.json()["experiment"]
    assert exp["status"] == "rejected"
    detail = client.get(f"/api/v1/experiments/{exp['id']}").json()
    codes = [f["code"] for f in detail["compilation"]["findings"]]
    assert "unsupported_intervention_effect" in codes
    assert client.get(f"/api/v1/experiments/{exp['id']}/report").status_code == 409


def test_duplicate_submission_returns_existing(tmp_path: Path) -> None:
    app = create_app(db_path=str(tmp_path / "d.sqlite3"), workers=0, start_jobs=False)
    with TestClient(app) as c:
        first = c.post("/api/v1/experiments", json=spec())
        second = c.post("/api/v1/experiments", json=spec())
        assert first.status_code == 201 and second.status_code == 200
        assert second.json()["duplicate"] is True
        assert second.json()["experiment"]["id"] == first.json()["experiment"]["id"]
        eid = first.json()["experiment"]["id"]
        cancelled = c.post(f"/api/v1/experiments/{eid}/cancel").json()
        assert cancelled["experiment"]["status"] == "cancelled"


def test_model_endpoints_without_key_degrade_gracefully(client: TestClient) -> None:
    r = client.post("/api/v1/scenarios/dc-aisle4-crossing/interpret", json={})
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"
    calls = client.get("/api/v1/scenarios/dc-aisle4-crossing/model-calls").json()
    assert calls[0]["status"] == "error"
    # Deterministic features keep working.
    assert client.get("/api/v1/scenarios/dc-aisle4-crossing/replay").status_code == 200


def test_model_endpoints_persist_validated_output(tmp_path: Path) -> None:
    payload = {
        "candidates": [
            {"type": "remove_obstruction", "obstruction_id": "stack-a", "label": "Remove stack A"},
            {"type": "signage", "label": "Sign"},
        ]
    }
    app = create_app(
        db_path=str(tmp_path / "m.sqlite3"),
        workers=0,
        client_factory=fake_client_factory(json.dumps(payload)),
    )
    with TestClient(app) as c:
        r = c.post("/api/v1/scenarios/dc-aisle4-crossing/candidates").json()
        assert r["status"] == "ok"
        assert [x["status"] for x in r["result"]["candidates"]] == ["valid", "rejected"]
        calls = c.get("/api/v1/scenarios/dc-aisle4-crossing/model-calls").json()
        assert calls[0]["model_served"] == "nvidia/Nemotron-3_5-Lightning"
        # The same fake returns candidate JSON for the interpreter: wrong shape, so rejected.
        bad = c.post("/api/v1/scenarios/dc-aisle4-crossing/interpret", json={}).json()
        assert bad["status"] == "invalid_output"
        v = c.post("/api/v1/integrations/nebius/verify").json()
        assert v["status"] == "ok" and v["model_served"] == "nvidia/Nemotron-3_5-Lightning"


def test_save_user_scenario_validates_geometry(client: TestClient) -> None:
    sc = client.get("/api/v1/scenarios/dc-aisle4-crossing").json()["scenario"]
    sc["id"] = "dc-aisle4-variant"
    sc["data_origin"] = "user_defined"
    ok = client.post("/api/v1/scenarios", json={"scenario": sc, "parent_id": "dc-aisle4-crossing"})
    assert ok.status_code == 201
    sc["obstructions"][0]["geometry"]["center"] = [19.6, 9.0]
    bad = client.post("/api/v1/scenarios", json={"scenario": sc})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_geometry"
    ro = client.post("/api/v1/scenarios", json={"scenario": dict(sc, id="dc-aisle4-crossing")})
    assert ro.status_code == 409
