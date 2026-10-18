// Types mirror the API's Pydantic schemas (nearmiss/domain, nearmiss/compiler, nearmiss/api).

export type Point = [number, number];
export type Provenance = "observed" | "estimated" | "assumed";

export interface Param {
  value: number;
  unit: string;
  provenance: Provenance;
  source: string;
  uncertainty: number | null;
}

export interface Rect {
  center: Point;
  length: number;
  width: number;
  rotation_deg: number;
}

export interface Obstruction {
  id: string;
  label: string;
  kind: "pallet_stack" | "rack_end" | "wall" | "equipment";
  geometry: Rect;
  height_m: number;
  opaque: boolean;
  movable: boolean;
  provenance: Provenance;
  source: string;
}

export interface LayoutZone {
  id: string;
  label: string;
  kind: "aisle" | "walkway" | "crossing" | "rack" | "dock";
  polygon: Point[];
}

export interface Scenario {
  schema_version: string;
  id: string;
  title: string;
  description: string;
  data_origin: "synthetic_reference" | "user_defined" | "model_assisted";
  bounds: { width_m: number; height_m: number };
  calibration: {
    frame: string;
    scale_source: string;
    reference_measurements: { label: string; value_m: number; provenance: Provenance; source: string }[];
  };
  layout: LayoutZone[];
  obstructions: Obstruction[];
  forklift: {
    id: string;
    label: string;
    length_m: number;
    width_m: number;
    eye_offset_m: Point;
    route: Point[];
    cruise_speed: Param;
    braking_decel: Param;
    max_accel: Param;
    reaction_time: Param;
    detection_range_m: number;
    field_of_view_deg: number;
    hazard_lateral_margin_m: number;
    prediction_horizon_s: number;
  };
  pedestrian: {
    id: string;
    label: string;
    radius_m: number;
    route: Point[];
    walking_speed: Param;
    start_time: Param;
    behaviour: string;
  };
  simulation: { dt_s: number; duration_s: number };
  crossing_template: {
    approach_y_m: number;
    exit_y_m: number;
    min_crossing_x_m: number;
    max_crossing_x_m: number;
  } | null;
  incident: {
    id: string;
    occurred_at: string;
    location: string;
    summary: string;
    narrative: string;
    reported_closest_approach_m: Param | null;
  } | null;
  tags: string[];
}

export interface ScenarioSummary {
  id: string;
  origin: string;
  title: string;
  data_origin: Scenario["data_origin"];
  tags: string[];
  incident: Scenario["incident"];
}

export interface Trajectory {
  engine_version: string;
  dt: number;
  t: number[];
  forklift_x: number[];
  forklift_y: number[];
  forklift_heading: number[];
  forklift_speed: number[];
  driver_state: string[];
  pedestrian_x: number[];
  pedestrian_y: number[];
  pedestrian_visible: boolean[];
  hazard: boolean[];
  events: { t: number; kind: string; detail: string }[];
}

export interface RunMetrics {
  min_separation_m: number;
  min_moving_separation_m: number | null;
  t_min_moving_separation_s: number | null;
  collision: boolean;
  first_contact_s: number | null;
  min_ttc_s: number | null;
  pet_s: number | null;
  forklift_peak_speed_mps: number;
}

export type Outcome = "collision" | "near_miss" | "clear";

export interface SimulationPayload {
  scenario: Scenario;
  trajectory: Trajectory;
  full_trajectory_hash: string;
  config_hash: string;
  frames_full: number;
  separation_m: number[];
  metrics: RunMetrics;
  outcome: Outcome;
  conflict_zone: { polygon: Point[] } | null;
  engine_version: string;
  verification?: {
    recorded_engine_version: string;
    current_engine_version: string;
    recorded_config_hash: string;
    recorded_trajectory_hash: string;
    config_hash_match: boolean;
    trajectory_hash_match: boolean;
  };
  run?: RunRecord;
}

export interface StressAxis {
  name: string;
  low: number;
  high: number;
  unit: string;
}

export interface StressAxisMeta {
  name: string;
  unit: string;
  kind: "absolute" | "offset";
  description: string;
  limits: [number, number];
}

export interface Criteria {
  id: string;
  near_miss_threshold_m: number;
  intervention_rule: string;
  rationale: string;
  run_outcomes: Record<string, string>;
}

export interface MetricDefinition {
  name: string;
  unit: string;
  description: string;
  supports_clearance_claim: boolean;
}

export interface Reference {
  criteria: Criteria;
  metrics: MetricDefinition[];
  stress_axes: StressAxisMeta[];
}

export type Intervention =
  | { type: "relocate_obstruction"; id: string; label: string; rationale?: string; origin?: string; obstruction_id: string; offset_m: Point }
  | { type: "remove_obstruction"; id: string; label: string; rationale?: string; origin?: string; obstruction_id: string }
  | { type: "reroute_crossing"; id: string; label: string; rationale?: string; origin?: string; crossing_x_m: number }
  | { type: "floor_marking" | "signage" | "training"; id: string; label: string; rationale?: string; origin?: string; description?: string };

export interface Finding {
  code: string;
  severity: "error" | "warning" | "info";
  path: string;
  message: string;
  hint: string;
  correctable: boolean;
}

export interface CompilationResult {
  status: "accepted" | "rejected";
  findings: Finding[];
  original_spec: Record<string, unknown>;
  corrected_spec: Record<string, unknown> | null;
  corrected_spec_status: "accepted" | "rejected" | null;
  plan: { total_runs: number; plan_hash: string; strategies: string[]; arms: { arm_id: string; label: string }[] } | null;
}

export type ExperimentStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "rejected";

export interface ArmSummary {
  arm_id: string;
  label: string;
  is_baseline: boolean;
  decisions: Record<string, "accepted" | "rejected">;
  unsafe_runs: Record<string, number>;
  first_failure_index: Record<string, number | null>;
  verdict: string;
  verdict_text: string;
}

export interface PlanArm {
  arm_id: string;
  label: string;
  is_baseline: boolean;
  intervention: Intervention | null;
  scenario: { id: string; title: string };
  changes: { path: string; before: unknown; after: unknown }[];
}

export interface Experiment {
  id: string;
  title: string;
  scenario_id: string;
  status: ExperimentStatus;
  spec_hash: string;
  plan_hash: string | null;
  spec: Record<string, unknown>;
  compilation: CompilationResult;
  plan: {
    claim: string;
    claim_text: string;
    stress: { axes: StressAxis[] } | null;
    strategies: string[];
    budget_per_arm: number;
    seed: number;
    arms: PlanArm[];
    total_runs: number;
    engine_version: string;
    strategy_version: string;
    criteria_id: string;
  } | null;
  progress_done: number;
  progress_total: number;
  result_summary: { primary_strategy: string; summaries: ArmSummary[] } | null;
  error: string | null;
  runtime_s: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  strategy_results?: {
    arm_id: string;
    strategy: string;
    decision: string;
    unsafe_runs: number;
    first_failure_index: number | null;
    worst_run_index: number;
    runtime_s: number;
    budget: number;
  }[];
}

export interface RunRecord {
  experiment_id: string;
  arm_id: string;
  strategy: string;
  idx: number;
  batch: number;
  stress_point: Record<string, number>;
  config_hash: string;
  trajectory_hash: string;
  metrics: RunMetrics;
  outcome: Outcome;
  margin_m: number;
  elapsed_ms: number;
}

export interface ModelCall {
  id: string;
  task: string;
  model_requested: string | null;
  model_served: string | null;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  status: "ok" | "invalid_output" | "error";
  error_kind: string | null;
  error: string | null;
  raw_output: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
}

export interface ProposedParameter {
  name: string;
  value: number | null;
  unit: string;
  provenance: Provenance;
  evidence: string;
  rationale: string;
  status: "accepted" | "downgraded" | "rejected";
  validation_note: string;
}

export interface CandidateCheck {
  index: number;
  status: "valid" | "rejected";
  reasons: string[];
  intervention: Intervention | null;
  raw: Record<string, unknown>;
}
