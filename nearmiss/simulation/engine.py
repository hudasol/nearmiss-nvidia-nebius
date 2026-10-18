"""Deterministic, time-stepped forklift–pedestrian crossing simulator.

Model summary (see docs/simulation-model.md for the full statement of assumptions):

* Planar kinematics. The forklift is an oriented rectangle whose reference point follows
  its route by arc length; the pedestrian is a disc following its route.
* Forklift longitudinal dynamics: constant acceleration up to cruise speed, constant
  braking deceleration down to zero. Integration is exact for piecewise-constant
  acceleration within a step, including the instant the vehicle comes to rest.
* Driver perception: the pedestrian is *visible* when within detection range and field of
  view and at least one of three sample points across the pedestrian's width has an
  unobstructed sight line from the driver's eye past every opaque obstruction.
* Driver decision: a visible pedestrian is a *hazard* when its current or constant-velocity
  predicted position (over the prediction horizon) lies inside the forklift's forward
  corridor. Braking starts one perception-reaction time after the first hazard
  detection. The forklift holds at rest while the pedestrian is in the corridor and
  resumes after the corridor has been clear for ``resume_clear_time_s``.
* Pedestrian: not observable before its start time, then walks at constant speed and does
  not yield to an approaching vehicle (the distracted-pedestrian incident type). It waits
  rather than walk into the flank of a vehicle that is already alongside, keeping a 0.3 m
  stand-off. Contact can therefore only come from the vehicle's front striking the
  pedestrian, which is the forward-collision mechanism this scenario studies.

The engine records poses only. It does not compute safety metrics; the evaluation layer
derives every metric independently from the recorded trajectory.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from nearmiss.domain import geometry as gm
from nearmiss.domain.models import ForkliftSpec, Scenario

ENGINE_VERSION = "nearmiss-kinematic/1.0.0"

#: Below this speed the forklift counts as stationary for the pedestrian.
STATIONARY_SPEED = 0.05
#: Time a pedestrian waits at a stationary vehicle before walking around it.
DETOUR_HESITATION_S = 0.5
#: Gap the pedestrian keeps from a vehicle it would otherwise walk into.
PEDESTRIAN_STANDOFF_M = 0.3

DriverState = Literal["cruise", "reacting", "braking", "stopped", "resuming", "finished"]
EventKind = Literal[
    "pedestrian_started",
    "pedestrian_first_visible",
    "hazard_detected",
    "braking_started",
    "forklift_stopped",
    "forklift_resumed",
    "pedestrian_detour",
]


class EngineEvent(BaseModel):
    t: float
    kind: EventKind
    detail: str = ""


class Trajectory(BaseModel):
    """Columnar trajectory sampled at every engine step."""

    engine_version: str
    scenario_id: str
    config_hash: str
    dt: float
    t: list[float]
    forklift_x: list[float]
    forklift_y: list[float]
    forklift_heading: list[float]
    forklift_speed: list[float]
    driver_state: list[str]
    pedestrian_x: list[float]
    pedestrian_y: list[float]
    pedestrian_visible: list[bool]
    hazard: list[bool]
    events: list[EngineEvent]
    trajectory_hash: str = ""

    def downsample(self, every: int) -> Trajectory:
        """Every ``every``-th frame (always keeping the last) for display."""
        idx = list(range(0, len(self.t), every))
        if idx[-1] != len(self.t) - 1:
            idx.append(len(self.t) - 1)
        cols = [
            "t",
            "forklift_x",
            "forklift_y",
            "forklift_heading",
            "forklift_speed",
            "driver_state",
            "pedestrian_x",
            "pedestrian_y",
            "pedestrian_visible",
            "hazard",
        ]
        data = {c: [getattr(self, c)[i] for i in idx] for c in cols}
        return self.model_copy(update=data)


def canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scenario_hash(scenario: Scenario) -> str:
    return sha256_hex(canonical_json(scenario.model_dump(mode="json")))


def trajectory_hash(traj: Trajectory) -> str:
    """Hash of the trajectory rounded to micrometres / microseconds.

    Rounding makes the hash robust to last-ulp differences in libm trigonometry across
    platforms while still detecting any physically meaningful change.
    """

    def r(xs: list[float]) -> list[float]:
        return [round(x, 6) for x in xs]

    payload = {
        "engine": traj.engine_version,
        "config": traj.config_hash,
        "t": r(traj.t),
        "fx": r(traj.forklift_x),
        "fy": r(traj.forklift_y),
        "fh": r(traj.forklift_heading),
        "fv": r(traj.forklift_speed),
        "px": r(traj.pedestrian_x),
        "py": r(traj.pedestrian_y),
        "vis": traj.pedestrian_visible,
        "hz": traj.hazard,
        "st": traj.driver_state,
        "ev": [[round(e.t, 6), e.kind] for e in traj.events],
    }
    return sha256_hex(canonical_json(payload))


@dataclass
class _ForkliftState:
    s: float
    v: float
    state: DriverState = "cruise"
    hazard_since: float | None = None
    brake_at: float | None = None
    clear_since: float | None = None
    stopped_logged: bool = False


@dataclass
class _Perception:
    visible_ever: bool = False
    events: list[EngineEvent] = field(default_factory=list)


def _advance(s: float, v: float, a: float, dt: float, v_cap: float) -> tuple[float, float]:
    """Exact integration of constant acceleration over ``dt`` with clamping at 0 and cap."""
    if a < 0.0:
        t_stop = v / -a if a != 0 else math.inf
        if t_stop <= dt:
            return s + v * t_stop + 0.5 * a * t_stop * t_stop, 0.0
        return s + v * dt + 0.5 * a * dt * dt, v + a * dt
    if a > 0.0:
        t_cap = (v_cap - v) / a
        if t_cap <= dt:
            return s + v * t_cap + 0.5 * a * t_cap * t_cap + v_cap * (dt - t_cap), v_cap
        return s + v * dt + 0.5 * a * dt * dt, v + a * dt
    return s + v * dt, v


def _is_visible(
    eye: gm.Point,
    heading: float,
    ped: gm.Point,
    radius: float,
    detection_range: float,
    half_fov: float,
    blockers: list[gm.Polygon],
) -> bool:
    d = gm.distance(eye, ped)
    if d > detection_range or d < 1e-9:
        return d < 1e-9
    bearing = math.atan2(ped[1] - eye[1], ped[0] - eye[0])
    rel = (bearing - heading + math.pi) % (2 * math.pi) - math.pi
    if abs(rel) > half_fov:
        return False
    # Sample the pedestrian's left edge, centre and right edge as seen from the eye.
    ux, uy = (ped[0] - eye[0]) / d, (ped[1] - eye[1]) / d
    nx, ny = -uy, ux
    samples = [
        ped,
        (ped[0] + nx * radius, ped[1] + ny * radius),
        (ped[0] - nx * radius, ped[1] - ny * radius),
    ]
    for p in samples:
        if not any(gm.segment_blocked_by_polygon(eye, p, poly) for poly in blockers):
            return True
    return False


def _detour_route(
    route: gm.Polyline,
    s: float,
    center: gm.Point,
    heading: float,
    fk: ForkliftSpec,
    radius: float,
) -> gm.Polyline | None:
    """Route around a stationary vehicle, rejoining the original route beyond it.

    The pedestrian passes in front of the vehicle if it is ahead of the vehicle's centre,
    behind it otherwise, at the stand-off distance plus 0.1 m.
    """
    pos, _ = route.pose_at(s)
    lx, ly = gm.to_local(pos, center, heading)
    clear = radius + PEDESTRIAN_STANDOFF_M + 0.1
    side_x = (fk.length_m / 2.0 + clear) * (1.0 if lx >= 0 else -1.0)
    far_y = (fk.width_m / 2.0 + clear) * (-1.0 if ly >= 0 else 1.0)
    # First point on the remaining route that is on the far side and clear of the vehicle.
    rejoin_s: float | None = None
    probe = s
    while probe < route.length:
        probe = min(route.length, probe + 0.1)
        p, _ = route.pose_at(probe)
        plx, ply = gm.to_local(p, center, heading)
        sep = gm.rectangle_disc_separation(center, fk.length_m, fk.width_m, heading, p, radius)
        if sep > PEDESTRIAN_STANDOFF_M + 0.1 and ply * far_y > 0:
            rejoin_s = probe
            break
    if rejoin_s is None:
        return None
    waypoints = [pos, gm.to_world((side_x, ly), center, heading)]
    waypoints.append(gm.to_world((side_x, far_y), center, heading))
    rejoin, _ = route.pose_at(rejoin_s)
    waypoints.append(rejoin)
    for i, cum in enumerate(route.cumulative):
        if cum > rejoin_s + 1e-6:
            waypoints.append(route.points[i])
    cleaned: list[gm.Point] = []
    for p in waypoints:
        if not cleaned or gm.distance(p, cleaned[-1]) > 1e-6:
            cleaned.append(p)
    return gm.Polyline(cleaned) if len(cleaned) >= 2 else None


def _in_corridor(
    p: gm.Point,
    center: gm.Point,
    heading: float,
    rear_extent: float,
    ahead: float,
    half_width: float,
) -> bool:
    lx, ly = gm.to_local(p, center, heading)
    return -rear_extent <= lx <= ahead and abs(ly) <= half_width


def simulate(scenario: Scenario) -> Trajectory:
    """Run the scenario and return the full-rate trajectory."""
    fk = scenario.forklift
    ped = scenario.pedestrian
    dt = scenario.simulation.dt_s
    n_steps = round(scenario.simulation.duration_s / dt)

    fk_route = gm.Polyline(fk.route)
    ped_route = gm.Polyline(ped.route)
    blockers = [o.geometry.polygon() for o in scenario.obstructions if o.opaque]

    v_cruise = fk.cruise_speed.value
    decel = fk.braking_decel.value
    accel = fk.max_accel.value
    reaction = fk.reaction_time.value
    ped_speed = ped.walking_speed.value
    ped_start = ped.start_time.value
    half_fov = math.radians(fk.field_of_view_deg) / 2.0
    corridor_half_width = fk.width_m / 2.0 + ped.radius_m + fk.hazard_lateral_margin_m
    horizon = fk.prediction_horizon_s
    pred_steps = max(1, round(horizon / 0.25))

    state = _ForkliftState(s=0.0, v=v_cruise)
    perception = _Perception()
    ped_started_logged = False
    ped_s = 0.0
    ped_moving = False
    ped_blocked_since: float | None = None

    cols: dict[str, list[float]] = {k: [] for k in ("t", "fx", "fy", "fh", "fv", "px", "py")}
    states: list[str] = []
    visible_col: list[bool] = []
    hazard_col: list[bool] = []

    for step in range(n_steps + 1):
        t = step * dt
        center, heading = fk_route.pose_at(state.s)
        ped_pos, ped_heading = ped_route.pose_at(ped_s)
        ped_present = t >= ped_start - 1e-9
        if not ped_started_logged and ped_present:
            perception.events.append(EngineEvent(t=t, kind="pedestrian_started"))
            ped_started_logged = True

        # Pedestrian velocity: route tangent while walking, zero while waiting.
        if ped_present and ped_moving:
            ped_vel = (ped_speed * math.cos(ped_heading), ped_speed * math.sin(ped_heading))
        else:
            ped_vel = (0.0, 0.0)

        eye = gm.to_world(fk.eye_offset_m, center, heading)
        # Before its start time the pedestrian is still inside the office: not observable.
        visible = ped_present and _is_visible(
            eye, heading, ped_pos, ped.radius_m, fk.detection_range_m, half_fov, blockers
        )
        if visible and not perception.visible_ever:
            perception.visible_ever = True
            perception.events.append(EngineEvent(t=t, kind="pedestrian_first_visible"))

        # Forward corridor: stopping distance at current speed plus a 3 m buffer, never
        # shorter than one vehicle length ahead of the front bumper.
        stop_dist = state.v * reaction + state.v * state.v / (2.0 * decel)
        ahead = fk.length_m / 2.0 + max(stop_dist + 3.0, fk.length_m)
        rear = fk.length_m / 2.0
        in_corridor_now = _in_corridor(ped_pos, center, heading, rear, ahead, corridor_half_width)
        hazard = False
        if visible:
            if in_corridor_now:
                hazard = True
            else:
                for k in range(1, pred_steps + 1):
                    tau = k * horizon / pred_steps
                    future_fk_center, _ = fk_route.pose_at(state.s + state.v * tau)
                    future_ped = (ped_pos[0] + ped_vel[0] * tau, ped_pos[1] + ped_vel[1] * tau)
                    # Predicted conflict: pedestrian will be in the lane the forklift
                    # occupies at that future time (front-to-rear plus margin).
                    if _in_corridor(
                        future_ped,
                        future_fk_center,
                        heading,
                        rear + 1.0,
                        fk.length_m / 2.0 + 1.0,
                        corridor_half_width,
                    ):
                        hazard = True
                        break

        # --- driver state machine -------------------------------------------------
        if state.state == "cruise" or state.state == "resuming":
            if hazard:
                state.state = "reacting"
                state.hazard_since = t
                state.brake_at = t + reaction
                perception.events.append(EngineEvent(t=t, kind="hazard_detected"))
        if state.state == "reacting" and state.brake_at is not None and t >= state.brake_at - 1e-9:
            state.state = "braking"
            perception.events.append(
                EngineEvent(t=t, kind="braking_started", detail=f"v={state.v:.2f} m/s")
            )
        if state.state == "braking" and state.v <= 1e-9:
            state.state = "stopped"
            state.clear_since = None
            perception.events.append(EngineEvent(t=t, kind="forklift_stopped"))
        if state.state == "stopped":
            # The driver stays stopped while the pedestrian is in the lane ahead, or is
            # visible and predicted to enter it within the prediction horizon.
            # The lane checked is the full stopping corridor at cruise speed.
            cruise_stop = v_cruise * reaction + v_cruise * v_cruise / (2.0 * decel)
            resume_ahead = fk.length_m / 2.0 + max(cruise_stop + 3.0, fk.length_m)
            occupied = _in_corridor(
                ped_pos, center, heading, rear, resume_ahead, corridor_half_width
            )
            if not occupied and visible and (ped_vel[0] or ped_vel[1]):
                for k in range(1, pred_steps + 1):
                    tau = k * horizon / pred_steps
                    future_ped = (ped_pos[0] + ped_vel[0] * tau, ped_pos[1] + ped_vel[1] * tau)
                    if _in_corridor(
                        future_ped, center, heading, rear, resume_ahead, corridor_half_width
                    ):
                        occupied = True
                        break
            if occupied:
                state.clear_since = None
            else:
                if state.clear_since is None:
                    state.clear_since = t
                if t - state.clear_since >= fk.resume_clear_time_s - 1e-9:
                    state.state = "resuming"
                    perception.events.append(EngineEvent(t=t, kind="forklift_resumed"))

        if state.s >= fk_route.length - 1e-9:
            state.state = "finished"

        # --- record ---------------------------------------------------------------
        cols["t"].append(round(t, 9))
        cols["fx"].append(center[0])
        cols["fy"].append(center[1])
        cols["fh"].append(heading)
        cols["fv"].append(state.v)
        cols["px"].append(ped_pos[0])
        cols["py"].append(ped_pos[1])
        states.append(state.state)
        visible_col.append(visible)
        hazard_col.append(hazard)

        # --- integrate ------------------------------------------------------------
        if state.state in ("braking",):
            a = -decel
        elif state.state == "stopped" or state.state == "finished":
            a = 0.0
        else:
            a = accel if state.v < v_cruise else 0.0
        if state.state == "stopped":
            state.v = 0.0
        else:
            state.s, state.v = _advance(state.s, state.v, a, dt, v_cruise)
            state.s = min(state.s, fk_route.length)

        # Pedestrian: walks at constant speed and does not yield to an approaching vehicle
        # (distracted, non-yielding). It never walks into a stationary vehicle or into the
        # flank of a vehicle already alongside: it keeps a stand-off and, if the vehicle is
        # stationary, walks around it on a deterministic detour.
        t_next = (step + 1) * dt
        ped_moving = False
        if t_next >= ped_start - 1e-9 and ped_s < ped_route.length:
            walk_time = min(dt, t_next - ped_start)
            candidate_s = min(ped_route.length, ped_s + ped_speed * walk_time)
            cand_pos, _ = ped_route.pose_at(candidate_s)
            next_center, next_heading = fk_route.pose_at(state.s)
            stationary = state.v < STATIONARY_SPEED
            alongside = abs(gm.to_local(cand_pos, next_center, next_heading)[0]) <= (
                fk.length_m / 2.0
            )
            too_close = (
                gm.rectangle_disc_separation(
                    next_center, fk.length_m, fk.width_m, next_heading, cand_pos, ped.radius_m
                )
                < PEDESTRIAN_STANDOFF_M
            )
            blocked = too_close and (stationary or alongside)
            # After contact the pedestrian does not try to walk out of the footprint.
            overlapping = (
                gm.rectangle_disc_separation(
                    next_center, fk.length_m, fk.width_m, next_heading, ped_pos, ped.radius_m
                )
                <= 0.0
            )
            if not blocked:
                ped_s = candidate_s
                ped_moving = True
                ped_blocked_since = None
            elif stationary and not overlapping:
                if ped_blocked_since is None:
                    ped_blocked_since = t_next
                elif t_next - ped_blocked_since >= DETOUR_HESITATION_S - 1e-9:
                    detour = _detour_route(
                        ped_route, ped_s, next_center, next_heading, fk, ped.radius_m
                    )
                    if detour is not None:
                        ped_route, ped_s = detour, 0.0
                        perception.events.append(
                            EngineEvent(t=t_next, kind="pedestrian_detour")
                        )
                    ped_blocked_since = None
            else:
                ped_blocked_since = None

    traj = Trajectory(
        engine_version=ENGINE_VERSION,
        scenario_id=scenario.id,
        config_hash=scenario_hash(scenario),
        dt=dt,
        t=cols["t"],
        forklift_x=cols["fx"],
        forklift_y=cols["fy"],
        forklift_heading=cols["fh"],
        forklift_speed=cols["fv"],
        driver_state=states,
        pedestrian_x=cols["px"],
        pedestrian_y=cols["py"],
        pedestrian_visible=visible_col,
        hazard=hazard_col,
        events=perception.events,
    )
    traj.trajectory_hash = trajectory_hash(traj)
    return traj
