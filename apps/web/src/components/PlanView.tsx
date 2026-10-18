import { useId } from "react";
import type { Obstruction, Point, Scenario, Trajectory } from "../types";

/**
 * Site-plan drawing of a scenario. Pure presentation: every pose, visibility flag and
 * state comes from the engine trajectory returned by the API. The only computation here
 * is the world→screen transform and drawing the footprint at the recorded pose.
 */
export interface PlanViewProps {
  scenario: Scenario;
  trajectory?: Trajectory;
  frame?: number;
  conflictZone?: Point[] | null;
  ghosts?: Obstruction[];
  ghostRoute?: Point[] | null;
  variant?: "full" | "thumb";
  pxPerM?: number;
}

const PAD = 26;

export function PlanView({
  scenario,
  trajectory,
  frame = 0,
  conflictZone,
  ghosts = [],
  ghostRoute,
  variant = "full",
  pxPerM = 24,
}: PlanViewProps) {
  const uid = useId().replace(/:/g, "");
  const thumb = variant === "thumb";
  const k = thumb ? 6 : pxPerM;
  const pad = thumb ? 4 : PAD;
  const W = scenario.bounds.width_m;
  const H = scenario.bounds.height_m;
  const sx = (x: number) => pad + x * k;
  const sy = (y: number) => pad + (H - y) * k;
  const pts = (p: Point[]) => p.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ");
  const vbW = W * k + pad * 2;
  const vbH = H * k + pad * 2;

  const i = trajectory ? Math.min(frame, trajectory.t.length - 1) : -1;
  const t = trajectory && i >= 0 ? trajectory.t[i] : 0;
  const pedPresent = trajectory ? t >= scenario.pedestrian.start_time.value - 1e-9 : false;

  const fk = scenario.forklift;
  const forkLen = Math.min(1.15, fk.length_m * 0.35);

  const pose = trajectory && i >= 0
    ? { x: trajectory.forklift_x[i], y: trajectory.forklift_y[i], h: trajectory.forklift_heading[i] }
    : (() => {
        const [a, b] = fk.route;
        return { x: a[0] + fk.length_m / 2, y: a[1], h: Math.atan2(b[1] - a[1], b[0] - a[0]) };
      })();
  const ped: Point = trajectory && i >= 0
    ? [trajectory.pedestrian_x[i], trajectory.pedestrian_y[i]]
    : scenario.pedestrian.route[0];
  const eye: Point = [
    pose.x + fk.eye_offset_m[0] * Math.cos(pose.h) - fk.eye_offset_m[1] * Math.sin(pose.h),
    pose.y + fk.eye_offset_m[0] * Math.sin(pose.h) + fk.eye_offset_m[1] * Math.cos(pose.h),
  ];
  const visible = trajectory && i >= 0 ? trajectory.pedestrian_visible[i] : false;
  const state = trajectory && i >= 0 ? trajectory.driver_state[i] : "cruise";
  const deg = (-pose.h * 180) / Math.PI;

  const trail = (xs: number[], ys: number[]) =>
    xs.slice(0, i + 1).map((x, j) => `${sx(x).toFixed(1)},${sy(ys[j]).toFixed(1)}`).join(" ");

  const obstructionShape = (o: Obstruction, ghost = false) => {
    const g = o.geometry;
    const solid = o.kind === "wall";
    return (
      <g
        key={(ghost ? "ghost-" : "") + o.id}
        transform={`translate(${sx(g.center[0])} ${sy(g.center[1])}) rotate(${-g.rotation_deg})`}
      >
        <rect
          x={(-g.length / 2) * k}
          y={(-g.width / 2) * k}
          width={g.length * k}
          height={g.width * k}
          fill={ghost ? "none" : solid ? "#4b5057" : `url(#hatch-${uid})`}
          stroke={ghost ? "#737981" : "#24272b"}
          strokeWidth={ghost ? 1 : 1.2}
          strokeDasharray={ghost ? "4 3" : undefined}
        />
      </g>
    );
  };

  return (
    <svg
      className="plan"
      viewBox={`0 0 ${vbW} ${vbH}`}
      role="img"
      aria-label={`Site plan of ${scenario.title}${trajectory ? `, t = ${t.toFixed(1)} s` : ""}`}
    >
      <defs>
        <pattern id={`hatch-${uid}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="#c9c9c1" />
          <line x1="0" y1="0" x2="0" y2="6" stroke="#5b6067" strokeWidth="1.4" />
        </pattern>
      </defs>

      <rect x={pad} y={pad} width={W * k} height={H * k} fill="var(--floor)" />

      {!thumb && (
        <g aria-hidden>
          {Array.from({ length: Math.floor(W) + 1 }, (_, x) => (
            <line key={`gx${x}`} x1={sx(x)} y1={sy(0)} x2={sx(x)} y2={sy(H)}
              stroke={x % 5 === 0 ? "#cfcfc8" : "#dbdbd5"} strokeWidth={x % 5 === 0 ? 0.8 : 0.4} />
          ))}
          {Array.from({ length: Math.floor(H) + 1 }, (_, y) => (
            <line key={`gy${y}`} x1={sx(0)} y1={sy(y)} x2={sx(W)} y2={sy(y)}
              stroke={y % 5 === 0 ? "#cfcfc8" : "#dbdbd5"} strokeWidth={y % 5 === 0 ? 0.8 : 0.4} />
          ))}
          {Array.from({ length: Math.floor(W / 5) + 1 }, (_, n) => (
            <text key={`tx${n}`} x={sx(n * 5)} y={pad - 8} fontSize="10" textAnchor="middle" fill="#737981">{n * 5}</text>
          ))}
          {Array.from({ length: Math.floor(H / 5) + 1 }, (_, n) => (
            <text key={`ty${n}`} x={pad - 6} y={sy(n * 5) + 3} fontSize="10" textAnchor="end" fill="#737981">{n * 5}</text>
          ))}
        </g>
      )}

      {/* Painted layout: aisle edge lines, crossings, walkways. Visual reference only. */}
      {scenario.layout.map((z) => {
        const xs = z.polygon.map((p) => p[0]);
        const ys = z.polygon.map((p) => p[1]);
        const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
        if (z.kind === "aisle") {
          return (
            <g key={z.id}>
              <rect x={sx(x0)} y={sy(y1)} width={(x1 - x0) * k} height={(y1 - y0) * k} fill="#dcdcd6" />
              <line x1={sx(x0)} y1={sy(y0)} x2={sx(x1)} y2={sy(y0)} stroke="var(--paint)" strokeWidth={thumb ? 1 : 2.4} />
              <line x1={sx(x0)} y1={sy(y1)} x2={sx(x1)} y2={sy(y1)} stroke="var(--paint)" strokeWidth={thumb ? 1 : 2.4} />
              {!thumb && <text x={sx(x0) + 6} y={sy(y1) + 13} fontSize="10.5" fill="#737981">{z.label}</text>}
            </g>
          );
        }
        if (z.kind === "crossing") {
          const vertical = y1 - y0 > x1 - x0;
          const n = Math.max(2, Math.floor((vertical ? y1 - y0 : x1 - x0) / 0.6));
          return (
            <g key={z.id}>
              {Array.from({ length: n }, (_, s) => {
                const f = (s + 0.25) / n;
                return vertical ? (
                  <rect key={s} x={sx(x0)} y={sy(y0 + f * (y1 - y0)) - 0.15 * k} width={(x1 - x0) * k} height={0.3 * k} fill="var(--paint)" opacity="0.85" />
                ) : (
                  <rect key={s} x={sx(x0 + f * (x1 - x0))} y={sy(y1)} width={0.15 * k} height={(y1 - y0) * k} fill="var(--paint)" opacity="0.85" />
                );
              })}
            </g>
          );
        }
        if (z.kind === "walkway") {
          return <rect key={z.id} x={sx(x0)} y={sy(y1)} width={(x1 - x0) * k} height={(y1 - y0) * k} fill="none" stroke="var(--paint)" strokeWidth={thumb ? 0.8 : 1.6} strokeDasharray="6 4" />;
        }
        return !thumb && z.kind === "dock" ? (
          <text key={z.id} x={sx(x0) + 6} y={sy(y1) + 13} fontSize="10.5" fill="#8a8f96">{z.label}</text>
        ) : null;
      })}

      {/* Planned routes */}
      <polyline points={pts(fk.route)} fill="none" stroke="#24272b" strokeWidth="1" strokeDasharray="2 4" opacity="0.6" />
      <polyline points={pts(scenario.pedestrian.route)} fill="none" stroke="var(--hivis)" strokeWidth={thumb ? 1 : 1.4} strokeDasharray="5 4" />
      {ghostRoute && <polyline points={pts(ghostRoute)} fill="none" stroke="#737981" strokeWidth="1" strokeDasharray="3 4" />}

      {ghosts.map((o) => obstructionShape(o, true))}
      {scenario.obstructions.map((o) => obstructionShape(o))}
      {!thumb && scenario.obstructions.filter((o) => o.kind !== "wall" || o.geometry.length < 4).map((o) => (
        <text key={`l-${o.id}`} x={sx(o.geometry.center[0])} y={sy(o.geometry.center[1] - o.geometry.width / 2) + 12}
          fontSize="10" textAnchor="middle" fill="#4b5057">{o.label}</text>
      ))}

      {!thumb && conflictZone && (
        <polygon points={pts(conflictZone)} fill="none" stroke="var(--accent)" strokeWidth="1" strokeDasharray="1.5 2.5" />
      )}

      {trajectory && i > 0 && (
        <>
          <polyline points={trail(trajectory.forklift_x, trajectory.forklift_y)} fill="none" stroke="#24272b" strokeWidth="2" opacity="0.28" />
          <polyline points={trail(trajectory.pedestrian_x, trajectory.pedestrian_y)} fill="none" stroke="var(--hivis)" strokeWidth="2" opacity="0.45" />
        </>
      )}

      {/* Driver sight line, as reported by the engine at this frame. */}
      {!thumb && trajectory && pedPresent && (
        <line x1={sx(eye[0])} y1={sy(eye[1])} x2={sx(ped[0])} y2={sy(ped[1])}
          stroke={visible ? "var(--accent)" : "#8a8f96"} strokeWidth={visible ? 1.4 : 1}
          strokeDasharray={visible ? undefined : "3 3"} />
      )}

      {/* Forklift footprint at the recorded pose */}
      <g transform={`translate(${sx(pose.x)} ${sy(pose.y)}) rotate(${deg})`}>
        <rect x={(-fk.length_m / 2) * k} y={(-fk.width_m / 2) * k} width={(fk.length_m - forkLen) * k} height={fk.width_m * k}
          fill="#2f3337" stroke="#24272b" rx={thumb ? 0.5 : 2} />
        <rect x={(fk.length_m / 2 - forkLen) * k} y={(-0.36) * k} width={forkLen * k} height={0.12 * k} fill="#2f3337" />
        <rect x={(fk.length_m / 2 - forkLen) * k} y={(0.24) * k} width={forkLen * k} height={0.12 * k} fill="#2f3337" />
        {!thumb && <circle cx={fk.eye_offset_m[0] * k} cy={-fk.eye_offset_m[1] * k} r={2.6} fill="#fff" />}
      </g>
      {!thumb && trajectory && state !== "cruise" && state !== "finished" && (
        <text x={sx(pose.x)} y={sy(pose.y) - (fk.width_m / 2) * k - 6} fontSize="10.5" textAnchor="middle"
          fill={state === "braking" || state === "reacting" ? "var(--near)" : "#24272b"} fontWeight="600">{state}</text>
      )}

      {/* Pedestrian */}
      {(pedPresent || !trajectory) && (
        <circle cx={sx(ped[0])} cy={sy(ped[1])} r={scenario.pedestrian.radius_m * k}
          fill="var(--hivis)" stroke="#7a3a05" strokeWidth={thumb ? 0.6 : 1.2} />
      )}

      {!thumb && (
        <g aria-hidden>
          <line x1={sx(0.5)} y1={sy(0) - 10} x2={sx(5.5)} y2={sy(0) - 10} stroke="#24272b" strokeWidth="2" />
          <line x1={sx(0.5)} y1={sy(0) - 14} x2={sx(0.5)} y2={sy(0) - 6} stroke="#24272b" />
          <line x1={sx(5.5)} y1={sy(0) - 14} x2={sx(5.5)} y2={sy(0) - 6} stroke="#24272b" />
          <text x={sx(3)} y={sy(0) - 16} fontSize="10" textAnchor="middle" fill="#24272b">5 m</text>
          <g transform={`translate(${sx(W) - 14} ${sy(H) + 18})`}>
            <path d="M0 -10 L5 4 L0 1 L-5 4 Z" fill="#24272b" />
            <text y="16" fontSize="10" textAnchor="middle" fill="#24272b">N</text>
          </g>
        </g>
      )}
    </svg>
  );
}
