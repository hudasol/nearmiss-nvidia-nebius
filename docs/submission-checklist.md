# Acceptance criteria and submission checklist

Status as of 2026-10-18. "Verified" means checked by running the system, not by reading code.

| Criterion | Status | Evidence |
| --- | --- | --- |
| Application starts with documented commands | Verified | README quick start; Playwright e2e starts the API serving the production build |
| Calibrated crossing scenario runs in the engine | Verified | `dc-aisle4-crossing` replay: 0.29 m vs reported 0.3 ± 0.2 m |
| Both interventions modify the intended physical scenario | Verified | Compiler's independent scenario diff; `tests/test_compiler.py`, `tests/test_domain.py` |
| Stress tests change physical conditions and recompute outcomes | Verified | `tests/test_evaluation.py`; every run stored with its own config hash |
| A meaningful intervention failure from real runs | Verified | "Move stack 3 m west" (3.41 m in replay) and "Move crossing to x = 27 m" (2.49 m) both fail under stress; benchmark reference sweep confirms (73 and 42 of 8,192 runs) |
| Conventional baseline supports parameter variation | Verified | Latin hypercube sweep over the same envelope and budget |
| Compiler rejects a methodologically invalid experiment | Verified | 24 compiler tests; rejection and correction in the UI e2e test |
| Metrics and reference labels defined independently | Verified | Metrics from poses only; labels from a Halton reference sweep used by neither workflow |
| Model candidates evaluated independently of the model | Verified | Candidates pass geometry checks, then simulation; the model never sees criteria or labels |
| Eligible NVIDIA open model integrated | Implemented, tested with mocks | `nvidia/Nemotron-3_5-Lightning` via `nearmiss/integrations`; 17 mock-transport tests |
| **Genuine Nebius Token Factory runtime call verified** | **Pending: needs an API key** | See "Verify the Nebius call" below |
| UI shows real results; primary interactions work | Verified | Manual review at 1440 px and 375 px; 3 Playwright e2e tests |
| Results inspectable and reproducible from recorded configurations | Verified | Run replay re-simulates and checks config and trajectory hashes (API test and e2e) |
| Tests and production build run successfully | Verified | 110 pytest tests, 3 Playwright tests, ruff, strict mypy, eslint (0 errors), Vite build; CI green on Linux |
| No fabricated claims or unverified metrics | Verified | Benchmark reported against its preregistered rule, including the unsupported verdict |
| Public licence, README, setup and architecture docs | Verified | Apache-2.0 `LICENSE`, README, `docs/` |
| Demo script fits the video limit | Written | `docs/demo-script.md` (2:48) |
| Positioned as decision support, not a certified safety system | Verified | README, every report, simulation model doc |

## Verify the Nebius call

1. Create a key at https://tokenfactory.nebius.com/ (API keys). It starts with `v1.`.
2. In the repository root, copy the template and add the key (never commit `.env`; it is
   git-ignored):

   ```bash
   cp .env.example .env
   ```

   Then set `NEBIUS_API_KEY=` to your key in `.env`.
3. Run the live test, which lists the key's models, resolves the NVIDIA model and makes a real
   Nemotron call:

   ```bash
   pytest -m live -s
   ```

4. Start the app (`python -m nearmiss.api.app`), open **Model integration**, and select
   **Check connection and model**. Then open an incident and select **Interpret with
   Nemotron**. Each call is recorded with the served model ID, latency and tokens, and appears
   on the Integration page as proof of the runtime call.

If the configured model is not in your key's catalogue, NearMiss falls back to another
`nvidia/` Nemotron model and records which one served the call. If no NVIDIA model is available,
the check fails with a clear message rather than silently using another provider.

## Still to do for the submission

- Verify the live Nebius call (above) and record the demo video from `docs/demo-script.md`.
- Upload the video to YouTube (public, under 3 minutes) and fill in the Devpost form: track
  "Physical AI", repository URL, video URL, NVIDIA and Nebius usage (README section), and the
  feedback in `docs/technology-feedback.md`.
