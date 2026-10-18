# NearMiss design system

NearMiss is used by safety engineers who investigate an incident, test a layout change and
defend the result in a review. The interface is designed as an instrument for that work,
not as a dashboard.

## References and what we took from them

| Reference type | Principle taken |
| --- | --- |
| Architectural and site drawings | Hatched solids for physical obstructions, dimension grid, scale bar and north arrow, painted markings drawn as they appear on the floor. |
| Industrial control and test-bench software | One live view with readouts underneath; transport controls; numbers with units, tabular figures. |
| Scientific visualisation | Marks encode real data only; thresholds drawn as reference lines; small multiples to compare methods on identical axes. |
| Engineering review documents | Reports read top-down (finding, claim, method, evidence, limits), printable, every number traceable. |

## Palette

| Token | Hex | Use |
| --- | --- | --- |
| `--surface` | `#F2F2EF` | Page: light concrete, cool-neutral rather than cream |
| `--floor` | `#E4E4DF` | Plan floor |
| `--ink` | `#24272B` | Graphite text, forklift footprint |
| `--accent` | `#145E73` | Petrol ink: interaction, selection, the driver's sight line |
| `--paint` | `#D9A400` | Floor paint. Used only on the plan, never as UI chrome |
| `--hivis` | `#F07F13` | Pedestrian. Used only on the plan |
| `--clear` / `--near` / `--collision` | `#2F6B3F` / `#C4421F` / `#8E1B12` | Run outcomes and decisions only |
| `--caution` | `#96620A` | Estimated and assumed values |

Strong colours carry state, never decoration. Outcome tags pair colour with text, and the stress
map pairs it with shape (○ clear, × unsafe), so nothing relies on colour alone.

## Type

Barlow and Barlow Semi Condensed, a family derived from highway and industrial signage. Body
15 px, headings 600 weight, condensed face for labels and table headers, tabular figures
everywhere. Labels are sentence case. A monospace face appears only for hashes and
machine identifiers, which are genuinely code.

## Layout

- **Incident workspace**: evidence on the left (report, model interpretation, parameters with
  provenance), the plan instrument on the right with the replay or the experiment builder below.
- **Experiment**: a decision matrix (arm × workflow), then small multiples of the stress map
  (one per workflow, same axes), then the run table.
- **Run**: conditions, metrics and the reproduction check beside the instrument; optional second
  instrument with the as-is layout under the same conditions.
- **Report**: a single printable document column.

## Rules

1. The plan shows only engine output: poses, visibility flags and driver states come from the
   trajectory. The browser computes no physics and no outcomes.
2. Observed, estimated and assumed values are always tagged.
3. Model output is boxed and attributed with model ID, latency and tokens, and is never
   presented as a result.
4. "No failure found" is never shortened to "safe".
5. Cards are used only for real groupings; tables carry technical comparisons.
6. Motion only for simulation playback and running-job progress; reduced motion respected.
