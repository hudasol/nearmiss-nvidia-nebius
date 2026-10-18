import { useState } from "react";
import { api } from "../api";
import { useAsync } from "../hooks";
import { ErrorNotice, Loading } from "../components/States";

/* eslint-disable @typescript-eslint/no-explicit-any */
const SUBSETS: [string, string][] = [
  ["held_out_interventions", "Held-out scenarios"],
  ["development_interventions", "Development scenarios"],
  ["all_interventions", "All intervention cases"],
];
const STRATS: [string, string][] = [
  ["targeted_search", "NearMiss targeted search"],
  ["conventional_sweep", "Conventional sweep"],
  ["nominal_only", "Replay only"],
];
const REPO = "https://github.com/hudasol/nearmiss-nvidia-nebius/blob/main";

function pct(r: any) {
  if (!r || r.value == null) return <span className="muted">n/a</span>;
  return (
    <span title={`${r.num} of ${r.den}; 95% Wilson ${r.wilson95?.map((x: number) => (x * 100).toFixed(0)).join("–")}%`}>
      {(r.value * 100).toFixed(0)}% <span className="muted">({r.num}/{r.den})</span>
    </span>
  );
}

export function BenchmarkPage() {
  const b = useAsync(() => api.benchmark(), []);
  const [subset, setSubset] = useState("held_out_interventions");
  if (b.loading) return <main className="page"><Loading rows={4} height={60} /></main>;
  if (b.error) {
    return (
      <main className="page">
        <h1>Benchmark</h1>
        {b.error.code === "benchmark_not_run"
          ? <p className="muted">No benchmark results yet. Run <code>python -m nearmiss.evaluation.benchmark</code> to generate them.</p>
          : <ErrorNotice error={b.error} onRetry={b.reload} />}
      </main>
    );
  }
  const d = b.data!;
  const s = d.summary[subset];
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Benchmark</h1>
          <p className="sub">
            Does physics-targeted stress testing find failures that a conventional parameter sweep misses, with the same
            48-run budget? Criteria, cases and decision rule were{" "}
            <a href={`${REPO}/docs/benchmark-preregistration.md`} target="_blank" rel="noreferrer">preregistered</a> before the run.
          </p>
        </div>
      </div>

      <div className="notice warn section">
        <strong>Preregistered verdict: not supported on held-out scenarios.</strong> Both workflows caught every unsafe
        held-out change. The difference appears on development scenarios, where failure regions are small. The
        targeted search's held-out false rejections are real near misses that the reference sweep missed; see the{" "}
        <a href={`${REPO}/docs/benchmark-results.md`} target="_blank" rel="noreferrer">full results and deviations</a>.
      </div>

      <section className="section">
        <div className="tabs" role="tablist">
          {SUBSETS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={subset === k} onClick={() => setSubset(k)}>{label}</button>
          ))}
        </div>
        <p className="muted">
          {s.cases} cases: {s.unsafe_cases} unsafe and {s.safe_cases} with no failure in the reference sweep
          ({d.config.oracle_points.toLocaleString()} Halton runs per case). Rates are over cases × {d.config.seeds.length} seeds.
        </p>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Workflow</th>
                <th className="r">Unsafe-change recall</th>
                <th className="r">False acceptance</th>
                <th className="r">Effective-change acceptance</th>
                <th className="r">False rejection</th>
                <th className="r">Median runs to first failure</th>
                <th className="r">Wall time per evaluation</th>
              </tr>
            </thead>
            <tbody>
              {STRATS.map(([k, label]) => {
                const m = s[k];
                return (
                  <tr key={k}>
                    <td><strong>{label}</strong></td>
                    <td className="r num">{pct(m.unsafe_intervention_recall)}</td>
                    <td className="r num">{pct(m.false_acceptance_rate)}</td>
                    <td className="r num">{pct(m.effective_intervention_acceptance_rate)}</td>
                    <td className="r num">{pct(m.false_rejection_rate)}</td>
                    <td className="r num">{m.median_runs_to_first_failure ?? "—"}</td>
                    <td className="r num">{m.mean_runtime_s_per_evaluation != null ? `${m.mean_runtime_s_per_evaluation.toFixed(2)} s` : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="muted" style={{ fontSize: "0.86rem", marginTop: 8 }}>
          Wilson intervals (hover) treat seeds as independent and so understate uncertainty. Model inference is not part of either
          workflow; its cost is tracked separately on the integration page.
        </p>
      </section>

      <section className="section">
        <h2>Cases</h2>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Case</th><th>Role</th><th>Category</th><th className="r">Replay clearance</th>
                <th>Reference label</th><th className="r">Targeted rejects</th><th className="r">Conventional rejects</th><th className="r">Replay-only rejects</th>
              </tr>
            </thead>
            <tbody>
              {d.cases.map((c: any) => (
                <tr key={c.case_id}>
                  <td>{c.intervention?.label ?? "As-is layout"}<div className="hash muted">{c.case_id}</div></td>
                  <td>{c.role === "held_out" ? "Held out" : "Development"}</td>
                  <td>{c.category.replace("_", " ")}</td>
                  <td className="r num">{c.nominal_moving_separation_m != null ? `${c.nominal_moving_separation_m.toFixed(2)} m` : "—"}</td>
                  <td>
                    <span className={`tag ${c.oracle.label === "unsafe" ? "unsafe" : "clear"}`}>{c.oracle.label === "unsafe" ? "Unsafe" : "No failure found"}</span>
                    <div className="muted num" style={{ fontSize: "0.82rem" }}>{c.oracle.unsafe_runs} / {c.oracle.points} unsafe</div>
                  </td>
                  <td className="r num">{c.rejections.targeted_search} / {c.evaluations.targeted_search}</td>
                  <td className="r num">{c.rejections.conventional_sweep} / {c.evaluations.conventional_sweep}</td>
                  <td className="r num">{c.rejections.nominal_only} / {c.evaluations.nominal_only}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section">
        <h2>Provenance</h2>
        <dl className="kv">
          <dt>Generated</dt><dd>{new Date(d.generated_at).toLocaleString()} in {Math.round(d.runtime_s / 60)} min</dd>
          <dt>Commit</dt><dd className="hash">{d.provenance.git_commit}</dd>
          <dt>Versions</dt><dd>{d.provenance.engine_version}, {d.provenance.metrics_version}, {d.provenance.strategy_version}, {d.provenance.criteria_id}</dd>
          <dt>Budget</dt><dd>{d.config.budget_per_evaluation} runs per evaluation</dd>
        </dl>
      </section>
    </main>
  );
}
