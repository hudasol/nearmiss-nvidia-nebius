import type { ApiError } from "../api";

export function Loading({ rows = 1, height = 56 }: { rows?: number; height?: number }) {
  return (
    <div aria-busy="true" aria-live="polite" style={{ display: "grid", gap: 10 }}>
      <span className="sr-only" style={{ position: "absolute", left: -9999 }}>Loading</span>
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton" style={{ height }} />)}
    </div>
  );
}

export function ErrorNotice({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="notice error" role="alert">
      <strong>{error.message}</strong>{" "}
      <span className="code" style={{ fontFamily: "var(--font-hash)", fontSize: "0.8em" }}>({error.code})</span>
      {onRetry && (
        <button className="btn small" style={{ marginLeft: 12 }} onClick={onRetry}>Try again</button>
      )}
    </div>
  );
}
