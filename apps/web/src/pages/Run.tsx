import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAsync, useReference } from "../hooks";
import { Instrument, fmt } from "../components/Instrument";
import { ErrorNotice, Loading } from "../components/States";
import type { SimulationPayload } from "../types";

const STRATEGY_LABEL: Record<string, string> = {
  targeted_search: "NearMiss targeted search",
  conventional_sweep: "Conventional sweep",
};

export function RunPage() {
  const { id = "", arm = "", strategy = "", idx = "0" } = useParams();
  const ref = useReference();
  const threshold = ref?.criteria.near_miss_threshold_m ?? 0.5;
  const exp = useAsync(() => api.experiment(id), [id]);
  const run = useAsync(() => api.replayRun(id, arm, strategy, Number(idx)), [id, arm, strategy, idx]);
  const [baseline, setBaseline] = useState<SimulationPayload | null>(null);
  const [baselineError, setBaselineError] = useState<ApiError | null>(null);
  const [loadingBase, setLoadingBase] = useState(false);

  const armInfo = exp.data?.plan?.arms.find((a) => a.arm_id === arm);
  const isBaseline = armInfo?.is_baseline ?? false;

  const compare = async () => {
    if (!run.data?.run || !exp.data) return;
    setLoadingBase(true);
    setBaselineError(null);
    try {
      setBaseline(await api.simulate({ scenario_id: exp.data.scenario_id, stress_point: run.data.run.stress_point }));
    } catch (e) {
      setBaselineError(e as ApiError);
    } finally {
      setLoadingBase(false);
    }
  };

  const v = run.data?.verification;
  const meta = ref?.stress_axes ?? [];

  return (
    <main className="page">
      <div className="crumbs">
        <Link to="/experiments">Experiments</Link> / <Link to={`/experiments/${id}`}>{exp.data?.title ?? id}</Link> / run {idx}
      </div>
      <div className="page-head">
        <div>
          <h1>{armInfo?.label ?? arm}, run {idx}</h1>
          <p className="sub">{STRATEGY_LABEL[strategy] ?? strategy}{Number(idx) === 0 && " · replayed incident conditions"}</p>
        </div>
        {!isBaseline && run.data && !baseline && (
          <button className="btn" onClick={compare} disabled={loadingBase}>{loadingBase ? "Simulating…" : "Compare with as-is layout under the same conditions"}</button>
        )}
      </div>

      {run.loading && <Loading height={460} />}
      {run.error && <ErrorNotice error={run.error} onRetry={run.reload} />}
      {run.data && (
        <div className="workspace">
          <div className="stack">
            <section className="panel">
              <div className="panel-head"><h2>Conditions for this run</h2></div>
              <div className="panel-pad" style={{ paddingTop: 6 }}>
                <table className="data">
                  <tbody>
                    {Object.entries(run.data.run!.stress_point).map(([k, val]) => {
                      const m = meta.find((x) => x.name === k);
                      return (
                        <tr key={k}>
                          <td>{m?.description ?? k}{m?.kind === "offset" && <span className="muted"> (shift)</span>}</td>
                          <td className="r num">{val.toFixed(3)} {m?.unit}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="panel">
              <div className="panel-head"><h2>Metrics</h2><span className="muted">computed from the trajectory</span></div>
              <div className="panel-pad">
                <dl className="kv" style={{ margin: 0 }}>
                  <dt>Moving clearance</dt><dd className="num">{fmt(run.data.metrics.min_moving_separation_m)} m</dd>
                  <dt>Any-time clearance</dt><dd className="num">{fmt(run.data.metrics.min_separation_m)} m</dd>
                  <dt>Contact</dt><dd>{run.data.metrics.collision ? `yes, at ${fmt(run.data.metrics.first_contact_s, 1)} s` : "no"}</dd>
                  <dt>Min TTC</dt><dd className="num">{run.data.metrics.min_ttc_s != null ? `${run.data.metrics.min_ttc_s.toFixed(1)} s` : "no projected contact"}</dd>
                  <dt>PET</dt><dd className="num">{run.data.metrics.pet_s != null ? `${run.data.metrics.pet_s.toFixed(2)} s` : "truck did not reach the crossing"}</dd>
                </dl>
              </div>
            </section>
            <section className="panel">
              <div className="panel-head"><h2>Reproduction check</h2></div>
              <div className="panel-pad">
                <p style={{ fontSize: "0.92rem" }}>
                  This page re-ran the simulation from the stored configuration and compared it with the recorded run.
                </p>
                <dl className="kv" style={{ margin: 0 }}>
                  <dt>Configuration</dt>
                  <dd>{v?.config_hash_match ? <span className="tag ok">Matches</span> : <span className="tag error">Differs</span>} <span className="hash">{v?.recorded_config_hash.slice(0, 16)}</span></dd>
                  <dt>Trajectory</dt>
                  <dd>{v?.trajectory_hash_match ? <span className="tag ok">Matches</span> : <span className="tag error">Differs</span>} <span className="hash">{v?.recorded_trajectory_hash.slice(0, 16)}</span></dd>
                  <dt>Engine</dt>
                  <dd>{v?.recorded_engine_version}{v && v.recorded_engine_version !== v.current_engine_version && <> recorded, {v.current_engine_version} now</>}</dd>
                </dl>
                {v && v.recorded_engine_version !== v.current_engine_version && (
                  <p className="notice warn" style={{ marginTop: 10, fontSize: "0.88rem" }}>
                    This run was recorded with an earlier engine version, so the replay may differ. Re-run the experiment to
                    reproduce it on the current engine.
                  </p>
                )}
              </div>
            </section>
          </div>
          <div className="stack">
            <Instrument data={run.data} threshold={threshold} title={armInfo?.label ?? arm} autoplay={false} />
            {baselineError && <ErrorNotice error={baselineError} />}
            {baseline && (
              <Instrument data={baseline} threshold={threshold} title="As-is layout, same conditions"
                actions={<button className="btn small" onClick={() => setBaseline(null)}>Hide</button>} />
            )}
          </div>
        </div>
      )}
    </main>
  );
}
