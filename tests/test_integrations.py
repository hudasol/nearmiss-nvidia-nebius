"""Model integration tests against a mock transport, plus one opt-in live test."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from nearmiss.domain.models import Scenario
from nearmiss.integrations import nebius
from nearmiss.integrations.model_tasks import (
    RawInterpretation,
    interpret_incident,
    propose_interventions,
    validate_candidates,
    validate_interpretation,
)
from nearmiss.integrations.nebius import (
    ModelAuthError,
    ModelNotConfigured,
    ModelTimeout,
    ModelUnavailable,
    NebiusConfig,
    TokenFactoryClient,
    strip_reasoning,
)

MODELS = {"data": [{"id": "nvidia/Nemotron-3_5-Lightning"}, {"id": "openai/gpt-oss-120b"}]}


def completion(content: str, finish: str = "stop") -> dict[str, Any]:
    return {
        "model": "nvidia/Nemotron-3_5-Lightning",
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 400, "completion_tokens": 120},
    }


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], retries: int = 2
) -> TokenFactoryClient:
    cfg = NebiusConfig(api_key="test-key", max_retries=retries, timeout_s=5)
    return TokenFactoryClient(cfg, transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nebius.time, "sleep", lambda _s: None)


def router(
    chat: Callable[[dict[str, Any]], httpx.Response],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer test-key"
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        return chat(json.loads(req.content))

    return handle


def test_missing_key_is_reported_not_crashed() -> None:
    client = TokenFactoryClient(NebiusConfig(api_key=None))
    with pytest.raises(ModelNotConfigured):
        client.list_models()


def test_model_resolution_prefers_configured_nvidia_model() -> None:
    client = make_client(router(lambda b: httpx.Response(500)))
    assert client.resolve_model() == "nvidia/Nemotron-3_5-Lightning"


def test_model_resolution_falls_back_and_refuses_non_nvidia() -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "openai/gpt-oss-120b"}]})

    with pytest.raises(ModelUnavailable):
        make_client(handle).resolve_model()


def test_request_body_disables_thinking_and_requests_json() -> None:
    seen: dict[str, Any] = {}

    def chat(body: dict[str, Any]) -> httpx.Response:
        seen.update(body)
        return httpx.Response(200, json=completion('{"ok": true}'))

    res = make_client(router(chat)).chat_json("sys", "user")
    assert seen["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["model"] == "nvidia/Nemotron-3_5-Lightning"
    assert res.prompt_tokens == 400 and res.model_served == "nvidia/Nemotron-3_5-Lightning"


def test_retries_transient_errors_then_succeeds() -> None:
    calls = {"n": 0}

    def chat(body: dict[str, Any]) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"detail": "rate limited"})
        return httpx.Response(200, json=completion("{}"))

    res = make_client(router(chat)).chat_json("s", "u")
    assert res.attempts == 3


def test_auth_error_is_not_retried() -> None:
    calls = {"n": 0}

    def handle(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"detail": "bad key"})

    with pytest.raises(ModelAuthError):
        make_client(handle).list_models()
    assert calls["n"] == 1


def test_timeout_after_retries() -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ModelTimeout):
        make_client(handle, retries=1).list_models()


def test_strip_reasoning() -> None:
    assert strip_reasoning('<think>hmm</think>\n{"a":1}') == '{"a":1}'
    assert strip_reasoning('partial thoughts</think>{"a":1}') == '{"a":1}'


NARRATIVE = (
    "Forklift FL-07 was travelling at roughly normal aisle speed. The driver said they "
    "stopped with the forks roughly a third of a metre away. Telematics recorded 2.6 m/s."
)


def test_interpretation_validation_rules() -> None:
    raw = RawInterpretation.model_validate(
        {
            "summary": "Blind corner near miss.",
            "parameters": [
                {
                    "name": "forklift.cruise_speed",
                    "value": 2.6,
                    "unit": "m/s",
                    "provenance": "observed",
                    "evidence": "Telematics recorded 2.6 m/s.",
                },
                {
                    "name": "incident.closest_approach",
                    "value": 0.33,
                    "unit": "m",
                    "provenance": "observed",
                    "evidence": "roughly a third of a metre away",
                },
                {
                    "name": "pedestrian.walking_speed",
                    "value": 1.4,
                    "unit": "m/s",
                    "provenance": "estimated",
                    "evidence": "the picker was strolling",
                },
                {
                    "name": "forklift.braking_decel",
                    "value": 12.0,
                    "unit": "m/s²",
                    "provenance": "assumed",
                },
                {
                    "name": "forklift.reaction_time",
                    "value": 1.0,
                    "unit": "s",
                    "provenance": "assumed",
                    "rationale": "typical",
                },
                {"name": "forklift.colour", "value": 1.0, "provenance": "assumed"},
            ],
        }
    )
    out = {p.name: p for p in validate_interpretation(raw, NARRATIVE).parameters}
    assert out["forklift.cruise_speed"].status == "accepted"
    # Number not literally stated in the quote: downgraded to an estimate.
    assert out["incident.closest_approach"].status == "downgraded"
    assert out["incident.closest_approach"].provenance == "estimated"
    # Fabricated quote rejected.
    assert out["pedestrian.walking_speed"].status == "rejected"
    # Physically implausible value rejected.
    assert out["forklift.braking_decel"].status == "rejected"
    assert out["forklift.reaction_time"].status == "accepted"
    assert out["forklift.colour"].status == "rejected"


def test_interpret_incident_end_to_end_with_mock() -> None:
    payload = {
        "summary": "Pedestrian emerged from behind a pallet stack.",
        "contributing_factors": ["sightline blocked by staged pallets"],
        "parameters": [
            {
                "name": "forklift.cruise_speed",
                "value": 2.6,
                "unit": "m/s",
                "provenance": "observed",
                "evidence": "Telematics recorded 2.6 m/s.",
            }
        ],
        "open_questions": ["Was the stack there every day?"],
    }
    client = make_client(
        router(lambda b: httpx.Response(200, json=completion(json.dumps(payload))))
    )
    outcome, interp = interpret_incident(client, NARRATIVE)
    assert outcome.status == "ok" and interp is not None
    assert outcome.model_served == "nvidia/Nemotron-3_5-Lightning"
    assert interp.parameters[0].status == "accepted"


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        '```json\n{"summary": "x"}\n```',
        '["a list"]',
        '{"summary": 5, "parameters": "x"}',
    ],
)
def test_malformed_model_output_is_rejected(content: str) -> None:
    client = make_client(router(lambda b: httpx.Response(200, json=completion(content))))
    outcome, interp = interpret_incident(client, NARRATIVE)
    assert outcome.status == "invalid_output"
    assert interp is None
    assert outcome.raw_output == content.strip()


def test_truncated_output_is_rejected() -> None:
    client = make_client(
        router(lambda b: httpx.Response(200, json=completion('{"summ', finish="length")))
    )
    outcome, _ = interpret_incident(client, NARRATIVE)
    assert outcome.status == "invalid_output" and outcome.error_kind == "truncated"


def test_candidate_validation(reference: Scenario) -> None:
    checks = validate_candidates(
        reference,
        {
            "candidates": [
                {
                    "type": "relocate_obstruction",
                    "obstruction_id": "stack-a",
                    "offset_m": [-4, 0],
                    "label": "Move stack west",
                    "rationale": "Open the sightline.",
                },
                {
                    "type": "relocate_obstruction",
                    "obstruction_id": "column-c7",
                    "offset_m": [1, 0],
                    "label": "x",
                },
                {
                    "type": "relocate_obstruction",
                    "obstruction_id": "stack-a",
                    "offset_m": [0, 3],
                    "label": "into aisle",
                },
                {"type": "reroute_crossing", "crossing_x_m": 30, "label": "Cross at 30"},
                {"type": "reroute_crossing", "crossing_x_m": 99, "label": "Cross at 99"},
                {"type": "speed_limit", "value": 1.5},
                {"type": "remove_obstruction", "obstruction_id": "ghost"},
            ]
        },
    )
    status = [c.status for c in checks]
    assert status == ["valid", "rejected", "rejected", "valid", "rejected", "rejected", "rejected"]
    assert checks[0].intervention is not None and checks[0].intervention["origin"] == "model"
    assert any("fixed structure" in r for r in checks[1].reasons)


def test_propose_interventions_with_mock(reference: Scenario) -> None:
    payload = {
        "candidates": [
            {"type": "remove_obstruction", "obstruction_id": "stack-a", "label": "Remove stack A"}
        ]
    }
    client = make_client(
        router(lambda b: httpx.Response(200, json=completion(json.dumps(payload))))
    )
    outcome, checks = propose_interventions(client, reference, {"min_moving_separation_m": 0.29})
    assert outcome.status == "ok" and checks is not None and checks[0].status == "valid"


@pytest.mark.live
def test_live_token_factory_call(reference: Scenario) -> None:
    """Real runtime call to Nebius Token Factory. Run with: pytest -m live"""
    if not os.environ.get("NEBIUS_API_KEY"):
        pytest.skip("NEBIUS_API_KEY not set")
    client = TokenFactoryClient(NebiusConfig.from_env())
    model = client.resolve_model()
    assert model.lower().startswith("nvidia/")
    assert reference.incident is not None
    outcome, _ = interpret_incident(client, reference.incident.narrative)
    print(outcome.model_dump_json(indent=1))
    assert outcome.status in ("ok", "invalid_output")
    assert outcome.model_served
