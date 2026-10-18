# Benchmark preregistration — NearMiss benchmark v1

Written and committed **before** the benchmark was run. Any later change to this file,
to `data/benchmark/cases.v1.json`, to the criteria, or to strategy version `1.0.0` is a
deviation and will be reported as such in `docs/benchmark-results.md`.

## Hypothesis

> At a fixed budget of 48 simulation runs per evaluation, NearMiss' physics-targeted stress
> search exposes intervention failures that a credible conventional parameter sweep
> misses, judged by independently computed outcomes.

This is a hypothesis, not an expected result. If it is not supported we report that and
describe what the compiler contributes instead (experiment validity, traceability).

## What is compared

| Workflow | How it chooses the 48 runs |
| --- | --- |
| `targeted_search` (NearMiss) | Nominal replay; analytic worst stopping corner × pedestrian-speed and placement extremes at two arrival leads; arrival-aligned Latin hypercube; adaptive perturbation of the lowest-margin runs. |
| `conventional_sweep` (baseline) | Nominal replay plus a 47-point Latin hypercube over the same envelope. |
| `nominal_only` (reference) | The replayed incident only (1 run). Reported to show what a replay-only check concludes. |

Both full workflows use the same simulator, metrics, criteria, envelope, intervention, seed
list (0–29) and budget. Neither sees the reference labels.

## Ground-truth labels (independent of both workflows)

- Dense reference sweep: 8,192 points of a Halton sequence (skip 20) over the scenario's
  envelope. Neither workflow uses Halton.
- A case is **unsafe** if any reference run is a collision or a near miss under
  `nearmiss.criteria/1` (moving-forklift clearance below 0.5 m). Otherwise it is
  **no failure found**.
- Labels are a lower bound on unsafety. If a workflow rejects a case labelled "no failure
  found", its failing run is a real, replayable failure that the reference sweep missed.
  These are counted as false rejections in the headline numbers, as the formulas require,
  and listed separately as *reference-sweep misses*.

## Cases

`data/benchmark/cases.v1.json`: 28 cases over 4 synthetic scenarios.

- Development scenarios (used while building the engine and strategies):
  `dc-aisle4-crossing`, `dc-aisle9-wrapper`.
- Held-out scenarios (created after strategy v1.0.0 was frozen; never used to tune it):
  `cs-door2-exit`, `mz-stair-foot`.
- Categories: `as_is` (the existing layout; predefined failure case), `negative_control`
  (a token 0.5 m move), `candidate` (relocation, removal or reroute).

Envelope per scenario: the compiler's default ranges for all seven stress axes, centred on
the scenario's values; `pedestrian_start_offset` is widened only if the compiler reports
`timing_unreachable`, to exactly its corrected range. The final envelopes are stored in
the results file.

## Metrics (over case × seed evaluations, intervention cases only; `as_is` reported separately)

- **Unsafe-intervention recall** = rejected unsafe evaluations / unsafe evaluations.
- **False-acceptance rate** = 1 − recall.
- **Effective-intervention acceptance rate** = accepted "no failure found" evaluations /
  "no failure found" evaluations.
- **False-rejection rate** = 1 − effective acceptance.
- Median runs to first failure (on unsafe cases), mean wall time per evaluation.
- 95 % Wilson intervals are shown for orientation only: seeds within a case are not
  independent, so the intervals understate uncertainty.

Model inference is not part of either workflow and is reported separately from
application logs (`model_calls` table).

## Decision rule

The hypothesis is **supported** if, on the **held-out intervention cases**:

1. targeted recall exceeds conventional recall by at least 0.15, **and**
2. targeted false-rejection rate is at most conventional false-rejection rate + 0.02.

It is **not supported** if condition 1 fails. If there are no unsafe held-out intervention
cases, the held-out comparison is **not evaluable** and the result is reported on
development cases only, labelled as such.

## Predictions

- P1. Recall: targeted > conventional on both development and held-out subsets.
- P2. False rejection near zero for both (rejections require a real failing run).
- P3. Nominal-only accepts most candidate interventions, including unsafe ones.
- P4. Median runs to first failure is lower for targeted search.
- P5. At least one candidate per scenario is unsafe despite a clear nominal replay.

## Pipeline check

Before the full run, the pipeline may be smoke-tested with 2 seeds and 64 reference points
to catch crashes. No strategy parameter, criterion, case or envelope rule will be changed
after that smoke test.
