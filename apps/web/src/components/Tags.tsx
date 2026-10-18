import type { Outcome, Provenance } from "../types";

const OUTCOME_TEXT: Record<Outcome, string> = { collision: "Collision", near_miss: "Near miss", clear: "Clear" };

export function OutcomeTag({ outcome }: { outcome: Outcome }) {
  return <span className={`tag ${outcome}`}>{OUTCOME_TEXT[outcome]}</span>;
}

const STATUS_TEXT: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  rejected: "Rejected by compiler",
  accepted: "Accepted",
  ok: "OK",
  invalid_output: "Invalid output",
  error: "Error",
  valid: "Valid",
  downgraded: "Downgraded",
};

export function StatusTag({ status, text }: { status: string; text?: string }) {
  return <span className={`tag ${status}`}>{text ?? STATUS_TEXT[status] ?? status}</span>;
}

export function DecisionTag({ decision }: { decision: "accepted" | "rejected" | string }) {
  return (
    <span className={`tag ${decision === "rejected" ? "rejected" : "accepted"}`}>
      {decision === "rejected" ? "Failure found" : "No failure found"}
    </span>
  );
}

export function ProvTag({ p }: { p: Provenance }) {
  const title = {
    observed: "Observed: measured or dimensioned on a drawing",
    estimated: "Estimated: derived from telematics, CCTV timing or a statement",
    assumed: "Assumed: engineering default, not measured",
  }[p];
  return <span className={`prov ${p}`} title={title}>{p}</span>;
}

export function SyntheticNote() {
  return (
    <span className="tag neutral" title="Illustrative reference data, not measurements from a real site">
      Synthetic reference data
    </span>
  );
}
