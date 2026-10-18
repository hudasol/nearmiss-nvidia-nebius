import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAsync, useReference } from "../hooks";
import { Instrument } from "../components/Instrument";
import { fmt } from "../format";
import { ProvTag, StatusTag, SyntheticNote } from "../components/Tags";
import { ErrorNotice, Loading } from "../components/States";
import { ExperimentBuilder } from "../components/ExperimentBuilder";
import type { Intervention, ModelCall, Obstruction, Param, Point, ProposedParameter, Scenario, SimulationPayload } from "../types";

export function IncidentPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "design" ? "design" : "replay";
  const ref = useReference();
  const threshold = ref?.criteria.near_miss_threshold_m ?? 0.5;
  const detail = useAsync(() => api.scenario(id), [id]);
  const replay = useAsync(() => api.replay(id), [id]);
  const [preview, setPreview] = useState<{ iv: Intervention; data: SimulationPayload } | null>(null);
  const [previewError, setPreviewError] = useState<ApiError | null>(null);

  const scenario = detail.data?.scenario;

  const showPreview = async (iv: Intervention | null) => {
    setPreviewError(null);
    if (!iv) { setPreview(null); return; }
    try {
      const data = await api.simulate({ scenario_id: id, intervention: iv });
      setPreview({ iv, data });
    } catch (e) {
      setPreview(null);
      setPreviewError(e as ApiError);
    }
  };

  const ghosts = useMemo<Obstruction[]>(() => {
    if (!preview || !scenario) return [];
    const iv = preview.iv;
    if (iv.type === "relocate_obstruction" || iv.type === "remove_obstruction") {
      const o = scenario.obstructions.find((x) => x.id === iv.obstruction_id);
      return o ? [o] : [];
    }
    return [];
  }, [preview, scenario]);
  const ghostRoute: Point[] | null = preview?.iv.type === "reroute_crossing" && scenario ? scenario.pedestrian.route : null;

  if (detail.error) return <main className="page"><ErrorNotice error={detail.error} onRetry={detail.reload} /></main>;

  return (
    <main className="page">
      <div className="crumbs"><Link to="/">Incidents</Link> / {scenario?.incident?.id ?? id}</div>
      <div className="page-head">
        <div>
          <h1>{scenario?.title ?? "Loading scenario"}</h1>
          {scenario && (
            <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
              {scenario.data_origin === "synthetic_reference" ? <SyntheticNote /> : <span className="tag neutral">{scenario.data_origin.replace("_", " ")}</span>}
              <span className="tag neutral">Scenario {scenario.id}</span>
            </div>
          )}
        </div>
      </div>

      <div className="workspace">
        <div className="stack">
          {scenario ? <IncidentReport scenario={scenario} /> : <Loading rows={2} height={120} />}
          {scenario && <Interpretation scenario={scenario} />}
          {scenario && <Parameters scenario={scenario} />}
        </div>

        <div className="stack">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "replay"} onClick={() => { setParams({}); setPreview(null); }}>Incident replay</button>
            <button role="tab" aria-selected={tab === "design"} onClick={() => setParams({ tab: "design" })}>Design an experiment</button>
          </div>
          {replay.loading && <Loading height={420} />}
          {replay.error && <ErrorNotice error={replay.error} onRetry={replay.reload} />}
          {replay.data && (
            preview ? (
              <Instrument
                data={preview.data}
                threshold={threshold}
                title={<>Preview: {preview.iv.label} <span className="muted">(replayed incident conditions)</span></>}
                ghosts={ghosts}
                ghostRoute={ghostRoute}
                actions={<button className="btn small" onClick={() => setPreview(null)}>Back to as-is replay</button>}
              />
            ) : (
              <Instrument data={replay.data} threshold={threshold} title="As-is layout, replayed incident" />
            )
          )}
          {previewError && <ErrorNotice error={previewError} />}
          {tab === "replay" && replay.data && scenario && (
            <ReplaySummary data={replay.data} scenario={scenario} onDesign={() => setParams({ tab: "design" })} />
          )}
          {tab === "design" && scenario && detail.data && (
            <ExperimentBuilder scenario={scenario} defaultEnvelope={detail.data.default_envelope} onPreview={showPreview} previewing={preview?.iv.id ?? null} />
          )}
        </div>
      </div>
    </main>
  );
}

function IncidentReport({ scenario }: { scenario: Scenario }) {
  const inc = scenario.incident;
  return (
    <section className="panel">
      <div className="panel-head"><h2>Incident report</h2>{inc && <span className="muted num">{inc.id}</span>}</div>
      <div className="panel-pad">
        {inc ? (
          <>
            <dl className="kv" style={{ marginTop: 0 }}>
              <dt>When</dt><dd>{inc.occurred_at}</dd>
              <dt>Where</dt><dd>{inc.location}</dd>
              {inc.reported_closest_approach_m && (
                <>
                  <dt>Closest approach</dt>
                  <dd className="num">
                    {inc.reported_closest_approach_m.value} m ± {inc.reported_closest_approach_m.uncertainty ?? "?"}{" "}
                    <ProvTag p={inc.reported_closest_approach_m.provenance} />
                  </dd>
                </>
              )}
            </dl>
            <hr className="rule" style={{ margin: "12px 0" }} />
            <p className="narrative">{inc.narrative}</p>
          </>
        ) : (
          <p className="muted">This scenario has no incident report attached.</p>
        )}
      </div>
    </section>
  );
}

function Interpretation({ scenario }: { scenario: Scenario }) {
  const [call, setCall] = useState<ModelCall | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.modelCalls(scenario.id).then((calls) => {
      setCall(calls.find((c) => c.task === "interpret_incident" && c.status !== "error") ?? null);
    }).catch(() => undefined);
  }, [scenario.id]);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setCall(await api.interpret(scenario.id));
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  };

  const params = (call?.result?.parameters as ProposedParameter[] | undefined) ?? [];
  const usable = params.filter((p) => p.status !== "rejected" && p.value != null && p.name !== "incident.closest_approach");

  const createVariant = async () => {
    const sc: Scenario = JSON.parse(JSON.stringify(scenario));
    for (const p of usable) {
      const [owner, field] = p.name.split(".") as ["forklift" | "pedestrian", string];
      const target = (sc[owner] as unknown as Record<string, Param>)[field];
      if (target) {
        (sc[owner] as unknown as Record<string, Param>)[field] = {
          ...target, value: p.value as number, provenance: p.provenance,
          source: `Nemotron interpretation (${call?.model_served}); evidence: ${p.evidence || p.rationale}`.slice(0, 400),
          uncertainty: null,
        };
      }
    }
    sc.id = `${scenario.id}-m${Date.now().toString(36).slice(-5)}`;
    sc.title = `${scenario.title} (model-assisted variant)`;
    sc.data_origin = "model_assisted";
    try {
      await api.saveScenario(sc, scenario.id);
      navigate(`/incidents/${sc.id}`);
    } catch (e) {
      setError(e as ApiError);
    }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Report interpretation</h2>
        <button className="btn small" onClick={run} disabled={busy || !scenario.incident}>
          {busy ? "Asking Nemotron…" : call ? "Interpret again" : "Interpret with Nemotron"}
        </button>
      </div>
      <div className="panel-pad">
        {!call && !busy && !error && (
          <p className="muted" style={{ marginBottom: 0 }}>
            Nemotron reads the report and proposes simulation inputs, each tied to a quote. Proposals are checked
            against the report text and physical limits, and are never applied without your say-so.
          </p>
        )}
        {busy && <Loading rows={2} height={22} />}
        {error && <ErrorNotice error={error} />}
        {call && !busy && (
          <div className="stack" style={{ gap: 10 }}>
            <div className="notice model" style={{ fontSize: "0.86rem" }}>
              Model-generated by <strong>{call.model_served ?? call.model_requested}</strong> via Nebius Token Factory
              {call.latency_ms != null && <>, {(call.latency_ms / 1000).toFixed(1)} s</>}
              {call.prompt_tokens != null && <>, {call.prompt_tokens + (call.completion_tokens ?? 0)} tokens</>}
              . <StatusTag status={call.status} />
            </div>
            {call.status === "invalid_output" && (
              <p className="muted">The model's output failed validation and was discarded: {call.error}</p>
            )}
            {typeof call.result?.summary === "string" && <p style={{ margin: 0 }}>{call.result.summary as string}</p>}
            {params.length > 0 && (
              <table className="data">
                <thead><tr><th>Parameter</th><th className="r">Value</th><th>Basis</th></tr></thead>
                <tbody>
                  {params.map((p, j) => (
                    <tr key={j}>
                      <td>
                        {p.name}
                        <div><StatusTag status={p.status} /></div>
                      </td>
                      <td className="r num">{p.value ?? "—"} {p.unit}</td>
                      <td>
                        <ProvTag p={p.provenance} />{" "}
                        {p.evidence ? <q style={{ color: "var(--ink-2)" }}>{p.evidence}</q> : <span className="muted">{p.rationale}</span>}
                        {p.validation_note && <div className="muted" style={{ fontSize: "0.84rem" }}>{p.validation_note}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {Array.isArray(call.result?.open_questions) && (call.result!.open_questions as string[]).length > 0 && (
              <div>
                <div className="label">Open questions for the investigator</div>
                <ul style={{ margin: "4px 0 0", paddingLeft: "1.1em" }}>
                  {(call.result!.open_questions as string[]).map((q, j) => <li key={j}>{q}</li>)}
                </ul>
              </div>
            )}
            {usable.length > 0 && (
              <div>
                <button className="btn small" onClick={createVariant}>Save as model-assisted variant</button>
                <span className="muted" style={{ fontSize: "0.84rem", marginLeft: 8 }}>
                  Creates a new scenario with {usable.length} proposed value{usable.length > 1 ? "s" : ""}; the reference stays unchanged.
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function Parameters({ scenario }: { scenario: Scenario }) {
  const rows: [string, Param][] = [
    ["Forklift speed", scenario.forklift.cruise_speed],
    ["Braking deceleration", scenario.forklift.braking_decel],
    ["Driver reaction time", scenario.forklift.reaction_time],
    ["Forklift acceleration", scenario.forklift.max_accel],
    ["Walking speed", scenario.pedestrian.walking_speed],
    ["Pedestrian start time", scenario.pedestrian.start_time],
  ];
  return (
    <section className="panel">
      <div className="panel-head"><h2>Scenario parameters</h2></div>
      <div className="panel-pad" style={{ paddingTop: 6 }}>
        <table className="data">
          <tbody>
            {rows.map(([label, p]) => (
              <tr key={label}>
                <td>{label}<div className="muted" style={{ fontSize: "0.82rem" }}>{p.source}</div></td>
                <td className="r num" style={{ whiteSpace: "nowrap" }}>
                  {p.value} {p.unit}{p.uncertainty != null && <span className="muted"> ±{p.uncertainty}</span>}
                </td>
                <td><ProvTag p={p.provenance} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: "0.84rem", margin: "10px 0 0" }}>
          Forklift {scenario.forklift.length_m} × {scenario.forklift.width_m} m footprint · pedestrian radius{" "}
          {scenario.pedestrian.radius_m} m · {scenario.calibration.scale_source}.
        </p>
      </div>
    </section>
  );
}

function ReplaySummary({ data, scenario, onDesign }: { data: SimulationPayload; scenario: Scenario; onDesign: () => void }) {
  const reported = scenario.incident?.reported_closest_approach_m;
  const sim = data.metrics.min_moving_separation_m;
  const within = reported && sim != null && reported.uncertainty != null && Math.abs(sim - reported.value) <= reported.uncertainty;
  return (
    <section className="panel panel-pad">
      <div className="split" style={{ alignItems: "start" }}>
        <div>
          <h3 style={{ marginBottom: 6 }}>Replay against the report</h3>
          <p style={{ marginBottom: 6 }}>
            Simulated minimum clearance <strong className="num">{fmt(sim)} m</strong>
            {reported && <> against a reported <span className="num">{reported.value} ± {reported.uncertainty} m</span></>}.
            {reported && (within ? " The replay sits inside the report's uncertainty." : " The replay falls outside the report's uncertainty; check the estimated inputs.")}
          </p>
          <p className="muted" style={{ fontSize: "0.86rem", margin: 0 }}>
            Engine {data.engine_version}, {data.frames_full} steps. Config <span className="hash">{data.config_hash.slice(0, 12)}</span>.
          </p>
        </div>
        <div>
          <h3 style={{ marginBottom: 6 }}>Next</h3>
          <p style={{ marginBottom: 10 }}>
            A change that fixes this one replay can still fail at a different speed, timing or stack position.
            Design an experiment to stress-test candidate changes.
          </p>
          <button className="btn primary" onClick={onDesign}>Design an experiment</button>
        </div>
      </div>
    </section>
  );
}
