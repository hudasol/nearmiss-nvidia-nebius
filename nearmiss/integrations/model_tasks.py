"""Bounded Nemotron tasks with strict output validation.

1. ``interpret_incident`` — turn a confirmed incident narrative into *proposed* scenario
   parameters, each tagged observed / estimated / assumed and tied to a verbatim quote.
2. ``propose_interventions`` — propose intervention specifications restricted to the types
   the simulator supports.

Model output is untrusted. It is parsed as JSON, validated against Pydantic schemas and
domain rules, and anything that fails is reported as rejected, never repaired into a
success. The model never sees criteria, labels or results of its own proposals, and never
decides whether an intervention works.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from nearmiss.compiler.compiler import geometry_findings
from nearmiss.domain.models import (
    PHYSICAL_INTERVENTION_TYPES,
    Intervention,
    RerouteCrossing,
    Scenario,
)
from nearmiss.domain.transforms import TransformError, apply_intervention
from nearmiss.integrations.nebius import ChatResult, ModelBadOutput, TokenFactoryClient
from nearmiss.simulation.engine import canonical_json, sha256_hex

INTERVENTION = TypeAdapter(Intervention)

#: Parameters the interpreter may propose, with units and plausible ranges.
EXTRACTABLE: dict[str, tuple[str, float, float]] = {
    "forklift.cruise_speed": ("m/s", 0.3, 6.0),
    "forklift.reaction_time": ("s", 0.3, 3.0),
    "forklift.braking_decel": ("m/s²", 0.5, 5.0),
    "pedestrian.walking_speed": ("m/s", 0.3, 3.0),
    "incident.closest_approach": ("m", 0.0, 10.0),
}


# ---------------------------------------------------------------------------
# Incident interpretation
# ---------------------------------------------------------------------------


class RawParameter(BaseModel):
    name: str
    value: float | None = None
    unit: str = ""
    provenance: Literal["observed", "estimated", "assumed"]
    evidence: str = ""
    rationale: str = ""


class RawInterpretation(BaseModel):
    summary: str = Field(max_length=1200)
    contributing_factors: list[str] = Field(default_factory=list, max_length=10)
    parameters: list[RawParameter] = Field(default_factory=list, max_length=12)
    open_questions: list[str] = Field(default_factory=list, max_length=10)


class ProposedParameter(BaseModel):
    name: str
    value: float | None
    unit: str
    provenance: Literal["observed", "estimated", "assumed"]
    evidence: str
    rationale: str
    status: Literal["accepted", "downgraded", "rejected"]
    validation_note: str = ""


class IncidentInterpretation(BaseModel):
    summary: str
    contributing_factors: list[str]
    parameters: list[ProposedParameter]
    open_questions: list[str]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _numbers(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]


def validate_interpretation(raw: RawInterpretation, narrative: str) -> IncidentInterpretation:
    narr = _norm(narrative)
    out: list[ProposedParameter] = []
    for p in raw.parameters:
        note = ""
        status: Literal["accepted", "downgraded", "rejected"] = "accepted"
        prov = p.provenance
        spec = EXTRACTABLE.get(p.name)
        if spec is None:
            status, note = "rejected", f"'{p.name}' is not a parameter NearMiss can use."
        elif p.value is not None and not spec[1] <= p.value <= spec[2]:
            status, note = "rejected", f"{p.value} {spec[0]} is outside the plausible range {spec[1]}–{spec[2]} {spec[0]}."
        elif prov != "assumed" and (not p.evidence or _norm(p.evidence) not in narr):
            status, note = "rejected", "The quoted evidence does not appear in the incident report."
        elif prov == "observed" and p.value is not None and not any(
            abs(n - p.value) < 1e-9 for n in _numbers(p.evidence)
        ):
            status, prov = "downgraded", "estimated"
            note = "Marked observed, but the value is not stated in the quoted text; treated as an estimate."
        out.append(
            ProposedParameter(
                name=p.name,
                value=p.value,
                unit=spec[0] if spec else p.unit,
                provenance=prov,
                evidence=p.evidence,
                rationale=p.rationale,
                status=status,
                validation_note=note,
            )
        )
    return IncidentInterpretation(
        summary=raw.summary,
        contributing_factors=raw.contributing_factors,
        parameters=out,
        open_questions=raw.open_questions,
    )


INTERPRET_SYSTEM = """You help a warehouse safety engineer turn a confirmed near-miss report into simulation inputs.
Return ONLY a JSON object with keys: summary, contributing_factors, parameters, open_questions.
Each item in parameters has: name, value (number or null), unit, provenance, evidence, rationale.
Allowed parameter names and units: {names}.
provenance rules:
- "observed": the value is stated in the report. evidence MUST be an exact quote containing the number.
- "estimated": inferred from words in the report (e.g. "roughly normal aisle speed"). evidence MUST be an exact quote.
- "assumed": not supported by the report; give a typical engineering value and explain it in rationale; evidence may be empty.
Never invent quotes. Never present an estimate as observed. Keep summary under 80 words.
Put anything the report leaves unclear into open_questions."""


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        raise ModelBadOutput("Model wrapped its JSON in a code fence; strict parsing rejects it.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelBadOutput(f"Model output is not valid JSON ({exc.msg} at char {exc.pos}).") from exc
    if not isinstance(data, dict):
        raise ModelBadOutput("Model output is JSON but not an object.")
    return data


class TaskOutcome(BaseModel):
    """Everything persisted about one model call."""

    task: str
    request_hash: str
    model_requested: str | None = None
    model_served: str | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempts: int | None = None
    finish_reason: str | None = None
    status: Literal["ok", "invalid_output", "error"]
    error_kind: str | None = None
    error: str | None = None
    raw_output: str | None = None
    result: dict[str, Any] | None = None


def _meta(res: ChatResult) -> dict[str, Any]:
    return {
        "model_requested": res.model_requested,
        "model_served": res.model_served,
        "latency_ms": res.latency_ms,
        "prompt_tokens": res.prompt_tokens,
        "completion_tokens": res.completion_tokens,
        "attempts": res.attempts,
        "finish_reason": res.finish_reason,
        "raw_output": res.content[:20000],
    }


def interpret_incident(client: TokenFactoryClient, narrative: str) -> tuple[TaskOutcome, IncidentInterpretation | None]:
    names = ", ".join(f"{k} ({v[0]})" for k, v in EXTRACTABLE.items())
    system = INTERPRET_SYSTEM.format(names=names)
    user = f"Incident report:\n<<<\n{narrative.strip()}\n>>>"
    rh = sha256_hex(canonical_json({"task": "interpret_incident", "system": system, "user": user}))
    res = client.chat_json(system, user, max_tokens=1500)
    meta = _meta(res)
    if res.finish_reason == "length":
        return TaskOutcome(task="interpret_incident", request_hash=rh, status="invalid_output",
                           error_kind="truncated", error="Model output was cut off at the token limit.", **meta), None
    try:
        raw = RawInterpretation.model_validate(_parse_json(res.content))
    except (ModelBadOutput, ValidationError) as exc:
        return TaskOutcome(task="interpret_incident", request_hash=rh, status="invalid_output",
                           error_kind="invalid_output", error=str(exc)[:1000], **meta), None
    interp = validate_interpretation(raw, narrative)
    return TaskOutcome(task="interpret_incident", request_hash=rh, status="ok",
                       result=interp.model_dump(mode="json"), **meta), interp


# ---------------------------------------------------------------------------
# Intervention candidates
# ---------------------------------------------------------------------------


class CandidateCheck(BaseModel):
    index: int
    status: Literal["valid", "rejected"]
    reasons: list[str]
    intervention: dict[str, Any] | None
    raw: dict[str, Any]


PROPOSE_SYSTEM = """You propose physical layout changes for a warehouse crossing where a near miss occurred.
Return ONLY a JSON object: {{"candidates": [ ... ]}} with 3 to 5 candidates.
Each candidate is one of:
- {{"type": "relocate_obstruction", "obstruction_id": <id>, "offset_m": [dx, dy], "label": <short>, "rationale": <one sentence>}}
- {{"type": "remove_obstruction", "obstruction_id": <id>, "label": <short>, "rationale": <one sentence>}}
- {{"type": "reroute_crossing", "crossing_x_m": <number>, "label": <short>, "rationale": <one sentence>}}
Only obstructions marked movable may be relocated or removed. Offsets are metres in the site frame
(x east, y north). Relocated objects must stay inside the site bounds and must not block either route.
Crossing positions must lie within the walkable range given. Do not propose signage, training,
speed limits or anything other than these three types. Do not predict whether a change will work:
NearMiss tests every candidate in simulation."""


def scenario_brief(sc: Scenario) -> dict[str, Any]:
    return {
        "site_bounds_m": [sc.bounds.width_m, sc.bounds.height_m],
        "forklift": {
            "route": sc.forklift.route,
            "cruise_speed_mps": sc.forklift.cruise_speed.value,
            "footprint_m": [sc.forklift.length_m, sc.forklift.width_m],
        },
        "pedestrian": {"route": sc.pedestrian.route, "walking_speed_mps": sc.pedestrian.walking_speed.value},
        "obstructions": [
            {
                "id": o.id,
                "label": o.label,
                "center_m": o.geometry.center,
                "size_m": [o.geometry.length, o.geometry.width],
                "movable": o.movable,
            }
            for o in sc.obstructions
        ],
        "crossing_walkable_range_x_m": (
            [sc.crossing_template.min_crossing_x_m, sc.crossing_template.max_crossing_x_m]
            if sc.crossing_template
            else None
        ),
    }


def validate_candidates(sc: Scenario, data: dict[str, Any]) -> list[CandidateCheck]:
    items = data.get("candidates")
    if not isinstance(items, list):
        raise ModelBadOutput("Model output has no 'candidates' list.")
    checks: list[CandidateCheck] = []
    for i, item in enumerate(items[:8]):
        reasons: list[str] = []
        if not isinstance(item, dict):
            checks.append(CandidateCheck(index=i, status="rejected", reasons=["not an object"], intervention=None, raw={"value": str(item)[:200]}))
            continue
        payload = {k: v for k, v in item.items() if k in {"type", "obstruction_id", "offset_m", "crossing_x_m", "label", "rationale"}}
        payload["id"] = f"model-{i + 1}"
        payload["origin"] = "model"
        payload.setdefault("label", f"Model candidate {i + 1}")
        if payload.get("type") not in PHYSICAL_INTERVENTION_TYPES:
            checks.append(CandidateCheck(index=i, status="rejected",
                                         reasons=[f"type '{item.get('type')}' is not a supported physical intervention"],
                                         intervention=None, raw=item))
            continue
        try:
            iv = INTERVENTION.validate_python(payload)
        except ValidationError as exc:
            checks.append(CandidateCheck(index=i, status="rejected",
                                         reasons=[e["msg"] for e in exc.errors()][:3], intervention=None, raw=item))
            continue
        target = getattr(iv, "obstruction_id", None)
        if target is not None:
            obs = sc.obstruction(target)
            if obs is None:
                reasons.append(f"obstruction '{target}' does not exist")
            elif not obs.movable:
                reasons.append(f"'{obs.label}' is fixed structure and cannot be moved or removed")
        if isinstance(iv, RerouteCrossing):
            tpl = sc.crossing_template
            if tpl is None:
                reasons.append("this site has no alternative crossing positions")
            elif not tpl.min_crossing_x_m <= iv.crossing_x_m <= tpl.max_crossing_x_m:
                reasons.append(f"crossing x {iv.crossing_x_m} m is outside the walkable range")
        if not reasons:
            try:
                resolved, changes = apply_intervention(sc, iv)
                if not changes:
                    reasons.append("leaves the scenario unchanged")
                reasons += [f.message for f in geometry_findings(resolved, "candidate")]
            except TransformError as exc:
                reasons.append(str(exc))
        checks.append(CandidateCheck(index=i, status="rejected" if reasons else "valid", reasons=reasons,
                                     intervention=iv.model_dump(mode="json"), raw=item))
    return checks


def propose_interventions(
    client: TokenFactoryClient, sc: Scenario, nominal_summary: dict[str, Any]
) -> tuple[TaskOutcome, list[CandidateCheck] | None]:
    user = (
        "Site and incident geometry (JSON):\n"
        + json.dumps(scenario_brief(sc))
        + "\nReplayed incident (deterministic simulation, for context only):\n"
        + json.dumps(nominal_summary)
        + "\nIncident summary: "
        + (sc.incident.summary if sc.incident else "n/a")
    )
    rh = sha256_hex(canonical_json({"task": "propose_interventions", "system": PROPOSE_SYSTEM, "user": user}))
    res = client.chat_json(PROPOSE_SYSTEM, user, max_tokens=1500, temperature=0.4)
    meta = _meta(res)
    if res.finish_reason == "length":
        return TaskOutcome(task="propose_interventions", request_hash=rh, status="invalid_output",
                           error_kind="truncated", error="Model output was cut off at the token limit.", **meta), None
    try:
        checks = validate_candidates(sc, _parse_json(res.content))
    except ModelBadOutput as exc:
        return TaskOutcome(task="propose_interventions", request_hash=rh, status="invalid_output",
                           error_kind="invalid_output", error=str(exc), **meta), None
    return TaskOutcome(task="propose_interventions", request_hash=rh, status="ok",
                       result={"candidates": [c.model_dump(mode="json") for c in checks]}, **meta), checks
