import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { useAsync } from "../hooks";
import { DecisionTag, OutcomeTag, ProvTag } from "../components/Tags";
import { ErrorNotice, Loading } from "../components/States";
import { fmt } from "../format";

/* eslint-disable @typescript-eslint/no-explicit-any */
export function ReportPage() {
  const { id = "" } = useParams();
  const rep = useAsync(() => api.report(id), [id]);
  if (rep.loading) return <main className="page"><Loading rows={4} height={90} /></main>;
  if (rep.error) return <main className="page"><ErrorNotice error={rep.error} onRetry={rep.reload} /></main>;
  const r = rep.data!;
  const sc = r.scenario;
  return (
    <main className="page">
      <div className="crumbs no-print"><Link to="/experiments">Experiments</Link> / <Link to={`/experiments/${id}`}>{r.experiment.title}</Link> / report</div>
      <div className="no-print" style={{ display: "flex", gap: 8, justifyContent: "flex-end", maxWidth: 980, margin: "0 auto 12px" }}>
        <a className="btn" href={`/api/v1/experiments/${id}/report.md`} download>Download Markdown</a>
        <button className="btn" onClick={() => window.print()}>Print</button>
      </div>
      <article className="report-doc">
        <h1>{r.experiment.title}</h1>
        <p className="muted">
          Evidence report for experiment <span className="hash">{r.experiment.id}</span>, completed {new Date(r.experiment.finished_at).toLocaleString()}.
          Generated from stored runs on {new Date(r.generated_at).toLocaleString()}.
        </p>
        <div className="notice warn" style={{ margin: "16px 0" }}>
          Simulation-based decision support, not a certified safety assessment. Scenario data is{" "}
          <strong>{String(sc.data_origin).replace(/_/g, " ")}</strong>.
        </div>

        <h2>Findings</h2>
        <ul>
          {r.headline.map((h: string, j: number) => <li key={j} style={h.startsWith("  ") ? { listStyle: "circle", marginLeft: 20 } : undefined}>{h.trim()}</li>)}
        </ul>

        <h2>Claim and acceptance rule</h2>
        <p>{r.claim.text}</p>
        <p className="muted">{r.criteria.rule} Near-miss threshold {r.criteria.threshold_m} m. {r.criteria.rationale}</p>

        <h2>Scenario</h2>
        <p><strong>{sc.title}</strong> <span className="hash">{sc.id}</span></p>
        <p>{sc.description}</p>
        {sc.incident && <p className="narrative"><em>Incident {sc.incident.id}:</em> {sc.incident.narrative}</p>}
        <table className="data">
          <thead><tr><th>Parameter</th><th className="r">Value</th><th>Provenance</th><th>Source</th></tr></thead>
          <tbody>
            {sc.parameters.map((p: any) => (
              <tr key={p.parameter}><td>{p.parameter}</td><td className="r num">{p.value} {p.unit}</td><td><ProvTag p={p.provenance} /></td><td className="muted">{p.source}</td></tr>
            ))}
          </tbody>
        </table>

        {r.stress_envelope && (
          <>
            <h2>Stress envelope</h2>
            <table className="data">
              <thead><tr><th>Condition</th><th className="r">Range</th><th>Applied as</th></tr></thead>
              <tbody>
                {r.stress_envelope.map((a: any) => (
                  <tr key={a.name}><td>{a.description}</td><td className="r num">{a.low} to {a.high} {a.unit}</td><td>{a.kind === "offset" ? "shift from incident value" : "absolute value"}</td></tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        <h2>Results by arm</h2>
        {r.arms.map((a: any) => (
          <div key={a.arm_id}>
            <h3>{a.label}</h3>
            {a.changes.length > 0 && <p className="muted">Changes {a.changes.map((c: any) => <code key={c.path}>{c.path} </code>)}</p>}
            {a.nominal && <p>Replayed incident: <OutcomeTag outcome={a.nominal.outcome} /> minimum clearance {fmt(a.nominal.min_moving_separation_m)} m.</p>}
            <table className="data">
              <thead><tr><th>Workflow</th><th>Decision</th><th className="r">Unsafe runs</th><th className="r">Collisions</th><th className="r">Worst clearance</th></tr></thead>
              <tbody>
                {Object.values(a.strategies).map((s: any) => (
                  <tr key={s.label}>
                    <td>{s.label}</td><td><DecisionTag decision={s.decision} /></td>
                    <td className="r num">{s.unsafe_runs} of {s.runs}</td><td className="r num">{s.collisions}</td>
                    <td className="r num">{fmt(s.worst_run.min_moving_separation_m)} m</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ marginTop: 8 }}>{a.verdict_text}</p>
          </div>
        ))}

        {r.failure_cases.length > 0 && (
          <>
            <h2>Failure cases</h2>
            <p className="muted">{r.failure_case_count} unsafe runs in total; the {Math.min(12, r.failure_cases.length)} worst are listed. Each can be replayed from its configuration hash.</p>
            <div className="table-scroll">
              <table className="data">
                <thead><tr><th>Arm</th><th>Workflow</th><th className="r">Run</th><th>Outcome</th><th className="r">Clearance</th><th>Config</th><th /></tr></thead>
                <tbody>
                  {r.failure_cases.slice(0, 12).map((f: any) => (
                    <tr key={`${f.arm_id}-${f.strategy}-${f.index}`}>
                      <td>{f.arm_label}</td><td>{f.strategy === "targeted_search" ? "Targeted" : "Conventional"}</td>
                      <td className="r num">{f.index}</td><td><OutcomeTag outcome={f.outcome} /></td>
                      <td className="r num">{fmt(f.min_moving_separation_m)} m</td>
                      <td className="hash">{f.config_hash.slice(0, 10)}</td>
                      <td className="no-print"><Link to={`/experiments/${id}/runs/${encodeURIComponent(f.arm_id)}/${f.strategy}/${f.index}`}>Replay</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <h2>Metric definitions</h2>
        <ul>{r.metrics.map((m: any) => <li key={m.name}><strong>{m.name}</strong> [{m.unit}]: {m.description}{m.supports_clearance_claim ? "" : " (diagnostic only)"}</li>)}</ul>

        <h2>Assumptions</h2>
        <ul>{r.assumptions.map((a: any) => <li key={a.parameter}>{a.parameter} = {a.value} {a.unit}: {a.source}</li>)}</ul>

        <h2>Limitations</h2>
        <ul>{r.limitations.map((l: string, j: number) => <li key={j}>{l}</li>)}</ul>

        <h2>Model-generated content</h2>
        <p className="muted">Shown for traceability. None of it was used to decide any outcome above.</p>
        {r.model_generated_content.length === 0 ? <p>No model calls are associated with this scenario.</p> : (
          <ul>{r.model_generated_content.map((m: any) => <li key={m.id}>{m.task} by {m.model ?? "unknown model"}, {m.status}, {new Date(m.created_at).toLocaleString()}</li>)}</ul>
        )}

        <h2>Reproducibility</h2>
        <dl className="kv">
          <dt>Plan hash</dt><dd className="hash">{r.provenance.plan_hash}</dd>
          <dt>Spec hash</dt><dd className="hash">{r.provenance.spec_hash}</dd>
          <dt>Versions</dt><dd>{r.provenance.engine_version}, {r.provenance.metrics_version}, {r.provenance.strategy_version}, {r.provenance.criteria_id}</dd>
          <dt>Runs</dt><dd className="num">Seed {r.provenance.seed}, {r.provenance.budget_per_arm} per arm per workflow, {r.provenance.total_runs} total</dd>
        </dl>
      </article>
    </main>
  );
}
