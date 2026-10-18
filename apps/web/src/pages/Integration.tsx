import { useState } from "react";
import { api, ApiError } from "../api";
import { useAsync } from "../hooks";
import { StatusTag } from "../components/Tags";
import { ErrorNotice, Loading } from "../components/States";

/* eslint-disable @typescript-eslint/no-explicit-any */
export function IntegrationPage() {
  const status = useAsync(() => api.nebius(), []);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const verify = async () => {
    setBusy(true);
    setError(null);
    try { setResult(await api.verifyNebius()); status.reload(); } catch (e) { setError(e as ApiError); status.reload(); } finally { setBusy(false); }
  };

  const s = status.data;
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Model integration</h1>
          <p className="sub">
            NearMiss calls an NVIDIA Nemotron model through Nebius Token Factory to interpret incident reports and to
            suggest layout changes. Simulation, evaluation and reports work without it.
          </p>
        </div>
      </div>
      {status.loading && <Loading rows={2} />}
      {status.error && <ErrorNotice error={status.error} onRetry={status.reload} />}
      {s && (
        <div className="split">
          <section className="panel">
            <div className="panel-head"><h2>Nebius Token Factory</h2><StatusTag status={s.configured ? "ok" : "warning"} text={s.configured ? "API key set" : "No API key"} /></div>
            <div className="panel-pad">
              <dl className="kv" style={{ marginTop: 0 }}>
                <dt>Endpoint</dt><dd className="hash">{s.base_url}</dd>
                <dt>Configured model</dt><dd>{s.model_configured}</dd>
                <dt>Last check</dt>
                <dd>{s.last_verification ? <>{new Date(s.last_verification.created_at).toLocaleString()} <StatusTag status={s.last_verification.status} /> {s.last_verification.model_served}</> : "never"}</dd>
              </dl>
              {!s.configured && (
                <p className="notice warn" style={{ marginTop: 12 }}>
                  Add NEBIUS_API_KEY to the server's .env file and restart the API. The key stays on the server and is never sent to this page.
                </p>
              )}
              <button className="btn primary" style={{ marginTop: 12 }} onClick={verify} disabled={busy}>{busy ? "Checking…" : "Check connection and model"}</button>
              {error && <div style={{ marginTop: 10 }}><ErrorNotice error={error} /></div>}
              {result && (
                <div className="notice" style={{ marginTop: 10 }}>
                  Connected in {result.latency_ms} ms. Using <strong>{result.model_served}</strong>. NVIDIA models available to this key:{" "}
                  {(result.result?.nvidia_models ?? []).join(", ") || "none"}.
                </div>
              )}
            </div>
          </section>
          <section className="panel">
            <div className="panel-head"><h2>Usage recorded by this server</h2></div>
            <div className="panel-pad">
              <dl className="kv" style={{ marginTop: 0 }}>
                <dt>Model calls</dt><dd className="num">{s.usage.calls} ({s.usage.ok} with valid output)</dd>
                <dt>Prompt tokens</dt><dd className="num">{s.usage.prompt_tokens.toLocaleString()}</dd>
                <dt>Completion tokens</dt><dd className="num">{s.usage.completion_tokens.toLocaleString()}</dd>
                <dt>Mean latency</dt><dd className="num">{s.usage.mean_latency_ms != null ? `${(s.usage.mean_latency_ms / 1000).toFixed(1)} s` : "—"}</dd>
                <dt>Last success</dt><dd>{s.usage.last_success ? `${new Date(s.usage.last_success.created_at).toLocaleString()}, ${s.usage.last_success.model_served}` : "none yet"}</dd>
              </dl>
              <p className="muted" style={{ fontSize: "0.86rem", marginTop: 12 }}>
                Token counts come from the API responses. Model inference cost is reported separately from simulation runtime.
              </p>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
