"""NearMiss HTTP API (FastAPI).

The API orchestrates; it never implements physics or decides outcomes itself. Every
external request is validated, every model call has a timeout and is persisted, and job
states are explicit.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from nearmiss import __version__
from nearmiss.api.jobs import JobManager
from nearmiss.compiler.compiler import compile_experiment, default_axis, geometry_findings
from nearmiss.compiler.spec import OVERRIDABLE_PARAMETERS
from nearmiss.domain.catalog import REPO_ROOT, reference_scenarios
from nearmiss.domain.models import (
    STRESS_AXIS_LIMITS,
    STRESS_AXIS_META,
    Intervention,
    Scenario,
)
from nearmiss.domain.transforms import TransformError, apply_intervention, apply_stress
from nearmiss.evaluation.criteria import classify_run, load_criteria
from nearmiss.evaluation.metrics import (
    METRIC_DEFINITIONS,
    compute_metrics,
    conflict_zone,
    separation_series,
)
from nearmiss.integrations.model_tasks import interpret_incident, propose_interventions
from nearmiss.integrations.nebius import ModelError, NebiusConfig, TokenFactoryClient
from nearmiss.persistence.store import Store, now
from nearmiss.reporting.report import build_report, report_markdown
from nearmiss.simulation.engine import (
    ENGINE_VERSION,
    canonical_json,
    scenario_hash,
    sha256_hex,
    simulate,
)

log = logging.getLogger("nearmiss.api")
INTERVENTION = TypeAdapter(Intervention)
WEB_DIST = REPO_ROOT / "apps" / "web" / "dist"
BENCHMARK_RESULTS = REPO_ROOT / "data" / "benchmark" / "results.v1.json"
DISPLAY_HZ = 10


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Minimal .env loader; never overrides variables already set in the environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k in ("request_id", "path", "status", "ms"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload)


def configure_logging() -> None:
    root = logging.getLogger("nearmiss")
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        root.addHandler(h)
        root.setLevel(logging.INFO)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        self.status, self.code, self.message, self.details = status, code, message, details


# --- request bodies ------------------------------------------------------------


class SimulateRequest(BaseModel):
    scenario_id: str | None = None
    scenario: Scenario | None = None
    intervention: dict[str, Any] | None = None
    stress_point: dict[str, float] = Field(default_factory=dict)


class SaveScenarioRequest(BaseModel):
    scenario: Scenario
    parent_id: str | None = None


class InterpretRequest(BaseModel):
    narrative: str | None = Field(default=None, max_length=8000)


def _trajectory_payload(sc: Scenario, stressed: Scenario) -> dict[str, Any]:
    traj = simulate(stressed)
    metrics = compute_metrics(stressed, traj)
    criteria = load_criteria()
    seps = separation_series(stressed, traj)
    every = max(1, round((1 / DISPLAY_HZ) / traj.dt))
    small = traj.downsample(every)
    idx = list(range(0, len(traj.t), every))
    if idx[-1] != len(traj.t) - 1:
        idx.append(len(traj.t) - 1)
    zone = conflict_zone(stressed)
    return {
        "scenario": stressed.model_dump(mode="json"),
        "trajectory": small.model_dump(mode="json"),
        "full_trajectory_hash": traj.trajectory_hash,
        "config_hash": traj.config_hash,
        "frames_full": len(traj.t),
        "separation_m": [seps[i] for i in idx],
        "metrics": metrics.model_dump(mode="json"),
        "outcome": classify_run(metrics, criteria),
        "conflict_zone": None if zone is None else {"polygon": zone.polygon()},
        "engine_version": ENGINE_VERSION,
        "display_hz": DISPLAY_HZ,
    }


def create_app(
    db_path: str | None = None,
    workers: int | None = None,
    client_factory: Callable[[], TokenFactoryClient] | None = None,
    start_jobs: bool = True,
) -> FastAPI:
    load_dotenv()
    configure_logging()
    store = Store(
        db_path or os.environ.get("NEARMISS_DB_PATH", str(REPO_ROOT / "var" / "nearmiss.sqlite3"))
    )
    for sc in reference_scenarios().values():
        store.upsert_scenario(sc, origin="reference")
    jobs = JobManager(
        store, workers if workers is not None else int(os.environ.get("NEARMISS_WORKERS", "0"))
    )
    nebius_config = NebiusConfig.from_env()
    make_client = client_factory or (lambda: TokenFactoryClient(nebius_config))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_jobs:
            jobs.start()
        log.info("NearMiss API %s started", __version__)
        yield
        jobs.stop()
        store.close()
        log.info("NearMiss API stopped")

    app = FastAPI(title="NearMiss API", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.state.jobs = jobs

    @app.middleware("http")
    async def request_log(request: Request, call_next: Callable[[Request], Any]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        response: Response = await call_next(request)
        if request.url.path.startswith("/api/"):
            log.info(
                "request",
                extra={
                    "request_id": rid,
                    "path": f"{request.method} {request.url.path}",
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                },
            )
        response.headers["x-request-id"] = rid
        return response

    @app.exception_handler(ApiError)
    async def api_error(_r: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_r: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"path": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request failed validation.",
                    "details": details,
                }
            },
        )

    def scenario_or_404(scenario_id: str) -> Scenario:
        sc = store.get_scenario(scenario_id)
        if sc is None:
            raise ApiError(404, "scenario_not_found", f"No scenario '{scenario_id}'.")
        return sc

    def experiment_or_404(eid: str, include_plan: bool = True) -> dict[str, Any]:
        exp = store.get_experiment(eid, include_plan=include_plan)
        if exp is None:
            raise ApiError(404, "experiment_not_found", f"No experiment '{eid}'.")
        return exp

    # --- meta -----------------------------------------------------------------

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "engine_version": ENGINE_VERSION,
            "database": store.ping(),
            "job_queue_depth": jobs.queue_depth(),
            "model_integration": {"configured": nebius_config.configured},
            "time": now(),
        }

    @app.get("/api/v1/reference")
    def reference() -> dict[str, Any]:
        return {
            "criteria": load_criteria().model_dump(),
            "metrics": [d.model_dump() for d in METRIC_DEFINITIONS.values()],
            "stress_axes": [
                {
                    "name": k,
                    "unit": v[0],
                    "kind": v[1],
                    "description": v[2],
                    "limits": STRESS_AXIS_LIMITS[k],
                }
                for k, v in STRESS_AXIS_META.items()
            ],
            "overridable_parameters": list(OVERRIDABLE_PARAMETERS),
        }

    # --- integrations ---------------------------------------------------------

    @app.get("/api/v1/integrations/nebius")
    def nebius_status() -> dict[str, Any]:
        verify = store.model_calls(task="verify", limit=1)
        return {
            "configured": nebius_config.configured,
            "base_url": nebius_config.base_url,
            "model_configured": nebius_config.model,
            "last_verification": verify[0] if verify else None,
            "usage": store.model_usage(),
        }

    @app.post("/api/v1/integrations/nebius/verify")
    def nebius_verify() -> dict[str, Any]:
        client = make_client()
        t0 = time.perf_counter()
        outcome: dict[str, Any] = {"task": "verify", "request_hash": sha256_hex("verify")}
        try:
            models = client.list_models()
            resolved = client.resolve_model()
            outcome.update(
                status="ok",
                model_requested=nebius_config.model,
                model_served=resolved,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                result={
                    "nvidia_models": [m for m in models if m.lower().startswith("nvidia/")],
                    "catalog_size": len(models),
                },
            )
        except ModelError as exc:
            outcome.update(
                status="error",
                error_kind=exc.kind,
                error=str(exc),
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
        store.record_model_call(None, outcome)
        if outcome["status"] != "ok":
            raise ApiError(
                502 if outcome["error_kind"] != "not_configured" else 503,
                outcome["error_kind"],
                outcome["error"],
            )
        return outcome

    # --- scenarios ------------------------------------------------------------

    @app.get("/api/v1/scenarios")
    def list_scenarios() -> list[dict[str, Any]]:
        return store.list_scenarios()

    @app.get("/api/v1/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str) -> dict[str, Any]:
        sc = scenario_or_404(scenario_id)
        return {
            "scenario": sc.model_dump(mode="json"),
            "default_envelope": [default_axis(n, sc).model_dump() for n in STRESS_AXIS_META],
            "geometry_findings": [f.model_dump() for f in geometry_findings(sc, "scenario")],
        }

    @app.post("/api/v1/scenarios", status_code=201)
    def save_scenario(body: SaveScenarioRequest) -> dict[str, Any]:
        sc = body.scenario
        if sc.id in reference_scenarios():
            raise ApiError(
                409,
                "reference_scenario_readonly",
                "Reference scenarios are read-only; save under a new id.",
            )
        if sc.data_origin == "synthetic_reference":
            raise ApiError(
                422, "invalid_origin", "Saved scenarios must be 'user_defined' or 'model_assisted'."
            )
        findings = geometry_findings(sc, "scenario")
        if findings:
            raise ApiError(
                422,
                "invalid_geometry",
                "Scenario geometry is invalid.",
                [f.model_dump() for f in findings],
            )
        store.upsert_scenario(sc, origin="user", parent_id=body.parent_id)
        return {"id": sc.id, "content_hash": scenario_hash(sc)}

    @app.get("/api/v1/scenarios/{scenario_id}/replay")
    def replay_scenario(scenario_id: str) -> dict[str, Any]:
        sc = scenario_or_404(scenario_id)
        return _trajectory_payload(sc, sc)

    @app.post("/api/v1/simulate")
    def simulate_endpoint(body: SimulateRequest) -> dict[str, Any]:
        if body.scenario is not None:
            sc = body.scenario
        elif body.scenario_id:
            sc = scenario_or_404(body.scenario_id)
        else:
            raise ApiError(422, "missing_scenario", "Provide scenario_id or scenario.")
        if body.intervention is not None:
            try:
                iv = INTERVENTION.validate_python(body.intervention)
                sc, _ = apply_intervention(sc, iv)
            except (ValidationError, TransformError) as exc:
                raise ApiError(422, "invalid_intervention", str(exc)[:500]) from exc
        findings = geometry_findings(sc, "scenario")
        if findings:
            raise ApiError(
                422,
                "invalid_geometry",
                "Scenario geometry is invalid.",
                [f.model_dump() for f in findings],
            )
        for k, v in body.stress_point.items():
            if k not in STRESS_AXIS_LIMITS:
                raise ApiError(422, "unknown_stress_axis", f"Unknown stress axis '{k}'.")
            lo, hi = STRESS_AXIS_LIMITS[k]
            if not lo <= v <= hi:
                raise ApiError(422, "stress_out_of_limits", f"{k}={v} is outside {lo}–{hi}.")
        stressed = apply_stress(sc, body.stress_point) if body.stress_point else sc
        return _trajectory_payload(sc, stressed)

    # --- model tasks ----------------------------------------------------------

    def _model_failure(task: str, scenario_id: str, exc: ModelError) -> ApiError:
        store.record_model_call(
            scenario_id,
            {
                "task": task,
                "request_hash": "",
                "status": "error",
                "error_kind": exc.kind,
                "error": str(exc),
            },
        )
        status = 503 if exc.kind == "not_configured" else 502
        return ApiError(status, exc.kind, str(exc))

    @app.post("/api/v1/scenarios/{scenario_id}/interpret")
    def interpret(scenario_id: str, body: InterpretRequest) -> dict[str, Any]:
        sc = scenario_or_404(scenario_id)
        narrative = body.narrative or (sc.incident.narrative if sc.incident else None)
        if not narrative:
            raise ApiError(
                422, "missing_narrative", "This scenario has no incident report to interpret."
            )
        try:
            outcome, _ = interpret_incident(make_client(), narrative)
        except ModelError as exc:
            raise _model_failure("interpret_incident", scenario_id, exc) from exc
        call_id = store.record_model_call(scenario_id, outcome.model_dump(mode="json"))
        return {"call_id": call_id, **outcome.model_dump(mode="json")}

    @app.post("/api/v1/scenarios/{scenario_id}/candidates")
    def candidates(scenario_id: str) -> dict[str, Any]:
        sc = scenario_or_404(scenario_id)
        traj = simulate(sc)
        m = compute_metrics(sc, traj)
        nominal = {
            "min_moving_separation_m": m.min_moving_separation_m,
            "collision": m.collision,
            "outcome": classify_run(m, load_criteria()),
        }
        try:
            outcome, _ = propose_interventions(make_client(), sc, nominal)
        except ModelError as exc:
            raise _model_failure("propose_interventions", scenario_id, exc) from exc
        call_id = store.record_model_call(scenario_id, outcome.model_dump(mode="json"))
        return {"call_id": call_id, **outcome.model_dump(mode="json")}

    @app.get("/api/v1/scenarios/{scenario_id}/model-calls")
    def scenario_model_calls(scenario_id: str) -> list[dict[str, Any]]:
        return store.model_calls(scenario_id=scenario_id, limit=20)

    # --- experiments ----------------------------------------------------------

    @app.post("/api/v1/experiments/compile")
    def compile_only(spec: dict[str, Any]) -> dict[str, Any]:
        return compile_experiment(spec, store.get_scenario).model_dump(mode="json")

    @app.post("/api/v1/experiments", status_code=201)
    def create_experiment(spec: dict[str, Any], response: Response) -> dict[str, Any]:
        result = compile_experiment(spec, store.get_scenario)
        spec_hash = sha256_hex(canonical_json(spec))
        if result.status == "accepted":
            assert result.plan is not None
            existing = store.find_active_by_spec(result.plan.spec_hash)
            if existing:
                response.status_code = 200
                return {
                    "experiment": store.get_experiment(existing, include_plan=False),
                    "duplicate": True,
                }
            eid = store.create_experiment(
                title=str(spec.get("title", "Untitled experiment")),
                scenario_id=str(spec.get("scenario_id")),
                status="queued",
                spec_hash=result.plan.spec_hash,
                spec=spec,
                compilation=result.model_dump(mode="json", exclude={"plan"}),
                plan=result.plan.model_dump(mode="json"),
                plan_hash=result.plan.plan_hash,
                total_runs=result.plan.total_runs,
            )
            jobs.submit(eid)
        else:
            eid = store.create_experiment(
                title=str(spec.get("title", "Untitled experiment"))[:200],
                scenario_id=str(spec.get("scenario_id", "")),
                status="rejected",
                spec_hash=spec_hash,
                spec=spec,
                compilation=result.model_dump(mode="json"),
                plan=None,
                plan_hash=None,
                total_runs=0,
            )
        return {"experiment": store.get_experiment(eid, include_plan=False), "duplicate": False}

    @app.get("/api/v1/experiments")
    def list_experiments(limit: int = 100) -> list[dict[str, Any]]:
        return store.list_experiments(limit=min(max(limit, 1), 500))

    @app.get("/api/v1/experiments/{eid}")
    def get_experiment(eid: str) -> dict[str, Any]:
        exp = experiment_or_404(eid)
        if exp["plan"]:
            # Arm scenarios are large; the UI fetches them through replay endpoints.
            for arm in exp["plan"]["arms"]:
                arm["scenario"] = {"id": arm["scenario"]["id"], "title": arm["scenario"]["title"]}
        exp["strategy_results"] = store.strategy_results(eid)
        return exp

    @app.post("/api/v1/experiments/{eid}/cancel")
    def cancel_experiment(eid: str) -> dict[str, Any]:
        experiment_or_404(eid, include_plan=False)
        if not jobs.cancel(eid):
            raise ApiError(
                409, "not_cancellable", "Only queued or running experiments can be cancelled."
            )
        return {"experiment": store.get_experiment(eid, include_plan=False)}

    @app.get("/api/v1/experiments/{eid}/runs")
    def experiment_runs(
        eid: str, arm: str | None = None, strategy: str | None = None
    ) -> list[dict[str, Any]]:
        experiment_or_404(eid, include_plan=False)
        return store.runs(eid, arm, strategy)

    @app.get("/api/v1/experiments/{eid}/runs/{arm_id}/{strategy}/{idx}/replay")
    def replay_run(eid: str, arm_id: str, strategy: str, idx: int) -> dict[str, Any]:
        exp = experiment_or_404(eid)
        rec = store.run(eid, arm_id, strategy, idx)
        if rec is None or exp["plan"] is None:
            raise ApiError(404, "run_not_found", "No such run.")
        arm = next((a for a in exp["plan"]["arms"] if a["arm_id"] == arm_id), None)
        if arm is None:
            raise ApiError(404, "arm_not_found", "No such arm.")
        sc = Scenario.model_validate(arm["scenario"])
        stressed = apply_stress(sc, rec["stress_point"])
        payload = _trajectory_payload(sc, stressed)
        payload["verification"] = {
            "recorded_engine_version": exp["plan"]["engine_version"],
            "current_engine_version": ENGINE_VERSION,
            "recorded_config_hash": rec["config_hash"],
            "recorded_trajectory_hash": rec["trajectory_hash"],
            "config_hash_match": payload["config_hash"] == rec["config_hash"],
            "trajectory_hash_match": payload["full_trajectory_hash"] == rec["trajectory_hash"],
        }
        payload["run"] = rec
        return payload

    @app.get("/api/v1/experiments/{eid}/report")
    def experiment_report(eid: str) -> dict[str, Any]:
        experiment_or_404(eid, include_plan=False)
        try:
            return build_report(store, eid)
        except ValueError as exc:
            raise ApiError(409, "report_unavailable", str(exc)) from exc

    @app.get("/api/v1/experiments/{eid}/report.md", response_class=PlainTextResponse)
    def experiment_report_md(eid: str) -> PlainTextResponse:
        experiment_or_404(eid, include_plan=False)
        try:
            md = report_markdown(build_report(store, eid))
        except ValueError as exc:
            raise ApiError(409, "report_unavailable", str(exc)) from exc
        return PlainTextResponse(
            md,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="nearmiss-{eid}.md"'},
        )

    # --- benchmark ------------------------------------------------------------

    @app.get("/api/v1/benchmark")
    def benchmark() -> dict[str, Any]:
        if not BENCHMARK_RESULTS.exists():
            raise ApiError(
                404, "benchmark_not_run", "No benchmark results have been generated yet."
            )
        data = json.loads(BENCHMARK_RESULTS.read_text(encoding="utf-8"))
        cases = []
        for c in data["cases"]:
            cases.append(
                {
                    "case_id": c["case_id"],
                    "scenario_id": c["scenario_id"],
                    "role": c["role"],
                    "category": c["category"],
                    "intervention": c["intervention"],
                    "nominal_outcome": c["nominal_outcome"],
                    "nominal_moving_separation_m": c["nominal_moving_separation_m"],
                    "oracle": c["oracle"],
                    "rejections": {
                        k: sum(1 for o in v if o["decision"] == "rejected")
                        for k, v in c["strategies"].items()
                    },
                    "evaluations": {k: len(v) for k, v in c["strategies"].items()},
                    "oracle_misses": len(c["oracle_misses"]),
                }
            )
        return {
            k: data[k]
            for k in (
                "benchmark_id",
                "generated_at",
                "runtime_s",
                "provenance",
                "config",
                "envelopes",
                "summary",
            )
        } | {"cases": cases}

    # --- web UI ---------------------------------------------------------------

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = WEB_DIST / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")

    return app


def main() -> None:
    import uvicorn

    load_dotenv()
    uvicorn.run(
        "nearmiss.api.app:create_app",
        factory=True,
        host=os.environ.get("NEARMISS_HOST", "127.0.0.1"),
        port=int(os.environ.get("NEARMISS_PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
