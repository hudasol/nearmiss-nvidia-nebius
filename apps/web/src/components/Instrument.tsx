import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Obstruction, Point, SimulationPayload } from "../types";
import { PlanView } from "./PlanView";
import { OutcomeTag } from "./Tags";
import { fmt, minSeparationFrame } from "../format";

const EVENT_LABEL: Record<string, string> = {
  pedestrian_started: "Pedestrian sets off",
  pedestrian_first_visible: "First in driver's view",
  hazard_detected: "Hazard detected",
  braking_started: "Braking",
  forklift_stopped: "Stopped",
  forklift_resumed: "Resumed",
  pedestrian_detour: "Pedestrian walks around truck",
};
const TIMELINE_EVENTS = new Set(["hazard_detected", "braking_started", "forklift_stopped"]);

/** Plan view + clearance timeline + transport, all driven by one simulation payload. */
export function Instrument({
  data,
  title,
  ghosts,
  ghostRoute,
  threshold,
  actions,
  autoplay = false,
}: {
  data: SimulationPayload;
  title: ReactNode;
  ghosts?: Obstruction[];
  ghostRoute?: Point[] | null;
  threshold: number;
  actions?: ReactNode;
  autoplay?: boolean;
}) {
  const traj = data.trajectory;
  const n = traj.t.length;
  const minIdx = minSeparationFrame(data);
  const [frame, setFrame] = useState(autoplay ? 0 : minIdx);
  const [playing, setPlaying] = useState(autoplay);
  const [speed, setSpeed] = useState(1);
  const raf = useRef<number | null>(null);
  const last = useRef<number | null>(null);
  const acc = useRef(0);

  useEffect(() => {
    setFrame(autoplay ? 0 : minSeparationFrame(data));
    setPlaying(autoplay);
  }, [data, autoplay]);

  useEffect(() => {
    if (!playing) return;
    const dt = traj.t[1] - traj.t[0];
    const step = (ts: number) => {
      if (last.current != null) acc.current += ((ts - last.current) / 1000) * speed;
      last.current = ts;
      if (acc.current >= dt) {
        const adv = Math.floor(acc.current / dt);
        acc.current -= adv * dt;
        setFrame((f) => {
          const nf = f + adv;
          if (nf >= n - 1) {
            setPlaying(false);
            return n - 1;
          }
          return nf;
        });
      }
      raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
      last.current = null;
      acc.current = 0;
    };
  }, [playing, speed, n, traj.t]);

  const t = traj.t[frame];
  const sep = data.separation_m[frame];
  const pedPresent = t >= data.scenario.pedestrian.start_time.value;

  return (
    <section className="instrument" aria-label="Simulation viewer">
      <div className="instrument-head">
        <span className="title">{title}</span>
        <OutcomeTag outcome={data.outcome} />
        <span className="muted" style={{ fontSize: "0.88rem" }}>
          Minimum moving clearance{" "}
          <strong className="num">{fmt(data.metrics.min_moving_separation_m)} m</strong>
          {data.metrics.min_ttc_s != null && <> · min TTC <span className="num">{data.metrics.min_ttc_s.toFixed(1)} s</span></>}
        </span>
        <span style={{ flex: 1 }} />
        {actions}
      </div>
      <div className="plan-wrap">
        <PlanView
          scenario={data.scenario}
          trajectory={traj}
          frame={frame}
          conflictZone={data.conflict_zone?.polygon}
          ghosts={ghosts}
          ghostRoute={ghostRoute}
        />
      </div>
      <div className="legend" style={{ padding: "6px 16px 10px" }}>
        <span><i style={{ background: "#2f3337" }} />Forklift footprint (forks forward)</span>
        <span><i style={{ background: "var(--hivis)", borderRadius: "50%", width: 10 }} />Pedestrian</span>
        <span><i style={{ background: "repeating-linear-gradient(45deg,#5b6067 0 1.5px,#c9c9c1 1.5px 4px)" }} />Obstruction</span>
        <span><i style={{ borderTop: "2px solid var(--accent)", height: 0 }} />Driver sight line, in view</span>
        <span><i style={{ borderTop: "2px dashed #8a8f96", height: 0 }} />Sight line blocked</span>
        <span><i style={{ border: "1px dotted var(--accent)" }} />Conflict zone</span>
        {ghosts && ghosts.length > 0 && <span><i style={{ border: "1px dashed #737981" }} />Position before change</span>}
      </div>
      <Timeline data={data} frame={frame} threshold={threshold} onSeek={(f) => { setPlaying(false); setFrame(f); }} />
      <div className="transport">
        <button className="btn small" onClick={() => {
          if (frame >= n - 1) setFrame(0);
          setPlaying((p) => !p);
        }} aria-label={playing ? "Pause" : "Play"}>
          {playing ? "Pause" : frame >= n - 1 ? "Replay" : "Play"}
        </button>
        <input
          type="range" min={0} max={n - 1} value={frame} aria-label="Simulation time"
          onChange={(e) => { setPlaying(false); setFrame(Number(e.target.value)); }}
        />
        <span className="time num">{t.toFixed(1)} s</span>
        <select className="inline" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Playback speed" style={{ width: "auto" }}>
          <option value={0.25}>0.25×</option>
          <option value={0.5}>0.5×</option>
          <option value={1}>1×</option>
          <option value={2}>2×</option>
        </select>
        <button className="btn small quiet" onClick={() => { setPlaying(false); setFrame(minIdx); }}>Jump to closest approach</button>
      </div>
      <div className="readouts">
        <Readout label="Forklift speed" value={traj.forklift_speed[frame].toFixed(2)} unit="m/s" />
        <Readout label="Driver" value={traj.driver_state[frame]} />
        <Readout label="Pedestrian" value={pedPresent ? (traj.pedestrian_visible[frame] ? "in view" : "hidden") : "not yet out"} />
        <Readout label="Clearance" value={pedPresent ? sep.toFixed(2) : "—"} unit={pedPresent ? "m" : ""} alert={pedPresent && sep < threshold} />
        <Readout label="Forklift x, y" value={`${traj.forklift_x[frame].toFixed(1)}, ${traj.forklift_y[frame].toFixed(1)}`} unit="m" />
        <Readout label="Pedestrian x, y" value={`${traj.pedestrian_x[frame].toFixed(1)}, ${traj.pedestrian_y[frame].toFixed(1)}`} unit="m" />
      </div>
    </section>
  );
}

function Readout({ label, value, unit, alert }: { label: string; value: string; unit?: string; alert?: boolean }) {
  return (
    <div className="readout">
      <div className="label">{label}</div>
      <div className="v num" style={alert ? { color: "var(--near)" } : undefined}>
        {value}
        {unit && <small>{unit}</small>}
      </div>
    </div>
  );
}

function Timeline({ data, frame, threshold, onSeek }: { data: SimulationPayload; frame: number; threshold: number; onSeek: (f: number) => void }) {
  const traj = data.trajectory;
  const W = 1000;
  const H = 136;
  const L = 44;
  const R = 12;
  const T = 38;
  const B = 22;
  const tmax = traj.t[traj.t.length - 1] || 1;
  const yMax = 6;
  const x = (t: number) => L + (t / tmax) * (W - L - R);
  const y = (v: number) => T + (1 - Math.max(0, Math.min(yMax, v)) / yMax) * (H - T - B);
  const start = data.scenario.pedestrian.start_time.value;
  const pts = traj.t
    .map((t, j) => (t >= start ? `${x(t).toFixed(1)},${y(data.separation_m[j]).toFixed(1)}` : null))
    .filter(Boolean)
    .join(" ");
  const events = traj.events.filter((e) => TIMELINE_EVENTS.has(e.kind));
  // Stagger event labels onto two rows when they would collide.
  const lastX = [-Infinity, -Infinity];
  const rows = events.map((e) => {
    const px = x(e.t);
    const row = px - lastX[0] > 110 ? 0 : px - lastX[1] > 110 ? 1 : 0;
    lastX[row] = px;
    return row;
  });
  const seek = (clientX: number, rect: DOMRect) => {
    const t = ((clientX - rect.left) / rect.width) * W;
    const tt = ((t - L) / (W - L - R)) * tmax;
    const idx = traj.t.reduce((b, v, j) => (Math.abs(v - tt) < Math.abs(traj.t[b] - tt) ? j : b), 0);
    onSeek(idx);
  };
  return (
    <div style={{ padding: "0 16px" }}>
      <svg
        viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block", cursor: "pointer" }}
        role="img" aria-label="Clearance between forklift and pedestrian over time"
        onClick={(e) => seek(e.clientX, e.currentTarget.getBoundingClientRect())}
      >
        <rect x={L} y={y(threshold)} width={W - L - R} height={y(0) - y(threshold)} fill="var(--near-soft)" />
        <line x1={L} x2={W - R} y1={y(threshold)} y2={y(threshold)} stroke="var(--near)" strokeDasharray="4 3" strokeWidth="1" />
        <text x={W - R} y={y(threshold) - 4} fontSize="11" textAnchor="end" fill="var(--near)">{threshold} m near-miss threshold</text>
        {[0, 2, 4, 6].map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="#e1e1db" />
            <text x={L - 6} y={y(v) + 4} fontSize="11" textAnchor="end" fill="#737981">{v}{v === 6 ? "+" : ""}</text>
          </g>
        ))}
        <text x={4} y={T - 24} fontSize="11" fill="#737981">Clearance, m</text>
        {Array.from({ length: Math.floor(tmax / 2) + 1 }, (_, n) => (
          <text key={n} x={x(n * 2)} y={H - 6} fontSize="11" textAnchor="middle" fill="#737981">{n * 2}</text>
        ))}
        <text x={W - R} y={H - 6} fontSize="11" textAnchor="end" fill="#737981">s</text>
        {events.map((e, j) => (
          <g key={j}>
            <line x1={x(e.t)} x2={x(e.t)} y1={T - 18 + rows[j] * 13} y2={H - B} stroke="#4b5057" strokeWidth="1" strokeDasharray="2 2" />
            <text x={x(e.t) + 3} y={T - 20 + rows[j] * 13} fontSize="10.5" fill="#4b5057">{EVENT_LABEL[e.kind] ?? e.kind} <tspan fill="#8a8f96">{e.t.toFixed(1)} s</tspan></text>
          </g>
        ))}
        <polyline points={pts} fill="none" stroke="var(--ink)" strokeWidth="1.8" />
        <line x1={x(traj.t[frame])} x2={x(traj.t[frame])} y1={T - 4} y2={H - B} stroke="var(--accent)" strokeWidth="2" />
      </svg>
    </div>
  );
}
