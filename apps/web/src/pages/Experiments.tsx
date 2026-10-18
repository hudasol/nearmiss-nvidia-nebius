import { Link } from "react-router-dom";
import { api } from "../api";
import { useAsync } from "../hooks";
import { StatusTag } from "../components/Tags";
import { ErrorNotice, Loading } from "../components/States";

export function ExperimentsPage() {
  const list = useAsync(() => api.experiments(), []);
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Experiments</h1>
          <p className="sub">Every experiment submitted, including those the compiler rejected.</p>
        </div>
      </div>
      {list.loading && <Loading rows={4} height={36} />}
      {list.error && <ErrorNotice error={list.error} onRetry={list.reload} />}
      {list.data?.length === 0 && (
        <p className="muted">No experiments yet. <Link to="/">Open an incident</Link> and design one from its replay.</p>
      )}
      {list.data && list.data.length > 0 && (
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr><th>Experiment</th><th>Scenario</th><th>Status</th><th>Findings</th><th className="r">Runs</th><th className="r">Runtime</th><th>Created</th></tr>
            </thead>
            <tbody>
              {list.data.map((e) => {
                const sums = (e.result_summary?.summaries ?? []).filter((s) => !s.is_baseline);
                const failing = sums.filter((s) => s.verdict !== "no_failure_found");
                return (
                  <tr key={e.id}>
                    <td><Link to={`/experiments/${e.id}`}>{e.title}</Link><div className="hash muted">{e.id}</div></td>
                    <td>{e.scenario_id}</td>
                    <td><StatusTag status={e.status} /></td>
                    <td>
                      {e.status === "completed"
                        ? failing.length
                          ? `${failing.length} of ${sums.length} changes failed`
                          : `No failure found in ${sums.length} change${sums.length === 1 ? "" : "s"}`
                        : e.status === "rejected" ? "Not run" : "—"}
                    </td>
                    <td className="r num">{e.progress_total ? `${e.progress_done} / ${e.progress_total}` : "—"}</td>
                    <td className="r num">{e.runtime_s != null ? `${e.runtime_s.toFixed(1)} s` : "—"}</td>
                    <td className="muted num">{new Date(e.created_at).toLocaleString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
