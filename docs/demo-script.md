# Demo video script (target 2:45, limit 3:00)

Record the running application at 1440 × 900 with the API serving the production build
(`python -m nearmiss.api.app`, http://127.0.0.1:8000). Every number on screen comes from the
running system; nothing is mocked. About two minutes of the video shows the application modules
in action, which covers the Physical AI track's "key application modules in action" requirement.

Before recording: run one experiment on `dc-aisle4-crossing` with "Move staged pallet stack A 3 m
west" and "Move the crossing to x = 27 m" (seed 1, 48 runs) so a completed result is ready, and
check that the Integration page shows a successful Nemotron call. Use a fresh database
(`NEARMISS_DB_PATH=var/demo.sqlite3`) so the experiment list is clean.

| Time | Screen | Action | Narration (keep it close to this) |
| --- | --- | --- | --- |
| 0:00–0:12 | Incidents | Show the list of four incidents. | "A forklift stopped a third of a metre from a picker who stepped out from behind staged pallets. The usual fix is to move the pallets. NearMiss tests whether that fix still works when the day is different." |
| 0:12–0:40 | Incident INC-2026-0412, replay | Press Play. Let the sight line turn from dashed to solid as the picker emerges; pause at closest approach. | "This is the incident rebuilt as a calibrated simulation: truck footprint, braking, driver reaction, and the driver's line of sight. The sight line is blocked until the picker clears the stack. The replay reproduces the reported 0.3 metres. Every input is tagged observed, estimated or assumed." |
| 0:40–0:58 | Report interpretation | Click Interpret with Nemotron; show the parameter table with quotes and one downgraded value. | "NVIDIA Nemotron, running on Nebius Token Factory, reads the report and proposes inputs. Each must quote the report; a number that isn't in its quote gets downgraded to an estimate. The model proposes; it never decides." |
| 0:58–1:20 | Design an experiment | Add "Move stack 3 m west"; Preview: clearance 3.41 m. Add "Move the crossing to x = 27 m". Untick arrival timing, Check experiment. | "Moving the stack three metres clears the replay with 3.4 metres to spare. Looks fixed. Now I'll make a methodological mistake and stop varying arrival timing. The Safety Change Compiler rejects the experiment and says why, before any simulation runs." |
| 1:20–1:30 | Same | Apply corrections, show "Experiment is valid", open the completed experiment. | "Apply the correction. Each change runs against the as-is layout under identical conditions, with two workflows and the same budget." |
| 1:30–2:05 | Experiment results | Decision matrix, then the two stress maps side by side. | "Both changes clear the replayed incident, and both fail under stress. For the moved crossing, the conventional parameter sweep found no failure in 48 runs. NearMiss' targeted search found four, because it solves for the arrival timing where the truck can't stop in time and concentrates runs there. You can see it in where each workflow looked." |
| 2:05–2:25 | Run viewer | Open the worst run; play it; show the reproduction check (both hashes match); click Compare with as-is layout. | "Every failure is a replayable run. NearMiss re-simulates it from its stored configuration and checks the hashes. Here the truck is a little faster and the picker a little earlier, and the moved crossing still produces a near miss." |
| 2:25–2:40 | Evidence report | Scroll Findings, Failure cases, Limitations, Model-generated content. | "The evidence report is generated from stored runs: findings, failure cases, metric definitions, assumptions and limitations. Model output is listed separately and never decides an outcome." |
| 2:40–2:48 | Benchmark | Show the held-out and development tables. | "We preregistered a benchmark. On development scenarios, targeted search caught 95 percent of unsafe changes against 39 percent for the conventional sweep. On held-out scenarios both caught every one, so our hypothesis wasn't supported there, and we report that." |

Closing card (2 s, optional): "NearMiss · Nemotron on Nebius Token Factory · Apache-2.0".
Do not add music or third-party logos.
