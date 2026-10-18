import { Link } from "react-router-dom";
import { api } from "../api";
import { useAsync } from "../hooks";
import { PlanView } from "../components/PlanView";
import { ProvTag, StatusTag, SyntheticNote } from "../components/Tags";
import { ErrorNotice, Loading } from "../components/States";
import type { Scenario } from "../types";

export function IncidentsPage() {
  const list = useAsync(async () => {
    const summaries = await api.scenarios();
    return Promise.all(summaries.map(async (s) => ({ summary: s, scenario: (await api.scenario(s.id)).scenario })));
  }, []);
  const experiments = useAsync(() => api.experiments(), []);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Incidents</h1>
          <p className="sub">
            Confirmed near-misses, each rebuilt as a calibrated crossing scenario. Open one to replay it and test a
            change to the layout.
          </p>
        </div>
      </div>

      <section className="section" aria-label="Incident list">
        {list.loading && <Loading rows={3} />}
        {list.error && <ErrorNotice error={list.error} onRetry={list.reload} />}
        {list.data?.length === 0 && <p className="muted">No scenarios are loaded. Check that data/scenarios contains reference files.</p>}
        {list.data?.map(({ summary, scenario }) => (
          <IncidentRow key={summary.id} scenario={scenario} origin={summary.origin} />
        ))}
      </section>

      <section className="section">
        <h2>Recent experiments</h2>
        {experiments.loading && <Loading rows={2} />}
        {experiments.error && <ErrorNotice error={experiments.error} onRetry={experiments.reload} />}
        {experiments.data && experiments.data.length === 0 && (
          <p className="muted">No experiments yet. Open an incident and design one from its replay.</p>
        )}
        {experiments.data && experiments.data.length > 0 && (
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr><th>Experiment</th><th>Scenario</th><th>Status</th><th>Arms with failures found</th><th>Created</th></tr>
              </thead>
              <tbody>
                {experiments.data.slice(0, 8).map((e) => {
                  const sums = e.result_summary?.summaries ?? [];
                  const failing = sums.filter((s) => !s.is_baseline && s.verdict !== "no_failure_found").length;
                  const total = sums.filter((s) => !s.is_baseline).length;
                  return (
                    <tr key={e.id}>
                      <td><Link to={`/experiments/${e.id}`}>{e.title}</Link></td>
                      <td>{e.scenario_id}</td>
                      <td><StatusTag status={e.status} /></td>
                      <td className="num">{e.status === "completed" ? `${failing} of ${total}` : "—"}</td>
                      <td className="muted num">{new Date(e.created_at).toLocaleString()}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

function IncidentRow({ scenario, origin }: { scenario: Scenario; origin: string }) {
  const inc = scenario.incident;
  const reported = inc?.reported_closest_approach_m;
  return (
    <Link to={`/incidents/${scenario.id}`} className="incident-row">
      <div className="thumb"><PlanView scenario={scenario} variant="thumb" /></div>
      <div>
        <h3 style={{ marginBottom: 4 }}>{inc?.summary ?? scenario.title}</h3>
        <div className="muted" style={{ fontSize: "0.9rem", marginBottom: 6 }}>
          {inc ? `${inc.location}, ${inc.occurred_at}` : scenario.title}
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {scenario.data_origin === "synthetic_reference" ? <SyntheticNote /> : <span className="tag neutral">{origin === "user" ? "Saved variant" : scenario.data_origin}</span>}
          {inc && <span className="tag neutral">{inc.id}</span>}
        </div>
      </div>
      <div className="meta">
        {reported && (
          <>
            <div className="label">Reported closest approach</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 600 }} className="num">
              {reported.value.toFixed(1)} m{reported.uncertainty != null && <span className="muted" style={{ fontWeight: 400 }}> ± {reported.uncertainty}</span>}
            </div>
            <ProvTag p={reported.provenance} />
          </>
        )}
      </div>
    </Link>
  );
}
