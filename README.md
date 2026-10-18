# NearMiss

**Replay-to-Redesign for warehouse safety.** NearMiss turns a confirmed forklift–pedestrian
near-miss into a reproducible physical simulation, lets a safety manager propose layout
changes, and stress-tests each change under varied speeds, timing, reaction times and
obstruction placement. Its job is to show when a change that fixes the replayed incident
**stops working once conditions change**, and to prove that with replayable evidence.

Built for the Nebius × NVIDIA Global AI Hackathon 2026, Physical AI track.

> NearMiss is a simulation-based decision-support tool, not a certified safety system.
> All scenarios shipped in this repository are synthetic and illustrative.

## What it does

1. **Replay the incident.** A calibrated 2-D crossing scenario (forklift footprint, pedestrian,
   obstructions, sight lines, braking, perception-reaction time) reproduces the reported
   closest approach. Every input is tagged *observed*, *estimated* or *assumed*.
2. **Interpret the report (Nemotron).** An NVIDIA Nemotron model on Nebius Token Factory reads
   the incident narrative and proposes simulation inputs, each tied to a verbatim quote. Values
   whose quote does not contain the number are downgraded from "observed" to "estimated";
   invented quotes are rejected.
3. **Propose changes.** Move or remove an obstruction, or move the crossing, by hand or from
   Nemotron's validated suggestions. Signage and training can be entered but are rejected by
   the compiler, because the simulator has no validated model of their effect.
4. **Compile the experiment.** The Safety Change Compiler rejects methodologically unsound
   experiments before any run: confounded changes, missing stress conditions, ranges outside
   physical limits, metrics that cannot support the stated claim, and stress envelopes that
   can never reach the conflict timing at a moved crossing. It returns structured findings
   and, where safe, a corrected specification.
5. **Stress-test.** Each change and the as-is baseline run under identical conditions with two
   workflows and the same budget: NearMiss' physics-targeted search and a conventional Latin
   hypercube parameter sweep.
6. **Decide from independent metrics.** Outcomes come from the recorded trajectory (footprint
   clearance while the truck moves, contact, TTC, post-encroachment time) against
   pre-declared criteria, never from the model.
7. **Report.** Evidence reports are built from stored runs. Every failure can be replayed;
   the replay recomputes the run and checks its configuration and trajectory hashes.

## What it found

On the reference scenario, moving the pallet stack 3 m west raises the replayed clearance from
0.29 m to 3.41 m, and moving the crossing to x = 27 m raises it to 2.49 m. Both look like fixes,
and both fail under stress inside the site's operating envelope.

The preregistered benchmark (28 cases, 4 synthetic scenarios, 30 seeds, 48 runs per
evaluation, labels from an independent 8,192-run reference sweep):

- A replay-only check accepted all 13 interventions that clear the replay but fail under stress.
- On development scenarios, targeted search caught unsafe interventions in 94.8 % of
  evaluations against 38.9 % for a conventional Latin hypercube sweep with the same budget.
- On held-out scenarios both workflows caught 100 %, so the preregistered hypothesis is **not
  supported** there. The targeted search also found real near misses in one held-out case that
  the reference sweep missed, which the preregistered formulas count as false rejections.

Full results, deviations and caveats: [docs/benchmark-results.md](docs/benchmark-results.md).

## Architecture

```
apps/web (React + TypeScript)        UI only: draws engine output, never computes physics
        │  /api/v1 (JSON)
nearmiss/api (FastAPI)                orchestration, validation, job states, structured logs
   ├── nearmiss/compiler              Safety Change Compiler: spec → validated plan | findings
   ├── nearmiss/evaluation            strategies, runner, metrics, criteria, benchmark
   ├── nearmiss/simulation            deterministic time-stepped engine (nearmiss-kinematic/1.1.0)
   ├── nearmiss/domain                versioned schemas, geometry, scenario transforms
   ├── nearmiss/integrations          Nebius Token Factory client, Nemotron tasks + validation
   ├── nearmiss/reporting             evidence reports (JSON, Markdown)
   └── nearmiss/persistence           SQLite: scenarios, experiments, runs, model calls
data/scenarios, data/criteria, data/benchmark   reference data, pre-declared criteria and cases
```

Key decisions are recorded in [docs/adr/0001-architecture.md](docs/adr/0001-architecture.md).
In short: a bounded deterministic engine instead of Isaac Sim (no NVIDIA GPU available,
bit-reproducible replays, thousands of runs per minute), and Nemotron 3.5 Lightning through
Token Factory (hosted Cosmos3 Reasoner was retired from Token Factory serverless on
2026-08-31). The physics model and its limits are in
[docs/simulation-model.md](docs/simulation-model.md).

## NVIDIA model and Nebius services used

| What | How it is used | Where |
| --- | --- | --- |
| **NVIDIA Nemotron 3.5 Lightning** (`nvidia/Nemotron-3_5-Lightning`, open weights) | Incident-report interpretation and intervention candidates, with JSON output, reasoning disabled (`chat_template_kwargs.enable_thinking=false`), strict schema and domain validation | `nearmiss/integrations/model_tasks.py` |
| **Nebius Token Factory** (OpenAI-compatible `https://api.tokenfactory.nebius.com/v1`) | Runtime inference calls; `/v1/models` checks that the configured NVIDIA model is live for the key, with fallback to other `nvidia/` Nemotron models | `nearmiss/integrations/nebius.py` |

Every model call is persisted (model requested and served, latency, tokens, status, raw
output) and shown on the Integration page. The model never sees reference labels or
acceptance criteria and never decides an outcome. Without a key, every deterministic
feature still works and the model buttons explain what is missing.

Nebius Serverless Jobs were considered for the benchmark sweep but not used: no Nebius AI
Cloud project was available to this build (see the ADR). The benchmark runs locally with
`multiprocessing`.

## Quick start

Requirements: Python 3.11+, Node 20+.

```bash
git clone https://github.com/hudasol/nearmiss-nvidia-nebius.git
cd nearmiss-nvidia-nebius
python -m venv .venv
```

Activate the virtual environment (`source .venv/bin/activate` on macOS/Linux,
`.venv\Scripts\activate` on Windows), then:

```bash
pip install -e ".[dev]"
```

```bash
cp .env.example .env
```

Add your Token Factory key to `.env` as `NEBIUS_API_KEY=...` (optional; everything except the
two Nemotron features works without it). Build the web app and start the server:

```bash
npm --prefix apps/web ci
```

```bash
npm --prefix apps/web run build
```

```bash
python -m nearmiss.api.app
```

Open http://127.0.0.1:8000. For UI development, run `npm --prefix apps/web run dev` and open
http://localhost:5173 (it proxies `/api` to port 8000).

The SQLite database is created at `var/nearmiss.sqlite3`. Stop the server with Ctrl+C; jobs
interrupted by a shutdown are marked failed on the next start rather than silently resumed.

## Using it

1. **Incidents**: pick an incident. The replay shows the plan, the driver's sight line (solid
   when the engine reports the pedestrian in view, dashed when blocked), the clearance timeline
   with the 0.5 m threshold, and engine events.
2. **Interpret with Nemotron** (needs a key): review proposed parameters with their quotes;
   optionally save them as a model-assisted scenario variant.
3. **Design an experiment**: add changes, preview each in the replay, choose the claim and the
   stress envelope, then **Check experiment**. Fix or apply corrections for any findings, then
   **Run experiment**.
4. **Results**: the decision matrix compares the as-is layout and each change under both
   workflows; the stress maps show every run on the same axes. Select any run to replay it,
   check its hashes, and compare it with the as-is layout under the same conditions.
5. **Evidence report**: printable, downloadable as Markdown, generated only from stored runs.

## Testing

```bash
pytest -q
```

110 unit and integration tests (plus one opt-in live test): geometry, schemas, engine determinism, analytic
braking distance, timestep convergence, occlusion, metric definitions on hand-built
trajectories, compiler acceptance and rejection, strategies, runner, API and persistence
lifecycle, replay hash verification, and model failure handling (malformed, fenced,
truncated and invented output; retries; auth errors; timeouts).

```bash
pytest -m live -s
```

Makes a real Token Factory call (needs `NEBIUS_API_KEY`).

```bash
npm --prefix apps/web run e2e
```

Playwright end-to-end tests against the production build and the real API: the full
incident → experiment → compiler rejection → correction → run → report workflow, run replay
verification, and degradation without a key.

Static checks: `ruff check nearmiss tests`, `ruff format --check nearmiss tests`, `mypy`
(strict), `npx eslint .` in `apps/web`. CI runs all of them except the live and e2e suites.

## Benchmark

The hypothesis, labels, metrics and decision rule were
[preregistered](docs/benchmark-preregistration.md) and committed before the run. Results,
including deviations from the plan, are in [docs/benchmark-results.md](docs/benchmark-results.md).

```bash
python -m nearmiss.evaluation.benchmark --workers 8
```

## Limitations

Planar geometry and 2-D occlusion; one truck and one pedestrian; the pedestrian does not yield
or take evasive action; no steering, reversing or rear swing; visibility is geometric line of
sight. Reference scenarios are synthetic. "No failure found" covers only the executed runs
within the stated envelope. The full list is in
[docs/simulation-model.md](docs/simulation-model.md).

## Repository layout

| Path | Contents |
| --- | --- |
| `nearmiss/` | Python package (domain, simulation, compiler, evaluation, integrations, persistence, reporting, api) |
| `apps/web/` | Web app (Vite, React, TypeScript) and Playwright tests |
| `data/scenarios/` | Synthetic reference scenarios (2 development, 2 held out) |
| `data/criteria/` | Pre-declared evaluation criteria |
| `data/benchmark/` | Benchmark cases and results |
| `tests/` | pytest suites |
| `docs/` | ADR, simulation model, design system, benchmark preregistration and results, demo script, technology feedback |

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
