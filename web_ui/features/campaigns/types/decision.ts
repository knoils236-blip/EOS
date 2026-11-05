export interface DecisionRound {
  run_name: string;
  status: string;
  inputs: Record<string, number>;
  objectives: Record<string, number>;
  selection: { method: string; reason: string; model?: string };
  sample_id: string | null;
  backend: string;
  data_source: string;
  model_version: string | null;
  measurement: { value_mm?: number; uncertainty_mm?: number; unit?: string; target_mm?: number };
  quality: 'passed' | 'rejected' | 'unknown';
  quality_reasons: string[];
  eligible: boolean;
  failure_kind: string | null;
  files: { task_name: string; name: string }[];
}

export interface DecisionSnapshot {
  campaign_name: string;
  status: string;
  protocol: string;
  generated_at: string;
  snapshot_id: string;
  total_runs: number;
  window: number;
  max_runs: number;
  stop_reason: string | null;
  objectives: { key: string; direction: string }[];
  best: {
    objective: string;
    value: number;
    run_name: string;
    backend: string;
    data_source: string;
    model_version: string | null;
  }[];
  rounds: DecisionRound[];
  constraints: Record<string, unknown>[];
  ai: { configured: boolean; model: string };
}

export interface DecisionReview {
  text: string;
  model: string;
  snapshot_at: string;
  snapshot_id: string;
  reviewed_runs: string[];
}
