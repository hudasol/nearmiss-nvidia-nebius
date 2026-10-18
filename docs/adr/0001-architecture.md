# ADR 0001 — NearMiss architecture

- Status: accepted
- Date: 2026-10-18
- Deciders: NearMiss engineering (single-team hackathon build)

## Context

NearMiss must turn a confirmed warehouse near-miss into a reproducible simulation
experiment, test proposed safety changes under controlled physical variation, and
report — from independently computed metrics — where a change that looks effective
in the replayed incident stops working. The build has to satisfy the Nebius × NVIDIA
Global AI Hackathon rules (Physical AI track), verified on 2026-10-18:

- **Platform rule.** The project "makes a runtime call to the Token Factory inference
  API, or is deployed/run using Nebius AI Cloud compute (Serverless Jobs, Serverless
  Endpoints, or DevPods)". The FAQ confirms that calling an NVIDIA model through
  Token Factory satisfies this and the rest of the app need not be hosted on Nebius.
- **Model rule.** At least one NVIDIA open-source model must be used. The FAQ confirms
  that Physical AI entries may use simulation instead of hardware and that NVIDIA
  open models beyond Nemotron/GR00T/Cosmos/Sonic are allowed. Calling an NVIDIA model
  through another provider (Hugging Face, OpenRouter) does **not** count.
- **Video.** Under three minutes, public on YouTube, with at least one minute of the
  key application modules in action (no hardware in this project).
- **Repo.** Public, open-source licence visible in the About panel, README with setup
  instructions, and a description of NVIDIA and Nebius usage plus technology feedback.
- Deadline: 2026-10-30 10:00 PT.

The development machine is Windows 11, 16 logical CPUs, **no NVIDIA GPU**, Python 3.13,
Node 24. No Nebius AI Cloud project or GPU allocation is available to this build.

## Decisions

### 1. Physical simulation — deterministic 2-D kinematic engine (chosen) vs Isaac Sim

| Criterion | NVIDIA Isaac Sim | Bounded deterministic engine (chosen) |
| --- | --- | --- |
| Environment availability | Needs an RTX-class NVIDIA GPU (local or a Nebius GPU VM). Neither is available here. | Runs on any CPU, in CI, in tests. |
| Fidelity relevant to the question | Full rigid-body dynamics, rendering, sensors. Most of it (tyre contact, mass distribution, rendering) is not what decides a forklift–pedestrian crossing outcome. | The quantities that decide the outcome — sightlines past an obstruction, perception-reaction delay, braking deceleration, footprint geometry, arrival timing — are modelled explicitly and testably. |
| Reproducibility | GPU physics is not bit-reproducible across hardware/driver versions. | Bit-identical replay from a recorded configuration; trajectory hashes are stored and re-checked. |
| Throughput | Seconds per episode plus scene-loading overhead. | ≈10–30 ms per episode in pure Python; dense reference sweeps of thousands of runs complete in minutes on 16 cores. |
| Integration effort in the time remaining | High (Omniverse install, USD scene authoring, headless orchestration). | Low; the engine is ordinary, unit-tested Python. |

The engine is deliberately *bounded*: planar (top-down) geometry in metres, fixed
timestep, a forklift modelled as an oriented rectangle following a route with
acceleration/braking limits and a driver perception-reaction model, a pedestrian as a
disc following a route at walking speed, opaque obstruction polygons that block line of
sight, and exact rectangle–disc distance for separation and overlap. Assumptions and
limits are listed in `docs/simulation-model.md`. The engine sits behind a stable
`simulate(SimulationConfig) -> Trajectory` interface so that an Isaac Sim backend could
replace it later without touching the compiler, evaluator or UI.

### 2. NVIDIA open model — Nemotron 3.5 Lightning via Token Factory

- **Cosmos.** `nvidia/Cosmos3-Super-Reasoner` was the hosted physical-reasoning model on
  Token Factory, but Nebius retired it from the Serverless API on 2026-08-31 (August 2026
  deprecation notice; Nebius' own `nebius-physical-ai` repo records an HTTP 409 "model is
  stopped" after that date). A dedicated endpoint would need Nebius AI Cloud GPUs, which
  this build does not have. Cosmos is therefore not used.
- **Nemotron.** Nebius' migration notes name `nvidia/Nemotron-3_5-Lightning`
  (NVIDIA-Nemotron-3.5-Lightning-30B-A3B, open weights) as the replacement default for
  text workloads, and the hackathon text recommends Nemotron Nano/Super for fast calls
  and Ultra for heavy reasoning. NearMiss uses Nemotron for two bounded, structured tasks:
  1. **Incident interpretation** — turn a confirmed free-text incident report into
     proposed scenario parameters, each tagged observed / estimated / assumed with the
     quoted source text.
  2. **Intervention candidates** — propose intervention specifications restricted to the
     intervention types the simulator supports, with parameters inside schema ranges.
- The model ID is **not hard-coded as an assumption**: the adapter reads
  `NEBIUS_MODEL` (default `nvidia/Nemotron-3_5-Lightning`), checks it against the live
  `/v1/models` list at startup, and falls back to other `nvidia/` Nemotron IDs present in
  that list. The model actually served is recorded with every call.
- Thinking is disabled for these direct-output calls
  (`chat_template_kwargs.enable_thinking=false`, per the model card and Nebius' migration
  notes) and output is requested as JSON, then validated against strict Pydantic schemas
  and compiler domain rules. Malformed output is rejected, never repaired into a success.
- The model never sees or edits reference labels, acceptance criteria or evaluation
  results, and never decides whether an intervention passes.

### 3. Nebius inference and deployment

- Runtime calls go to the OpenAI-compatible endpoint
  `https://api.tokenfactory.nebius.com/v1/chat/completions` with a Token Factory key from
  `NEBIUS_API_KEY` (server-side only). A plain `httpx` client is used rather than the
  OpenAI SDK to keep timeouts, retries and error mapping explicit.
- Every call is persisted (model requested/served, latency, token usage, request hash,
  validation outcome) so the integration is auditable from the UI.
- The application itself runs locally (FastAPI + static web build). Hosting the API on
  Nebius is optional per the FAQ and is not claimed.

### 4. Batch execution — in-process worker + multiprocessing CLI (chosen) vs Nebius Serverless Jobs

Experiment jobs (tens of runs, ~1 s) run on a bounded background worker inside the API
with persisted job states (queued, running, completed, failed, cancelled). Dense
reference sweeps for the benchmark (thousands of runs) use a `multiprocessing` CLI. The
benchmark runner is packaged so it can be submitted as a Nebius Serverless Job, but no
Nebius AI Cloud project is available to this build, so Serverless Jobs execution is
**documented, not claimed**.

### 5. Persistence and reproducibility — SQLite + content hashes

SQLite (standard library) stores scenarios, experiment specifications (original,
validation findings, compiled plan), runs, metrics, reports and model-call records. Every
stored object carries a schema version, a UTC timestamp, and a SHA-256 hash of its
canonical JSON. Run trajectories are reproducible from the stored configuration; evidence
runs keep their trajectory and its hash, and a replay endpoint recomputes the run and
compares hashes.

### 6. Local development and delivery

- Backend: Python 3.13, FastAPI, Pydantic v2, NumPy-free core (pure-Python geometry keeps
  determinism and portability simple), pytest, ruff, mypy.
- Frontend: TypeScript, React, Vite; the plan view is SVG driven entirely by
  trajectories returned by the API (the browser never computes physics or outcomes).
- One command starts each side; the production web build is served by the API.

## Consequences

- Physical fidelity is bounded to planar kinematics, braking and sightlines; vehicle
  dynamics beyond longitudinal braking, pedestrian evasive behaviour, sensor noise and
  3-D occlusion are out of scope and are stated as limitations in every report.
- The core loop works without the model; Nemotron adds interpretation and candidate
  generation and is clearly labelled as model-generated wherever it appears.
- The Nebius runtime call can only be verified with a valid Token Factory key; until
  then the integration is implemented and unit-tested against recorded fixtures, and the
  README states plainly that live verification is pending.
