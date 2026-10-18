import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useReference } from "../hooks";
import type { CandidateCheck, CompilationResult, Finding, Intervention, ModelCall, Scenario, StressAxis } from "../types";
import { StatusTag } from "./Tags";
import { ErrorNotice } from "./States";

type Kind = "relocate_obstruction" | "remove_obstruction" | "reroute_crossing" | "signage";

interface Arm {
  intervention: Intervention;
  source: "you" | "nemotron";
}

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

let counter = 0;
const nextId = (p: string) => `${p}-${Date.now().toString(36).slice(-4)}${(counter++).toString(36)}`;

export function ExperimentBuilder({
  scenario,
  defaultEnvelope,
  onPreview,
  previewing,
}: {
  scenario: Scenario;
  defaultEnvelope: StressAxis[];
  onPreview: (iv: Intervention | null) => void;
  previewing: string | null;
}) {
  const ref = useReference();
  const navigate = useNavigate();
  const movable = scenario.obstructions.filter((o) => o.movable);
  const tpl = scenario.crossing_template;

  const [title, setTitle] = useState(`Layout changes for ${scenario.incident?.id ?? scenario.id}`);
  const [arms, setArms] = useState<Arm[]>([]);
  const [axes, setAxes] = useState<(StressAxis & { on: boolean })[]>(() => defaultEnvelope.map((a) => ({ ...a, on: true })));
  const [claim, setClaim] = useState<"robust_clearance" | "nominal_improvement">("robust_clearance");
  const [metrics, setMetrics] = useState<string[]>(["min_moving_separation_m", "collision"]);
  const [budget, setBudget] = useState(48);
  const [seed, setSeed] = useState(1);
  const [compare, setCompare] = useState(true);
  const [check, setCheck] = useState<CompilationResult | null>(null);
  const [busy, setBusy] = useState<"check" | "run" | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  // Draft intervention form
  const [kind, setKind] = useState<Kind>(movable.length ? "relocate_obstruction" : "reroute_crossing");
  const [obsId, setObsId] = useState(movable[0]?.id ?? "");
  const [dx, setDx] = useState(-3);
  const [dy, setDy] = useState(0);
  const [crossX, setCrossX] = useState(tpl ? Math.round((tpl.min_crossing_x_m + tpl.max_crossing_x_m) / 2) : 0);

  const draft = useMemo<Intervention | null>(() => {
    const o = scenario.obstructions.find((x) => x.id === obsId);
    if (kind === "relocate_obstruction" && o) {
      const parts = [dx ? `${Math.abs(dx)} m ${dx < 0 ? "west" : "east"}` : "", dy ? `${Math.abs(dy)} m ${dy < 0 ? "south" : "north"}` : ""].filter(Boolean);
      return { type: kind, id: nextId("move"), label: `Move ${lowerFirst(o.label)} ${parts.join(", ") || "(no offset)"}`, obstruction_id: o.id, offset_m: [dx, dy] };
    }
    if (kind === "remove_obstruction" && o) return { type: kind, id: nextId("remove"), label: `Remove ${lowerFirst(o.label)}`, obstruction_id: o.id };
    if (kind === "reroute_crossing") return { type: kind, id: nextId("cross"), label: `Move the crossing to x = ${crossX} m`, crossing_x_m: crossX };
    if (kind === "signage") return { type: "signage", id: nextId("sign"), label: "Add a 'look left' sign at the crossing" };
    return null;
  }, [kind, obsId, dx, dy, crossX, scenario.obstructions]);

  const addArm = (iv: Intervention, source: Arm["source"]) => {
    if (arms.length >= 6) return;
    setArms((a) => [...a, { intervention: iv, source }]);
    setCheck(null);
  };

  const spec = useMemo(() => ({
    title,
    scenario_id: scenario.id,
    arms: arms.map((a) => ({ intervention: a.intervention })),
    stress: claim === "robust_clearance" ? { axes: axes.filter((a) => a.on).map((a) => ({ name: a.name, low: a.low, high: a.high, unit: a.unit })) } : null,
    claim,
    metrics,
    strategy: "targeted_search",
    compare_conventional: compare,
    budget_per_arm: budget,
    seed,
  }), [title, scenario.id, arms, axes, claim, metrics, compare, budget, seed]);

  const runCheck = async () => {
    setBusy("check");
    setError(null);
    try { setCheck(await api.compile(spec)); } catch (e) { setError(e as ApiError); } finally { setBusy(null); }
  };

  const submit = async () => {
    setBusy("run");
    setError(null);
    try {
      const res = await api.createExperiment(spec);
      navigate(`/experiments/${res.experiment.id}`);
    } catch (e) {
      setError(e as ApiError);
      setBusy(null);
    }
  };

  const applyCorrections = () => {
    const c = check?.corrected_spec as typeof spec | null;
    if (!c) return;
    const keep = new Set(c.arms.map((a) => (a.intervention as Intervention).id));
    setArms((prev) => prev.filter((a) => keep.has(a.intervention.id)));
    if (c.stress) {
      setAxes((prev) => {
        const byName = new Map(c.stress!.axes.map((a) => [a.name, a]));
        const merged = prev.map((a) => (byName.has(a.name) ? { ...byName.get(a.name)!, on: true } : { ...a, on: false }));
        return merged;
      });
    }
    setMetrics(c.metrics);
    setBudget(c.budget_per_arm);
    setCheck(null);
  };

  const errors = check?.findings.filter((f) => f.severity === "error") ?? [];
  const totalRuns = (arms.length + 1) * (claim === "robust_clearance" ? budget * (compare ? 2 : 1) : 1);

  return (
    <section className="panel" aria-label="Experiment design">
      <div className="panel-head">
        <h2>Experiment</h2>
        <input className="inline" style={{ width: "min(420px, 60%)", textAlign: "left" }} value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Experiment title" />
      </div>

      <div className="panel-pad stack" style={{ gap: 20 }}>
        <div>
          <h3 style={{ marginBottom: 4 }}>Changes to test</h3>
          <p className="muted" style={{ fontSize: "0.9rem" }}>
            Each change is simulated against the as-is layout under identical conditions. Up to six.
          </p>
          {arms.length === 0 && <p className="muted" style={{ fontStyle: "italic" }}>No changes added yet. Draft one below or ask Nemotron for suggestions.</p>}
          {arms.length > 0 && (
            <table className="data" style={{ marginBottom: 12 }}>
              <thead><tr><th>Change</th><th>Proposed by</th><th /></tr></thead>
              <tbody>
                {arms.map((a) => (
                  <tr key={a.intervention.id} className={previewing === a.intervention.id ? "selected" : undefined}>
                    <td>{a.intervention.label}{a.intervention.rationale && <div className="muted" style={{ fontSize: "0.84rem" }}>{a.intervention.rationale}</div>}</td>
                    <td>{a.source === "nemotron" ? <span className="tag neutral">Nemotron, untested</span> : "You"}</td>
                    <td className="r" style={{ whiteSpace: "nowrap" }}>
                      {a.intervention.type !== "signage" && <button className="btn small quiet" onClick={() => onPreview(a.intervention)}>Preview</button>}
                      <button className="btn small quiet" onClick={() => { setArms((x) => x.filter((y) => y !== a)); setCheck(null); }} aria-label={`Remove ${a.intervention.label}`}>Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="panel" style={{ background: "var(--surface)" }}>
            <div className="panel-pad" style={{ display: "grid", gap: 12 }}>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "end" }}>
                <label className="field">
                  <span className="label">Type of change</span>
                  <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
                    <option value="relocate_obstruction" disabled={!movable.length}>Move an obstruction</option>
                    <option value="remove_obstruction" disabled={!movable.length}>Remove an obstruction</option>
                    <option value="reroute_crossing" disabled={!tpl}>Move the crossing</option>
                    <option value="signage">Signage or floor marking</option>
                  </select>
                </label>
                {(kind === "relocate_obstruction" || kind === "remove_obstruction") && (
                  <label className="field">
                    <span className="label">Obstruction</span>
                    <select value={obsId} onChange={(e) => setObsId(e.target.value)}>
                      {movable.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                    </select>
                  </label>
                )}
                {kind === "relocate_obstruction" && (
                  <>
                    <label className="field"><span className="label">East–west shift, m</span>
                      <input type="number" step={0.5} min={-15} max={15} value={dx} onChange={(e) => setDx(Number(e.target.value))} style={{ width: 90 }} /></label>
                    <label className="field"><span className="label">North–south shift, m</span>
                      <input type="number" step={0.5} min={-10} max={10} value={dy} onChange={(e) => setDy(Number(e.target.value))} style={{ width: 90 }} /></label>
                  </>
                )}
                {kind === "reroute_crossing" && tpl && (
                  <label className="field" style={{ minWidth: 240, flex: 1 }}>
                    <span className="label">Crossing position x = {crossX} m (walkable {tpl.min_crossing_x_m}–{tpl.max_crossing_x_m} m)</span>
                    <input type="range" min={tpl.min_crossing_x_m} max={tpl.max_crossing_x_m} step={0.5} value={crossX} onChange={(e) => setCrossX(Number(e.target.value))} />
                  </label>
                )}
              </div>
              {kind === "signage" && (
                <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>
                  Behavioural measures can be added, but the compiler will reject them: the simulator has no validated
                  model of how a sign changes behaviour.
                </p>
              )}
              <div style={{ display: "flex", gap: 8 }}>
                {draft && draft.type !== "signage" && <button className="btn small" onClick={() => onPreview(draft)}>Preview in the replay</button>}
                <button className="btn small primary" disabled={!draft || arms.length >= 6} onClick={() => draft && addArm(draft, "you")}>Add change</button>
              </div>
            </div>
          </div>
          <Suggestions scenario={scenario} onAdd={(iv) => addArm(iv, "nemotron")} onPreview={onPreview} />
        </div>

        <div>
          <h3 style={{ marginBottom: 4 }}>What the experiment claims</h3>
          <div style={{ display: "grid", gap: 6 }}>
            <label style={{ display: "flex", gap: 8 }}>
              <input type="radio" name="claim" checked={claim === "robust_clearance"} onChange={() => setClaim("robust_clearance")} />
              <span><strong>Holds under changing conditions.</strong> <span className="muted">Clearance stays above {ref?.criteria.near_miss_threshold_m ?? 0.5} m with no contact across the whole stress envelope.</span></span>
            </label>
            <label style={{ display: "flex", gap: 8 }}>
              <input type="radio" name="claim" checked={claim === "nominal_improvement"} onChange={() => setClaim("nominal_improvement")} />
              <span><strong>Fixes the replayed incident only.</strong> <span className="muted">One replay per change. Says nothing about other conditions.</span></span>
            </label>
          </div>
        </div>

        {claim === "robust_clearance" && (
          <div>
            <h3 style={{ marginBottom: 4 }}>Stress envelope</h3>
            <p className="muted" style={{ fontSize: "0.9rem" }}>Conditions to vary. Defaults are centred on this incident.</p>
            <div className="table-scroll">
              <table className="data">
                <thead><tr><th>Vary</th><th>Condition</th><th className="r">From</th><th className="r">To</th><th>Unit</th></tr></thead>
                <tbody>
                  {axes.map((a, j) => {
                    const meta = ref?.stress_axes.find((m) => m.name === a.name);
                    return (
                      <tr key={a.name}>
                        <td><input type="checkbox" checked={a.on} aria-label={`Vary ${meta?.description ?? a.name}`} onChange={(e) => { setAxes((x) => x.map((y, k) => (k === j ? { ...y, on: e.target.checked } : y))); setCheck(null); }} /></td>
                        <td>{meta?.description ?? a.name}{meta?.kind === "offset" && <span className="muted"> (shift from incident)</span>}</td>
                        <td className="r"><input className="inline" type="number" step={0.05} value={a.low} disabled={!a.on} onChange={(e) => { setAxes((x) => x.map((y, k) => (k === j ? { ...y, low: Number(e.target.value) } : y))); setCheck(null); }} aria-label={`${a.name} from`} /></td>
                        <td className="r"><input className="inline" type="number" step={0.05} value={a.high} disabled={!a.on} onChange={(e) => { setAxes((x) => x.map((y, k) => (k === j ? { ...y, high: Number(e.target.value) } : y))); setCheck(null); }} aria-label={`${a.name} to`} /></td>
                        <td className="muted">{a.unit}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <details>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>Method settings</summary>
          <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginTop: 12, alignItems: "end" }}>
            <label className="field"><span className="label">Runs per change and workflow</span>
              <input type="number" min={1} max={256} value={budget} onChange={(e) => { setBudget(Number(e.target.value)); setCheck(null); }} style={{ width: 100 }} /></label>
            <label className="field"><span className="label">Seed</span>
              <input type="number" min={0} value={seed} onChange={(e) => { setSeed(Number(e.target.value)); setCheck(null); }} style={{ width: 100 }} /></label>
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} />
              Also run a conventional parameter sweep with the same budget
            </label>
          </div>
          <div style={{ marginTop: 12 }}>
            <span className="label">Metrics recorded for the decision</span>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 4 }}>
              {(ref?.metrics ?? []).map((m) => (
                <label key={m.name} style={{ display: "flex", gap: 6, alignItems: "center" }} title={m.description}>
                  <input type="checkbox" checked={metrics.includes(m.name)} onChange={(e) => { setMetrics((x) => (e.target.checked ? [...x, m.name] : x.filter((y) => y !== m.name))); setCheck(null); }} />
                  {m.name} <span className="muted">[{m.unit}]</span>
                </label>
              ))}
            </div>
          </div>
        </details>

        <div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn" onClick={runCheck} disabled={busy !== null || arms.length === 0}>{busy === "check" ? "Checking…" : "Check experiment"}</button>
            <button className="btn primary" onClick={submit} disabled={busy !== null || arms.length === 0 || (check !== null && check.status === "rejected")}>
              {busy === "run" ? "Submitting…" : "Run experiment"}
            </button>
            <span className="muted num" style={{ fontSize: "0.9rem" }}>{arms.length ? `${totalRuns} simulation runs` : "Add at least one change"}</span>
          </div>
          {error && <div style={{ marginTop: 10 }}><ErrorNotice error={error} /></div>}
          {check && <CheckResult check={check} errors={errors} onApply={applyCorrections} />}
        </div>
      </div>
    </section>
  );
}

function CheckResult({ check, errors, onApply }: { check: CompilationResult; errors: Finding[]; onApply: () => void }) {
  const warnings = check.findings.filter((f) => f.severity !== "error");
  return (
    <div style={{ marginTop: 14 }} className="stack">
      {check.status === "accepted" ? (
        <div className="notice">
          <StatusTag status="accepted" text="Experiment is valid" /> {check.plan?.total_runs} runs across {check.plan?.arms.length} arms
          (including the as-is baseline). Plan <span className="hash">{check.plan?.plan_hash.slice(0, 12)}</span>.
        </div>
      ) : (
        <div className="notice error">
          The compiler rejected this experiment: {errors.length} problem{errors.length > 1 ? "s" : ""} to fix before it can run.
          {check.corrected_spec && (
            <div style={{ marginTop: 8, color: "var(--ink)" }}>
              A corrected version is available{check.corrected_spec_status === "accepted" ? " and passes all checks" : ""}.{" "}
              <button className="btn small" onClick={onApply}>Apply corrections</button>
            </div>
          )}
        </div>
      )}
      <div className="findings">
        {[...errors, ...warnings].map((f, j) => (
          <div key={j} className={`finding ${f.severity}`}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
              <strong>{f.severity === "error" ? "Error" : "Warning"}</strong>
              <span className="code">{f.code} · {f.path}</span>
            </div>
            <div>{f.message}</div>
            {f.hint && <div className="hint">{f.hint}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

function Suggestions({ scenario, onAdd, onPreview }: { scenario: Scenario; onAdd: (iv: Intervention) => void; onPreview: (iv: Intervention) => void }) {
  const [call, setCall] = useState<ModelCall | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [added, setAdded] = useState<Set<number>>(new Set());
  const ask = async () => {
    setBusy(true);
    setError(null);
    setAdded(new Set());
    try { setCall(await api.candidates(scenario.id)); } catch (e) { setError(e as ApiError); } finally { setBusy(false); }
  };
  const candidates = (call?.result?.candidates as CandidateCheck[] | undefined) ?? [];
  return (
    <div style={{ marginTop: 12 }}>
      <button className="btn small" onClick={ask} disabled={busy}>{busy ? "Asking Nemotron…" : "Suggest changes with Nemotron"}</button>
      {error && <div style={{ marginTop: 8 }}><ErrorNotice error={error} /></div>}
      {error?.code === "not_configured" && (
        <p className="muted" style={{ fontSize: "0.86rem", marginTop: 6 }}>Set NEBIUS_API_KEY on the server to enable suggestions. Everything else works without it.</p>
      )}
      {call && (
        <div className="notice model" style={{ marginTop: 10 }}>
          <div style={{ fontSize: "0.86rem", marginBottom: 8 }}>
            Model-generated by <strong>{call.model_served}</strong>. Suggestions are checked against the site geometry here;
            whether they work is decided only by simulation. <StatusTag status={call.status} />
          </div>
          {call.status === "invalid_output" && <div className="muted">Output failed validation and was discarded: {call.error}</div>}
          <table className="data">
            <tbody>
              {candidates.map((c) => (
                <tr key={c.index}>
                  <td>
                    {(c.intervention?.label ?? String(c.raw.label ?? c.raw.type))}
                    {c.intervention?.rationale && <div className="muted" style={{ fontSize: "0.84rem" }}>{c.intervention.rationale}</div>}
                    {c.reasons.length > 0 && <div style={{ fontSize: "0.84rem", color: "var(--near)" }}>{c.reasons.join("; ")}</div>}
                  </td>
                  <td><StatusTag status={c.status} text={c.status === "valid" ? "Feasible" : "Not feasible"} /></td>
                  <td className="r" style={{ whiteSpace: "nowrap" }}>
                    {c.status === "valid" && c.intervention && (
                      <>
                        <button className="btn small quiet" onClick={() => onPreview(c.intervention!)}>Preview</button>
                        <button className="btn small" disabled={added.has(c.index)} onClick={() => { onAdd({ ...c.intervention!, id: `${c.intervention!.id}-${call.id.slice(-4)}` }); setAdded((s) => new Set(s).add(c.index)); }}>
                          {added.has(c.index) ? "Added" : "Add"}
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
