import type { SimulationPayload } from "./types";

export function fmt(v: number | null | undefined, nd = 2): string {
  return v == null ? "n/a" : v.toFixed(nd);
}

/** Frame with the smallest clearance after the pedestrian has set off. */
export function minSeparationFrame(data: SimulationPayload): number {
  const start = data.scenario.pedestrian.start_time.value;
  let best = 0;
  let bestV = Infinity;
  data.separation_m.forEach((v, j) => {
    if (data.trajectory.t[j] >= start && v < bestV) {
      bestV = v;
      best = j;
    }
  });
  return best;
}
