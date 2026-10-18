# Benchmark results — NearMiss benchmark v1

Generated 2026-10-18T01:56:11.532489+00:00 in 78 minutes on 14 worker processes from commit
`39bb50f1841e7f0abcb785e3f4b0b3bceae206aa` (nearmiss-kinematic/1.1.0, nearmiss-metrics/1.0.0, nearmiss-strategies/1.0.0, nearmiss.criteria/1). Raw results:
[`data/benchmark/results.v1.json`](../data/benchmark/results.v1.json). Plan:
[benchmark-preregistration.md](benchmark-preregistration.md).

## Verdict against the preregistered decision rule

**The hypothesis is not supported on the held-out cases.** The rule required targeted recall to
exceed conventional recall by at least 0.15 on held-out intervention cases. Both workflows
reached recall 1.000 there (210/210 evaluations), so condition 1 fails. Condition 2 also fails as
the formula is written: targeted false rejection was 0.189 (17/90) against 0.000 for the
conventional sweep. All 17 of those rejections are explained below: they are real, replayable
failures that the reference sweep missed.

On the **development** cases the difference is large: targeted recall 0.948 (256/270) against
0.389 (105/270) for the conventional sweep, with no false rejections for either. Those scenarios
were used while building the engine and strategies, so this is weaker evidence.

## Why the held-out comparison came out level

Every held-out case labelled unsafe has a large failure region: 744 to 2,340 of 8,192 reference
runs fail. At that density a 47-point Latin hypercube almost always lands in it, so both
workflows reject every time. The development cases include changes whose failure region is
small (20 to 145 of 8,192 runs), and that is where the uniform sweep misses failures and the
targeted search does not. The held-out set, fixed before the run, contained no unsafe case with a
small failure region apart from the one the reference sweep itself missed.

## Reference-sweep misses (counted as false rejections above)

`mz-stair-foot/cross-x14` (move the crossing to x = 14 m) is labelled "no failure found" because
none of its 8,192 Halton reference runs fell below 0.5 m; the closest reached 0.91 m. The targeted
search rejected it in 17 of 30 seeds. Each of the 17 failing runs was replayed on the same engine
and each is a genuine near miss inside the envelope, with clearance between 0.30 m and 0.49 m.
They sit at the analytic worst stopping corner (fast truck, slowest reaction, weakest braking,
fastest walking speed), which is the corner the targeted search tests first and a space-filling
sequence rarely samples. The preregistration said labels are a lower bound on unsafety and that
such cases would be counted as false rejections and listed separately; they are.

**Post-hoc analysis (not preregistered).** Relabelling this case unsafe on the strength of the
verified failures gives held-out recall 0.946 (227/240) for targeted search and 0.875 (210/240)
for the conventional sweep, with no false rejections for either. The gap (0.071) is still below
the preregistered 0.15, so the verdict does not change.

## Predictions

| Prediction | Result |
| --- | --- |
| P1. Targeted recall above conventional on development and held-out subsets | Development: yes (0.948 vs 0.389). Held-out: no, tied at 1.000. |
| P2. False rejection near zero for both | Conventional 0.000. Targeted 0.071 overall, entirely from the reference-sweep misses above. |
| P3. Replay-only accepts most candidate interventions, including unsafe ones | Yes. Replay-only recall is 0.000 on development cases and 0.188 overall; it rejects only changes that fail the replay itself. |
| P4. Lower median runs to first failure for targeted search | Overall yes (2 vs 6). Development 12 vs 11, but the conventional figure only counts the evaluations in which it found any failure. |
| P5. At least one candidate per scenario unsafe despite a clear replay | Yes for 3 of 4 scenarios by the reference labels; for `mz-stair-foot` only through the verified reference-sweep miss. |

## What the results do show

- **Replay-only checking is not enough.** 13 intervention cases clear the replayed
  incident yet fail inside their envelope; the replay check accepted all of them in every seed.
- **Targeted search finds small failure regions at the same cost.** Mean wall time per 48-run
  evaluation was 9.2 s for targeted search and 9.4 s for the conventional sweep (serial, one
  core). No model inference is involved in either workflow.
- **Negative controls behaved as expected.** Every token 0.5 m move was labelled unsafe and
  rejected by both full workflows.

## Deviations from the preregistration

1. **Aborted first run.** A first full run on engine 1.0.0 was stopped about a third of the way
   through, after a pedestrian-model bug was found while reviewing a replay in the UI: a
   pedestrian next to a stopped truck's front corner could freeze for the rest of the run. Engine
   1.1.0 fixed it (commit `f21ee36`); on 600 stress runs the fix changed no safety decision. All 10 cases completed
   before stopping had exactly the same rejection counts in the final run. No strategy, criterion, case or envelope rule changed between
   the two runs. The aborted log is kept locally (`var/benchmark-aborted-run1.log`).
2. **Envelope widening.** As the envelope rule allowed, `pedestrian_start_offset` was widened to
   -15 to 6 s for both development scenarios, because the compiler reported `timing_unreachable`
   for some rerouted crossings at the default ±6 s.
3. **Performance change before the run.** Early termination (stop 2 s after both actors have
   cleared the crossing) was added before either run; on 600 runs every metric was identical
   with and without it.

## Caveats

- Seeds within a case are not independent; the Wilson intervals in the results file treat them as
  independent and therefore understate uncertainty.
- 28 cases on 4 synthetic scenarios is a small benchmark, and the held-out set had no unsafe case
  with a small failure region, which is the regime where the methods differ.
- Labels come from an 8,192-point reference sweep and are a lower bound on unsafety, as
  `mz-stair-foot/cross-x14` shows.

## Results

### Held-out intervention cases

10 cases: 7 unsafe, 3 with no failure in the reference sweep. Rates over cases × 30 seeds.

| Workflow | Unsafe-intervention recall | False acceptance | Effective-intervention acceptance | False rejection | Median runs to first failure | Wall time per evaluation |
| --- | --- | --- | --- | --- | --- | --- |
| NearMiss targeted search | 1.000 (210/210) | 0.000 (0/210) | 0.811 (73/90) | 0.189 (17/90) | 2 | 8.18 s |
| Conventional sweep (LHS) | 1.000 (210/210) | 0.000 (0/210) | 1.000 (90/90) | 0.000 (0/90) | 3 | 8.03 s |
| Replay only (1 run) | 0.429 (90/210) | 0.571 (120/210) | 1.000 (90/90) | 0.000 (0/90) | 1 | 0.16 s |

### Development intervention cases

14 cases: 9 unsafe, 5 with no failure in the reference sweep. Rates over cases × 30 seeds.

| Workflow | Unsafe-intervention recall | False acceptance | Effective-intervention acceptance | False rejection | Median runs to first failure | Wall time per evaluation |
| --- | --- | --- | --- | --- | --- | --- |
| NearMiss targeted search | 0.948 (256/270) | 0.052 (14/270) | 1.000 (150/150) | 0.000 (0/150) | 12 | 9.93 s |
| Conventional sweep (LHS) | 0.389 (105/270) | 0.611 (165/270) | 1.000 (150/150) | 0.000 (0/150) | 11 | 10.46 s |
| Replay only (1 run) | 0.000 (0/270) | 1.000 (270/270) | 1.000 (150/150) | 0.000 (0/150) | — | 0.23 s |

### All intervention cases

24 cases: 16 unsafe, 8 with no failure in the reference sweep. Rates over cases × 30 seeds.

| Workflow | Unsafe-intervention recall | False acceptance | Effective-intervention acceptance | False rejection | Median runs to first failure | Wall time per evaluation |
| --- | --- | --- | --- | --- | --- | --- |
| NearMiss targeted search | 0.971 (466/480) | 0.029 (14/480) | 0.929 (223/240) | 0.071 (17/240) | 2 | 9.20 s |
| Conventional sweep (LHS) | 0.656 (315/480) | 0.344 (165/480) | 1.000 (240/240) | 0.000 (0/240) | 6 | 9.45 s |
| Replay only (1 run) | 0.188 (90/480) | 0.812 (390/480) | 1.000 (240/240) | 0.000 (0/240) | 1 | 0.20 s |

### Per case

| Case | Role | Category | Replay clearance | Reference unsafe / 8,192 | Label | Targeted rejects | Conventional rejects | Replay-only rejects |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dc-aisle4-crossing/as-is` | development | as is | 0.29 m | 914 | unsafe | 30/30 | 30/30 | 30/30 |
| `dc-aisle4-crossing/stack-w0.5` | development | negative control | 0.77 m | 754 | unsafe | 30/30 | 30/30 | 0/30 |
| `dc-aisle4-crossing/stack-w3` | development | candidate | 3.41 m | 73 | unsafe | 29/30 | 6/30 | 0/30 |
| `dc-aisle4-crossing/stack-w6` | development | candidate | 5.95 m | 0 | no failure found | 0/30 | 0/30 | 0/30 |
| `dc-aisle4-crossing/stack-remove` | development | candidate | 6.12 m | 0 | no failure found | 0/30 | 0/30 | 0/30 |
| `dc-aisle4-crossing/cross-x12` | development | candidate | 5.12 m | 0 | no failure found | 0/30 | 0/30 | 0/30 |
| `dc-aisle4-crossing/cross-x27` | development | candidate | 2.49 m | 42 | unsafe | 30/30 | 2/30 | 0/30 |
| `dc-aisle4-crossing/cross-x30` | development | candidate | 2.00 m | 23 | unsafe | 24/30 | 4/30 | 0/30 |
| `dc-aisle9-wrapper/as-is` | development | as is | 0.40 m | 970 | unsafe | 30/30 | 30/30 | 30/30 |
| `dc-aisle9-wrapper/queue-n0.5` | development | negative control | 1.34 m | 691 | unsafe | 30/30 | 30/30 | 0/30 |
| `dc-aisle9-wrapper/queue-n2` | development | candidate | 4.34 m | 145 | unsafe | 30/30 | 19/30 | 0/30 |
| `dc-aisle9-wrapper/queue-n3.5` | development | candidate | 4.49 m | 27 | unsafe | 27/30 | 3/30 | 0/30 |
| `dc-aisle9-wrapper/queue-remove` | development | candidate | 4.49 m | 20 | unsafe | 27/30 | 2/30 | 0/30 |
| `dc-aisle9-wrapper/cross-x10` | development | candidate | 2.38 m | 85 | unsafe | 29/30 | 9/30 | 0/30 |
| `dc-aisle9-wrapper/cross-x26` | development | candidate | 4.85 m | 0 | no failure found | 0/30 | 0/30 | 0/30 |
| `dc-aisle9-wrapper/cross-x30` | development | candidate | 4.85 m | 0 | no failure found | 0/30 | 0/30 | 0/30 |
| `cs-door2-exit/as-is` | held out | as is | 0.38 m | 1,767 | unsafe | 30/30 | 30/30 | 30/30 |
| `cs-door2-exit/cage-w0.5` | held out | negative control | 0.74 m | 1,546 | unsafe | 30/30 | 30/30 | 0/30 |
| `cs-door2-exit/cage-w2` | held out | candidate | 1.93 m | 865 | unsafe | 30/30 | 30/30 | 0/30 |
| `cs-door2-exit/cage-w4` | held out | candidate | 1.97 m | 745 | unsafe | 30/30 | 30/30 | 0/30 |
| `cs-door2-exit/cage-remove` | held out | candidate | 1.97 m | 744 | unsafe | 30/30 | 30/30 | 0/30 |
| `mz-stair-foot/as-is` | held out | as is | 0.31 m | 2,340 | unsafe | 30/30 | 30/30 | 30/30 |
| `mz-stair-foot/cages-e0.5` | held out | negative control | 0.31 m | 2,307 | unsafe | 30/30 | 30/30 | 30/30 |
| `mz-stair-foot/cages-e3` | held out | candidate | 0.31 m | 2,298 | unsafe | 30/30 | 30/30 | 30/30 |
| `mz-stair-foot/cages-remove` | held out | candidate | 0.31 m | 2,298 | unsafe | 30/30 | 30/30 | 30/30 |
| `mz-stair-foot/cross-x10` | held out | candidate | 5.32 m | 0 | no failure found | 0/30 | 0/30 | 0/30 |
| `mz-stair-foot/cross-x14` | held out | candidate | 3.59 m | 0 | no failure found | 17/30 | 0/30 | 0/30 |
| `mz-stair-foot/cross-x26` | held out | candidate | 6.67 m | 0 | no failure found | 0/30 | 0/30 | 0/30 |

### Envelopes used

- `dc-aisle4-crossing`: forklift_speed 1.8 to 3.3 m/s; pedestrian_speed 0.9 to 1.8 m/s; pedestrian_start_offset -15.0 to 6.0 s; reaction_time 0.75 to 1.5 s; braking_decel 1.5 to 2.5 m/s²; obstruction_shift_x -1.0 to 1.0 m; obstruction_shift_y -0.5 to 0.5 m
- `dc-aisle9-wrapper`: forklift_speed 1.4 to 2.9 m/s; pedestrian_speed 0.9 to 1.8 m/s; pedestrian_start_offset -15.0 to 6.0 s; reaction_time 0.75 to 1.5 s; braking_decel 1.1 to 2.1 m/s²; obstruction_shift_x -1.0 to 1.0 m; obstruction_shift_y -0.5 to 0.5 m
- `cs-door2-exit`: forklift_speed 1.0 to 2.5 m/s; pedestrian_speed 0.9 to 1.8 m/s; pedestrian_start_offset -6.0 to 6.0 s; reaction_time 0.75 to 1.5 s; braking_decel 0.9 to 1.9 m/s²; obstruction_shift_x -1.0 to 1.0 m; obstruction_shift_y -0.5 to 0.5 m
- `mz-stair-foot`: forklift_speed 1.6 to 3.1 m/s; pedestrian_speed 0.9 to 1.8 m/s; pedestrian_start_offset -6.0 to 6.0 s; reaction_time 0.75 to 1.5 s; braking_decel 1.5 to 2.5 m/s²; obstruction_shift_x -1.0 to 1.0 m; obstruction_shift_y -0.5 to 0.5 m
