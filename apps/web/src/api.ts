import type {
  CompilationResult,
  Experiment,
  ModelCall,
  Reference,
  RunRecord,
  Scenario,
  ScenarioSummary,
  SimulationPayload,
  StressAxis,
} from "./types";

// Report, benchmark and integration payloads are rendered as documents and typed loosely.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Doc = Record<string, any>;

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), init?.timeoutMs ?? 90_000);
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    clearTimeout(timer);
    if ((err as Error).name === "AbortError") {
      throw new ApiError(0, "timeout", "The server did not answer in time.");
    }
    throw new ApiError(0, "network", "Cannot reach the NearMiss API. Is it running on port 8000?");
  }
  clearTimeout(timer);
  const text = await res.text();
  let body: Record<string, any> | null = null; // eslint-disable-line @typescript-eslint/no-explicit-any
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(res.status, "bad_response", `The API returned an unexpected response (HTTP ${res.status}). Check that the API server is running and reachable at /api.`);
  }
  if (!res.ok) {
    const e = body?.error;
    throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? `HTTP ${res.status}`, e?.details);
  }
  return body as T;
}

const post = <T>(path: string, data?: unknown, timeoutMs?: number) =>
  request<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data), timeoutMs });

export const api = {
  health: () => request<{ status: string; version: string; model_integration: { configured: boolean } }>("/health"),
  reference: () => request<Reference>("/reference"),
  scenarios: () => request<ScenarioSummary[]>("/scenarios"),
  scenario: (id: string) =>
    request<{ scenario: Scenario; default_envelope: StressAxis[]; geometry_findings: unknown[] }>(
      `/scenarios/${encodeURIComponent(id)}`,
    ),
  saveScenario: (scenario: Scenario, parentId?: string) =>
    post<{ id: string; content_hash: string }>("/scenarios", { scenario, parent_id: parentId ?? null }),
  replay: (id: string) => request<SimulationPayload>(`/scenarios/${encodeURIComponent(id)}/replay`),
  simulate: (body: { scenario_id: string; intervention?: unknown; stress_point?: Record<string, number> }) =>
    post<SimulationPayload>("/simulate", body),
  interpret: (id: string) => post<ModelCall & { call_id: string }>(`/scenarios/${encodeURIComponent(id)}/interpret`, {}, 120_000),
  candidates: (id: string) => post<ModelCall & { call_id: string }>(`/scenarios/${encodeURIComponent(id)}/candidates`, undefined, 120_000),
  modelCalls: (id: string) => request<ModelCall[]>(`/scenarios/${encodeURIComponent(id)}/model-calls`),
  compile: (spec: unknown) => post<CompilationResult>("/experiments/compile", spec),
  createExperiment: (spec: unknown) => post<{ experiment: Experiment; duplicate: boolean }>("/experiments", spec),
  experiments: () => request<Experiment[]>("/experiments"),
  experiment: (id: string) => request<Experiment>(`/experiments/${id}`),
  cancel: (id: string) => post<{ experiment: Experiment }>(`/experiments/${id}/cancel`),
  runs: (id: string, arm?: string, strategy?: string) => {
    const q = new URLSearchParams();
    if (arm) q.set("arm", arm);
    if (strategy) q.set("strategy", strategy);
    return request<RunRecord[]>(`/experiments/${id}/runs?${q}`);
  },
  replayRun: (id: string, arm: string, strategy: string, idx: number) =>
    request<SimulationPayload>(`/experiments/${id}/runs/${encodeURIComponent(arm)}/${strategy}/${idx}/replay`),
  report: (id: string) => request<Doc>(`/experiments/${id}/report`),
  benchmark: () => request<Doc>("/benchmark"),
  nebius: () => request<Doc>("/integrations/nebius"),
  verifyNebius: () => post<Doc>("/integrations/nebius/verify", undefined, 60_000),
};
