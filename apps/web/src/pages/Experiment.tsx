import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAsync, useReference } from "../hooks";
import { DecisionTag, OutcomeTag, StatusTag } from "../components/Tags";
import { ErrorNotice, Loading } from "../components/States";
import { fmt } from "../format";
import type { Experiment, RunRecord } from "../types";

const STRATEGY_LABEL: Record<string, string> = {
  targeted_search: "NearMiss targeted search",
  conventional_sweep: "Conventional sweep",
};

export function ExperimentPage() {
  const { id = "" } = useParams();
  const [exp, setExp] = useState<Experiment | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const e = await api.experiment(id);
        if (!alive) return;
        setExp(e);
        setError(null);
        if (e.status === "queued" || e.status === "running") timer = setTimeout(load, 700);
      } catch (err) {
        if (alive) setError(err as ApiError);
      }
    };
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [id]);

  if (error && !exp) return <main className="page"><ErrorNotice error={error} /></main>;
  if (!exp) return <main className="page"><Loading rows={3} height={80} /></main>;

  const active = exp.status === "queued" || exp.status === "running";
  const cancel = async () => {
    setCancelling(true);
    try { setExp((await api.cancel(exp.id)).experiment); } catch (e) { setError(e as ApiError); } finally { setCancelling(false); }
  };

  return (
    <main className="page">
      <div className="crumbs"><Link to="/experiments">Experiments</Link> / {exp.id}</div>
      <div className="page-head">
        <div>
          <h1>{exp.title}</h1>
          <p className="sub">
            Scenario <Link to={`/incidents/${exp.scenario_id}`}>{exp.scenario_id}</Link> · created {new Date(exp.created_at).toLocaleString()}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <StatusTag status={exp.status} />
          {active && <button className="btn" onClick={cancel} disabled={cancelling}>{cancelling ? "Cancelling…" : "Cancel experiment"}</button>}
          {exp.status === "completed" && <Link className="btn primary" to={`/experiments/${exp.id}/report`}>Open evidence report</Link>}
        </div>
      </div>
      {error && <ErrorNotice error={error} />}

      {active && (
        <section className="panel panel-pad section">
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <span>{exp.status === "queued" ? "Waiting for the simulation worker" : "Simulating"}</span>
            <span className="num">{exp.progress_done} of {exp.progress_total} runs</span>
          </div>
          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={exp.progress_total} aria-valuenow={exp.progress_done}>
            <div style={{ width: `${exp.progress_total ? (100 * exp.progress_done) / exp.progress_total : 0}%` }} />
          </div>
        </section>
      )}

      {exp.status === "rejected" && <Rejected exp={exp} />}
      {exp.status === "failed" && <div className="notice error section">The experiment failed: {exp.error}</div>}
      {exp.status === "cancelled" && <div className="notice section">This experiment was cancelled before it finished. No results were recorded.</div>}
      {exp.status === "completed" && <Results exp={exp} />}
      {exp.plan && <Method exp={exp} />}
    </main>
  );
}

function Rejected({ exp }: { exp: Experiment }) {
  return (
    <section className="section">
      <div className="notice error" style={{ marginBottom: 12 }}>
        The Safety Change Compiler rejected this experiment before any simulation ran. It is kept here for the audit trail.
      </div>
      <div className="findings">
        {exp.compilation.findings.map((f, j) => (
          <div key={j} className={`finding ${f.severity}`}>
            <span className="code">{f.code} · {f.path}</span>
            <div>{f.message}</div>
            {f.hint && <div className="hint">{f.hint}</div>}
          </div>
        ))}
      </div>
    </section>
  );
}

function Results({ exp }: { exp: Experiment }) {
  const ref = useReference();
  const navigate = useNavigate();
  const runs = useAsync(() => api.runs(exp.id), [exp.id]);
  const plan = exp.plan!;
  const summaries = exp.result_summary?.summaries ?? [];
  const primary = plan.strategies[0];
  const [arm, setArm] = useState<string>(() => summaries.find((s) => s.verdict === "fails_under_stress")?.arm_id ?? summaries[1]?.arm_id ?? "baseline");
  const byArm = useMemo(() => {
    const m = new Map<string, RunRecord[]>();
    for (const r of runs.data ?? []) {
      const k = `${r.arm_id}|${r.strategy}`;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(r);
    }
    return m;
  }, [runs.data]);
  const threshold = ref?.criteria.near_miss_threshold_m ?? 0.5;

  return (
    <>
      <section className="section">
        <h2>Results</h2>
        <p className="muted">{plan.claim_text}</p>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Arm</th>
                <th className="r">Replayed incident, min clearance</th>
                {plan.strategies.map((s) => <th key={s}>{STRATEGY_LABEL[s] ?? s}</th>)}
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {summaries.map((s) => {
                const nominal = byArm.get(`${s.arm_id}|${primary}`)?.[0];
                return (
                  <tr key={s.arm_id} className={`clickable ${arm === s.arm_id ? "selected" : ""}`} onClick={() => setArm(s.arm_id)}>
                    <td><strong>{s.label}</strong>{s.is_baseline && <div className="muted" style={{ fontSize: "0.84rem" }}>Baseline for comparison</div>}</td>
                    <td className="r num">{nominal ? <>{fmt(nominal.metrics.min_moving_separation_m)} m <OutcomeTag outcome={nominal.outcome} /></> : "…"}</td>
                    {plan.strategies.map((st) => (
                      <td key={st}>
                        <DecisionTag decision={s.decisions[st]} />{" "}
                        <span className="num muted">{s.unsafe_runs[st]} of {plan.budget_per_arm} unsafe</span>
                      </td>
                    ))}
                    <td style={{ maxWidth: 280 }}>{s.verdict_text}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <h2>Where each workflow looked: {summaries.find((s) => s.arm_id === arm)?.label}</h2>
          <span className="muted" style={{ fontSize: "0.88rem" }}>Each mark is one simulation run. Select a run to replay it.</span>
        </div>
        {runs.loading && <Loading height={260} />}
        {runs.error && <ErrorNotice error={runs.error} onRetry={runs.reload} />}
        {runs.data && plan.stress && (
          <div className={plan.strategies.length > 1 ? "split" : undefined}>
            {plan.strategies.map((st) => (
              <StressMap
                key={st}
                title={STRATEGY_LABEL[st] ?? st}
                runs={byArm.get(`${arm}|${st}`) ?? []}
                axes={plan.stress!.axes}
                onPick={(r) => navigate(`/experiments/${exp.id}/runs/${encodeURIComponent(r.arm_id)}/${r.strategy}/${r.idx}`)}
              />
            ))}
          </div>
        )}
        {runs.data && (
          <RunTable runs={[...(byArm.get(`${arm}|${primary}`) ?? []), ...plan.strategies.slice(1).flatMap((st) => byArm.get(`${arm}|${st}`) ?? [])]} expId={exp.id} threshold={threshold} />
        )}
      </section>
    </>
  );
}

function StressMap({ title, runs, axes, onPick }: { title: string; runs: RunRecord[]; axes: { name: string; low: number; high: number; unit: string }[]; onPick: (r: RunRecord) => void }) {
  const ax = axes.find((a) => a.name === "pedestrian_start_offset") ?? axes[0];
  const ay = axes.find((a) => a.name === "forklift_speed") ?? axes[1] ?? axes[0];
  const W = 460, H = 260, L = 46, B = 34, T = 14, R = 10;
  const xs = (v: number) => L + ((v - ax.low) / (ax.high - ax.low || 1)) * (W - L - R);
  const ys = (v: number) => T + (1 - (v - ay.low) / (ay.high - ay.low || 1)) * (H - T - B);
  const [hover, setHover] = useState<RunRecord | null>(null);
  const unsafe = runs.filter((r) => r.outcome !== "clear").length;
  return (
    <figure className="panel" style={{ margin: 0 }}>
      <figcaption className="panel-head"><span style={{ fontWeight: 600 }}>{title}</span><span className="muted num">{unsafe} unsafe of {runs.length}</span></figcaption>
      <div style={{ padding: "8px 12px" }}>
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }} role="img"
          aria-label={`${title}: runs plotted by pedestrian timing and forklift speed; ${unsafe} unsafe`}>
          <rect x={L} y={T} width={W - L - R} height={H - T - B} fill="#f6f6f3" stroke="#e1e1db" />
          {[0, 0.25, 0.5, 0.75, 1].map((f) => (
            <g key={f}>
              <text x={xs(ax.low + f * (ax.high - ax.low))} y={H - B + 14} fontSize="10.5" textAnchor="middle" fill="#737981">{(ax.low + f * (ax.high - ax.low)).toFixed(1)}</text>
              <text x={L - 6} y={ys(ay.low + f * (ay.high - ay.low)) + 4} fontSize="10.5" textAnchor="end" fill="#737981">{(ay.low + f * (ay.high - ay.low)).toFixed(1)}</text>
            </g>
          ))}
          <text x={(L + W - R) / 2} y={H - 4} fontSize="11" textAnchor="middle" fill="#4b5057">Pedestrian arrival shift, {ax.unit}</text>
          <text x={12} y={(T + H - B) / 2} fontSize="11" textAnchor="middle" fill="#4b5057" transform={`rotate(-90 12 ${(T + H - B) / 2})`}>Forklift speed, {ay.unit}</text>
          {runs.map((r) => {
            const cx = xs(r.stress_point[ax.name] ?? 0), cy = ys(r.stress_point[ay.name] ?? 0);
            const color = r.outcome === "collision" ? "var(--collision)" : r.outcome === "near_miss" ? "var(--near)" : "var(--clear)";
            return r.outcome === "clear" ? (
              <circle key={r.idx} cx={cx} cy={cy} r={4.2} fill="none" stroke={color} strokeWidth={1.4} style={{ cursor: "pointer" }}
                onMouseEnter={() => setHover(r)} onMouseLeave={() => setHover(null)} onClick={() => onPick(r)}>
                <title>Run {r.idx}: clear, {fmt(r.metrics.min_moving_separation_m)} m</title>
              </circle>
            ) : (
              <path key={r.idx} d={`M${cx - 4.5} ${cy - 4.5} L${cx + 4.5} ${cy + 4.5} M${cx + 4.5} ${cy - 4.5} L${cx - 4.5} ${cy + 4.5}`} stroke={color} strokeWidth={2.4}
                style={{ cursor: "pointer" }} onMouseEnter={() => setHover(r)} onMouseLeave={() => setHover(null)} onClick={() => onPick(r)}>
                <title>Run {r.idx}: {r.outcome.replace("_", " ")}, {fmt(r.metrics.min_moving_separation_m)} m</title>
              </path>
            );
          })}
        </svg>
        <div className="muted" style={{ fontSize: "0.84rem", minHeight: "1.3em" }}>
          {hover ? <>Run {hover.idx}: {hover.outcome.replace("_", " ")}, clearance {fmt(hover.metrics.min_moving_separation_m)} m</> :
            <>○ clear · <span style={{ color: "var(--near)" }}>×</span> near miss or collision</>}
        </div>
      </div>
    </figure>
  );
}

const AXIS_SHORT: Record<string, string> = {
  forklift_speed: "speed", pedestrian_speed: "walk", pedestrian_start_offset: "timing",
  reaction_time: "react", braking_decel: "brake", obstruction_shift_x: "shift x", obstruction_shift_y: "shift y",
};

function RunTable({ runs, expId, threshold }: { runs: RunRecord[]; expId: string; threshold: number }) {
  const [onlyUnsafe, setOnlyUnsafe] = useState(true);
  const shown = runs.filter((r) => !onlyUnsafe || r.outcome !== "clear" || r.idx === 0);
  const sorted = [...shown].sort((a, b) => a.margin_m - b.margin_m);
  const keys = runs[0] ? Object.keys(runs[0].stress_point) : [];
  return (
    <div style={{ marginTop: 16 }}>
      <label style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <input type="checkbox" checked={onlyUnsafe} onChange={(e) => setOnlyUnsafe(e.target.checked)} />
        Show only unsafe runs and the replayed incident
      </label>
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th>Workflow</th><th className="r">Run</th><th>Outcome</th><th className="r">Clearance, m</th>
              {keys.map((k) => <th key={k} className="r">{AXIS_SHORT[k] ?? k}</th>)}
              <th />
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && <tr><td colSpan={5 + keys.length} className="muted">No unsafe runs in this arm. Clear the filter to see all runs.</td></tr>}
            {sorted.map((r) => (
              <tr key={`${r.strategy}-${r.idx}`}>
                <td>{STRATEGY_LABEL[r.strategy] ?? r.strategy}</td>
                <td className="r num">{r.idx}{r.idx === 0 && <span className="muted"> (incident)</span>}</td>
                <td><OutcomeTag outcome={r.outcome} /></td>
                <td className="r num" style={{ color: (r.metrics.min_moving_separation_m ?? 9) < threshold ? "var(--near)" : undefined }}>{fmt(r.metrics.min_moving_separation_m)}</td>
                {keys.map((k) => <td key={k} className="r num">{r.stress_point[k].toFixed(2)}</td>)}
                <td><Link to={`/experiments/${expId}/runs/${encodeURIComponent(r.arm_id)}/${r.strategy}/${r.idx}`}>Replay</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Method({ exp }: { exp: Experiment }) {
  const p = exp.plan!;
  return (
    <section className="section">
      <h2>Method and provenance</h2>
      <div className="split">
        <dl className="kv">
          <dt>Workflows</dt><dd>{p.strategies.map((s) => STRATEGY_LABEL[s] ?? s).join(", ")}</dd>
          <dt>Runs</dt><dd className="num">{p.budget_per_arm} per arm per workflow, {p.total_runs} in total</dd>
          <dt>Seed</dt><dd className="num">{p.seed}</dd>
          <dt>Criteria</dt><dd>{p.criteria_id}</dd>
          <dt>Runtime</dt><dd className="num">{exp.runtime_s != null ? `${exp.runtime_s.toFixed(1)} s` : "—"}</dd>
        </dl>
        <dl className="kv">
          <dt>Plan hash</dt><dd className="hash">{exp.plan_hash}</dd>
          <dt>Spec hash</dt><dd className="hash">{exp.spec_hash}</dd>
          <dt>Engine</dt><dd>{p.engine_version}</dd>
          <dt>Strategies</dt><dd>{p.strategy_version}</dd>
        </dl>
      </div>
      {p.arms.filter((a) => !a.is_baseline).map((a) => (
        <p key={a.arm_id} className="muted" style={{ fontSize: "0.88rem", margin: "8px 0 0" }}>
          {a.label}: changes {a.changes.map((c) => <code key={c.path}>{c.path}</code>)} only (independently verified by the compiler).
        </p>
      ))}
      {exp.compilation.findings.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: "pointer" }}>Compiler warnings ({exp.compilation.findings.length})</summary>
          <div className="findings" style={{ marginTop: 8 }}>
            {exp.compilation.findings.map((f, j) => (
              <div key={j} className={`finding ${f.severity}`}><span className="code">{f.code}</span><div>{f.message}</div></div>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
