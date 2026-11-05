export interface MatterixArtifact {
  name: string;
  key: string;
  kind: string;
  task_name?: string;
  [key: string]: unknown;
}

export interface MatterixSample extends Record<string, unknown> {
  task_name?: string;
  protocol_time_s?: number;
  sim_time_s?: number;
  session_id?: string | null;
  scene_id?: string | null;
  request_id?: string;
}

export interface MatterixProtocolSample extends MatterixSample {
  task_name: string;
  protocol_time_s: number;
  sim_time_s: number;
  session_id: string | null;
  scene_id: string | null;
}

export interface MatterixProtocolArtifact extends MatterixArtifact {
  task_name: string;
}

export interface MatterixSnapshotValues {
  submitted: Record<string, unknown>;
  effective: Record<string, unknown>;
}

export type MatterixExecutionSnapshot = MatterixSnapshotValues;

export interface MatterixProtocolSnapshot extends MatterixSnapshotValues {
  submitted: {
    parameters: Record<string, unknown>;
    meta: Record<string, unknown> | null;
  };
  effective: {
    parameters: Record<string, unknown>;
    meta: Record<string, unknown> | null;
  };
}

export interface MatterixAcceptanceCheck extends Record<string, unknown> {
  name?: string;
  actual?: unknown;
  expected?: unknown;
  passed?: boolean | null;
  unit?: string;
}

export interface MatterixAcceptanceStage extends Record<string, unknown> {
  passed?: boolean | null;
  status?: string | null;
  task_type?: string | null;
  acceptance?: MatterixAcceptance | null;
  checks?: MatterixAcceptanceCheck[];
  acceptance_checks?: MatterixAcceptanceCheck[];
}

export interface MatterixAcceptance extends Record<string, unknown> {
  passed?: boolean | null;
  checks?: MatterixAcceptanceCheck[];
  acceptance_checks?: MatterixAcceptanceCheck[];
  stages?: Record<string, MatterixAcceptanceStage>;
}

export interface MatterixProtocolAcceptance extends MatterixAcceptance {
  passed: boolean | null;
  stages: Record<string, MatterixAcceptanceStage>;
}

/**
 * Canonical protocol wire task shape.
 *
 * result.tasks uses name/type/status. Protocol samples and files retain
 * task_name, and acceptance stages retain task_type.
 */
export interface MatterixProtocolTaskResult extends Record<string, unknown> {
  name: string;
  type: string;
  status: string;
  start_time?: string | null;
  end_time?: string | null;
}

export interface MatterixProtocolResult extends Record<string, unknown> {
  protocol_run: Record<string, unknown>;
  tasks: MatterixProtocolTaskResult[];
}

export interface MatterixRecording {
  enabled?: boolean;
  status: 'disabled' | 'pending' | 'recording' | 'ready' | 'partial' | 'failed' | 'unavailable';
  complete: boolean;
  error?: string | null;
  segments?: Array<{
    task_name: string;
    status: string;
    key?: string;
    start_protocol_time_s?: number;
    end_protocol_time_s?: number;
    error?: string | null;
  }>;
  [key: string]: unknown;
}

export interface MatterixExecutionDetail {
  schema_version: 1;
  protocol_run_name: string;
  task_name: string;
  scope: 'task' | 'protocol';
  status: string;
  source: 'simulation' | 'mock' | null;
  snapshot: MatterixExecutionSnapshot;
  samples: MatterixSample[];
  latest_sample: MatterixSample | null;
  acceptance: MatterixAcceptance | null;
  videos: MatterixArtifact[];
  files: MatterixArtifact[];
  result: Record<string, unknown> | null;
  recording?: MatterixRecording | null;
  warnings: string[];
}

/**
 * Contract for GET /api/matterix/{protocol_run_name}.
 *
 * Recording status may be returned as `recording` or `result.recording`.
 * The protocol timeline uses `protocol_time_s`; every sample also retains its
 * task-local `sim_time_s`.
 */
export interface MatterixProtocolExecutionDetail extends MatterixExecutionDetail {
  task_name: '__protocol__';
  scope: 'protocol';
  snapshot: MatterixProtocolSnapshot;
  samples: MatterixProtocolSample[];
  latest_sample: MatterixProtocolSample | null;
  acceptance: MatterixProtocolAcceptance | null;
  files: MatterixProtocolArtifact[];
  result: MatterixProtocolResult;
}
