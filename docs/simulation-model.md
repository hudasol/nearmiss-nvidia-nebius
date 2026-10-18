# Simulation model

NearMiss uses a bounded, deterministic planar simulator (`nearmiss/simulation/engine.py`,
version `nearmiss-kinematic/1.0.0`). This page states what it models, what it assumes and
what it leaves out. Every report links here.

## Frame, units and time

- Site-plan frame: x east, y north, metres, origin at the south-west corner of the plan
  extract. Headings are radians counter-clockwise from +x.
- Fixed timestep `dt` (default 0.02 s, i.e. 50 Hz) over a fixed duration (default 24 s).
- Scenario parameters are `Param` values with unit and provenance: **observed** (measured
  or dimensioned on a drawing), **estimated** (derived from telematics, CCTV timing or a
  statement, with an uncertainty) or **assumed** (engineering default, not measured).
  Stress conditions overwrite values and mark them **assumed / stress condition**.

## Actors and geometry

| Element | Geometry | Notes |
| --- | --- | --- |
| Forklift | Oriented rectangle, overall length incl. forks × width | Reference point is the rectangle centre; it follows the route by arc length. |
| Pedestrian | Disc, radius 0.3 m | Follows its route by arc length. |
| Obstructions | Oriented rectangles | Opaque obstructions block sight lines. Movable ones (pallet stacks, queues) are shifted by placement stress; fixed ones (columns, equipment) are not. |
| Layout zones | Polygons | Drawing reference only; not used in physics. |

## Forklift motion

- Starts at the beginning of its route at cruise speed.
- Longitudinal dynamics with piecewise-constant acceleration: `+max_accel` up to cruise
  speed, `−braking_decel` when braking, zero otherwise. Integration within a step is exact,
  including the instant the truck comes to rest, so braking distance equals `v²/2a`
  (verified in `tests/test_engine.py`).
- No lateral dynamics, steering, tyre slip, load sway or tip-over.

## Driver perception and decision

1. **Visibility.** The pedestrian is visible when it is within `detection_range_m`, inside
   the forward field of view, and at least one of three sample points across its width has
   an unobstructed straight sight line from the driver's eye past every opaque
   obstruction. Obstructions are treated as taller than eye height (2-D occlusion).
2. **Hazard.** A visible pedestrian is a hazard when it is in the forward corridor now
   (corridor half-width = truck half-width + pedestrian radius + margin; length = current
   stopping distance + 3 m), or when its constant-velocity prediction over the prediction
   horizon (sampled every 0.25 s) falls inside the truck's predicted footprint ± 1 m.
3. **Reaction.** Braking starts one perception-reaction time after the first hazard.
   Once committed, the driver brakes to a stop.
4. **Resume.** The truck stays stopped while the pedestrian is in the lane ahead (full
   cruise stopping corridor), or is visible and predicted to enter it, and resumes after
   the lane has been clear for `resume_clear_time_s`.

## Pedestrian behaviour

- Not observable before its start time (still inside the origin doorway).
- Walks its route at constant speed and **does not yield to an approaching vehicle**:
  the distracted-pedestrian incident type that near-miss reports most often describe.
- Never walks into a stationary vehicle or into the flank of a vehicle already alongside;
  it keeps a 0.3 m stand-off and, after 0.5 s, walks around a stationary truck on a
  deterministic detour. Contact can therefore only come from a moving truck's front.

## Metrics (computed independently by `nearmiss/evaluation/metrics.py`)

| Metric | Unit | Definition | Used for decision |
| --- | --- | --- | --- |
| `min_moving_separation_m` | m | Minimum footprint clearance while the truck moves (≥ 0.1 m/s) and the pedestrian is ahead of or beside it | **Yes** |
| `collision` | — | Footprints touch or overlap at any instant | **Yes** |
| `min_separation_m` | m | Minimum clearance at any instant | No (diagnostic) |
| `min_ttc_s` | s | Constant-velocity time-to-collision on a 0.1 s grid, 4 s horizon | No (diagnostic; ignores braking) |
| `pet_s` | s | Post-encroachment time at the route-crossing conflict zone | No (diagnostic) |

The evaluator reads recorded poses only. It does not use the engine's visibility or
hazard flags, so a fault in the driver model cannot mark its own run safe.

## Reproducibility

- Same scenario → bit-identical trajectory (`tests/test_engine.py`).
- Each run stores `config_hash` (SHA-256 of the canonical scenario JSON) and
  `trajectory_hash` (SHA-256 of the trajectory rounded to 1 µm / 1 µs, which absorbs
  last-ulp libm differences across platforms).
- Halving the timestep changes the decision metric by less than 0.05 m on the reference
  scenario (`test_timestep_convergence`).

## Out of scope (stated limitations)

- 3-D occlusion (obstructions lower than eye height, mast and overhead-guard blind spots).
- Driver steering or swerving, horn use, pedestrian evasive action or eye contact.
- Multiple vehicles or pedestrians, reversing, rear-swing of counterbalance trucks.
- Sensor noise and lighting. Visibility is geometric, not perceptual.
- Floor friction variation beyond the braking-deceleration stress range.

NearMiss is a simulation-based decision-support tool. It is not a certified safety system
and does not replace a site risk assessment.
