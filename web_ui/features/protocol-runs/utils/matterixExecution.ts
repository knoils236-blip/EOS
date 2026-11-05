import type { LabSpec, ProtocolSpec } from '@/lib/api/specs';
import type { Task } from '@/lib/types/api';
import { matterixAssetLabel, matterixFieldLabel, matterixTaskLabel } from '@/lib/labels';
import type {
  MatterixExecutionDetail,
  MatterixProtocolExecutionDetail,
  MatterixProtocolSample,
  MatterixProtocolTaskResult,
  MatterixRecording,
  MatterixSample,
  MatterixSnapshotValues,
} from '../types/matterixExecution';

type JsonRecord = Record<string, unknown>;

export interface MatterixSeries {
  key: string;
  label: string;
  points: Array<{ x: number; y: number }>;
}

export interface MatterixChartGroup {
  key: string;
  title: string;
  unit: string;
  series: MatterixSeries[];
}

export interface KeyValueRow {
  key: string;
  value: unknown;
}

export interface MatterixSummaryRow {
  key: string;
  label: string;
  value: string;
}

export interface MatterixSummarySection {
  key: string;
  title: string;
  rows: MatterixSummaryRow[];
}

export interface MatterixResultSummary {
  sections: MatterixSummarySection[];
  syntheticModel: boolean;
}

export interface MatterixSnapshotGroup extends MatterixSnapshotValues {
  taskName: string;
}

export interface MatterixProtocolTaskRow {
  taskName: string;
  taskType: string;
  label: string;
  status: string;
  acceptance: JsonRecord | null;
  passed: boolean | null;
}

const TERMINAL_STATUSES = new Set([
  'CANCELLED',
  'COMPLETED',
  'FAILED',
  'INTERRUPTED',
  'REJECTED',
  'SUCCEEDED',
  'TIMED_OUT',
]);

const RECORDING_STATUSES = new Set<MatterixRecording['status']>([
  'disabled',
  'pending',
  'recording',
  'ready',
  'partial',
  'failed',
  'unavailable',
]);

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isMatterixRecording(value: unknown): value is MatterixRecording {
  return (
    isRecord(value) &&
    typeof value.complete === 'boolean' &&
    typeof value.status === 'string' &&
    RECORDING_STATUSES.has(value.status as MatterixRecording['status'])
  );
}

function stageAcceptance(value: unknown): JsonRecord | null {
  if (!isRecord(value)) return null;
  return isRecord(value.acceptance) ? value.acceptance : value;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function nestedRecord(value: unknown, ...path: string[]): JsonRecord | null {
  let current = value;
  for (const part of path) {
    if (!isRecord(current)) return null;
    current = current[part];
  }
  return isRecord(current) ? current : null;
}

function isProtocolSample(value: unknown): value is MatterixProtocolSample {
  return (
    isRecord(value) &&
    typeof value.task_name === 'string' &&
    (typeof value.session_id === 'string' || value.session_id === null) &&
    (typeof value.scene_id === 'string' || value.scene_id === null) &&
    finiteNumber(value.protocol_time_s) !== null &&
    finiteNumber(value.sim_time_s) !== null
  );
}

function isProtocolTaskResult(value: unknown): value is MatterixProtocolTaskResult {
  return (
    isRecord(value) &&
    typeof value.name === 'string' &&
    typeof value.type === 'string' &&
    typeof value.status === 'string'
  );
}

export function parseMatterixExecutionDetail(value: unknown): MatterixExecutionDetail {
  if (
    !isRecord(value) ||
    value.schema_version !== 1 ||
    typeof value.protocol_run_name !== 'string' ||
    typeof value.task_name !== 'string' ||
    typeof value.status !== 'string' ||
    (value.scope !== undefined && value.scope !== 'task' && value.scope !== 'protocol') ||
    !['simulation', 'mock', null].includes(value.source as string | null) ||
    !isRecord(value.snapshot) ||
    !isRecord(value.snapshot.submitted) ||
    !isRecord(value.snapshot.effective) ||
    !Array.isArray(value.samples) ||
    (value.latest_sample !== null && !isRecord(value.latest_sample)) ||
    (value.acceptance !== null && !isRecord(value.acceptance)) ||
    (value.result !== null && !isRecord(value.result)) ||
    (value.recording !== undefined && value.recording !== null && !isMatterixRecording(value.recording)) ||
    !Array.isArray(value.videos) ||
    !Array.isArray(value.files) ||
    !Array.isArray(value.warnings)
  ) {
    throw new Error('Matterix 执行详情格式无效');
  }
  const scope = value.scope === 'protocol' || value.task_name === '__protocol__' ? 'protocol' : 'task';
  if (scope === 'protocol' && value.task_name !== '__protocol__') {
    throw new Error('Matterix 协议执行详情格式无效');
  }
  return { ...value, scope } as unknown as MatterixExecutionDetail;
}

export function parseMatterixProtocolExecutionDetail(value: unknown): MatterixProtocolExecutionDetail {
  const detail = parseMatterixExecutionDetail(value);
  if (
    detail.scope !== 'protocol' ||
    detail.task_name !== '__protocol__' ||
    !isRecord(detail.snapshot.submitted.parameters) ||
    !('meta' in detail.snapshot.submitted) ||
    (detail.snapshot.submitted.meta !== null && !isRecord(detail.snapshot.submitted.meta)) ||
    !isRecord(detail.snapshot.effective.parameters) ||
    !('meta' in detail.snapshot.effective) ||
    (detail.snapshot.effective.meta !== null && !isRecord(detail.snapshot.effective.meta)) ||
    !detail.samples.every(isProtocolSample) ||
    (detail.latest_sample !== null && !isProtocolSample(detail.latest_sample)) ||
    !detail.files.every((file) => typeof file.task_name === 'string') ||
    detail.videos.length > 1 ||
    detail.videos.some((video) => video.name !== 'protocol-simulation.mp4') ||
    !isRecord(detail.result) ||
    !isRecord(detail.result.protocol_run) ||
    !Array.isArray(detail.result.tasks) ||
    !detail.result.tasks.every(isProtocolTaskResult)
  ) {
    throw new Error('Matterix 协议执行详情格式无效');
  }
  return detail as MatterixProtocolExecutionDetail;
}

export function mergeMatterixSamples<T extends MatterixSample>(current: T[], incoming: T[], limit = 5000): T[] {
  const merged: T[] = [];
  const positions = new Map<string, number>();

  for (const sample of [...current, ...incoming]) {
    const requestId = sample.request_id;
    const simTime = finiteNumber(sample.sim_time_s);
    const protocolTime = finiteNumber(sample.protocol_time_s);
    const identityParts = [sample.task_name, sample.session_id, requestId, sample.scene_id, protocolTime ?? simTime];
    const identity = identityParts.some((part) => part !== null && part !== undefined)
      ? identityParts.map((part) => String(part ?? '')).join('\u0000')
      : null;

    if (identity !== null && positions.has(identity)) {
      merged[positions.get(identity)!] = sample;
    } else {
      if (identity !== null) positions.set(identity, merged.length);
      merged.push(sample);
    }
  }

  const sorted = merged.sort(
    (left, right) =>
      (finiteNumber(left.protocol_time_s) ?? finiteNumber(left.sim_time_s) ?? 0) -
      (finiteNumber(right.protocol_time_s) ?? finiteNumber(right.sim_time_s) ?? 0)
  );
  if (sorted.length <= limit) return sorted;
  if (limit > 1 && sorted.some((sample) => finiteNumber(sample.protocol_time_s) !== null)) {
    return Array.from({ length: limit }, (_, index) => sorted[Math.round((index * (sorted.length - 1)) / (limit - 1))]);
  }
  return sorted.slice(-limit);
}

export function isActiveMatterixStatus(status: string): boolean {
  return ['CREATED', 'QUEUED', 'RUNNING', 'STARTING'].includes(status.toUpperCase());
}

export function isTerminalMatterixStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status.toUpperCase());
}

export function isMatterixRichTask(task: Task): boolean {
  if (isMatterixProtocolTaskType(task.type)) return true;
  return Object.values(task.devices ?? {}).some((device) => {
    const deviceType = String(device.type ?? '').toLowerCase();
    return deviceType.includes('matterix') || deviceType.includes('mixture');
  });
}

export function isMatterixProtocolTaskType(type: string): boolean {
  const normalized = type.trim().toLowerCase();
  if (!normalized || normalized.includes('mock')) return false;
  return normalized.includes('matterix') || normalized.includes('mixture_matterix');
}

export function protocolRunUsesMatterix({
  protocolRunType,
  protocolSpec,
  taskStatuses,
}: {
  protocolRunType: string;
  protocolSpec: ProtocolSpec | null | undefined;
  taskStatuses: Array<{ type: string }>;
}): boolean {
  return (
    isMatterixProtocolTaskType(protocolRunType) ||
    Boolean(protocolSpec && isMatterixProtocolTaskType(protocolSpec.type)) ||
    Boolean(protocolSpec?.labs.some(isMatterixProtocolTaskType)) ||
    taskStatuses.some((task) => isMatterixProtocolTaskType(task.type))
  );
}

export function protocolHasRealMatterixDevice(
  protocolSpec: ProtocolSpec | null,
  labSpecs: Record<string, LabSpec>
): boolean {
  if (!protocolSpec) return false;
  for (const labName of protocolSpec.labs) {
    const devices = labSpecs[labName]?.devices ?? {};
    for (const device of Object.values(devices)) {
      const deviceType = device.type.trim().toLowerCase();
      const backend = isRecord(device.init_parameters) ? device.init_parameters.backend : undefined;
      if (backend === 'mock') continue;
      if (deviceType === 'matterix_workstation') return true;
      if (deviceType === 'mixture_workstation' && backend === 'matterix') return true;
    }
  }
  return false;
}

export function matterixRecording(detail: MatterixExecutionDetail): MatterixRecording | null {
  if (detail.recording) return detail.recording;
  return isMatterixRecording(detail.result?.recording) ? detail.result.recording : null;
}

export function shouldPollMatterixExecution(detail: MatterixExecutionDetail): boolean {
  const status = matterixRecording(detail)?.status;
  return isActiveMatterixStatus(detail.status) || status === 'pending' || status === 'recording';
}

export function matterixRecordingStatusLabel(recording: MatterixRecording | null): string {
  if (!recording) return '未记录录制状态';
  return {
    disabled: '未启用录制',
    pending: '录制待开始',
    recording: '正在录制',
    ready: '完整协议视频已生成',
    partial: '仅生成部分任务视频',
    failed: '录制失败',
    unavailable: '录制不可用',
  }[recording.status];
}

export function matterixExecutionPassed(detail: MatterixExecutionDetail): boolean {
  if (detail.scope === 'protocol') {
    const tasks = matterixProtocolTaskResults(detail);
    if (detail.status.toUpperCase() !== 'COMPLETED' || detail.acceptance?.passed !== true || !tasks.length) {
      return false;
    }
    const stages = detail.acceptance.stages ?? {};
    return tasks.every(
      (task) => task.status.toUpperCase() === 'COMPLETED' && stageAcceptance(stages[task.name])?.passed === true
    );
  }
  return detail.acceptance?.passed === true;
}

export function matterixVideoNotice(detail: MatterixExecutionDetail): string | null {
  if (detail.videos.length > 0) return null;
  if (detail.source === 'mock') return 'Mock 不支持视频录制。';
  if (detail.scope !== 'protocol') {
    return '本次任务执行没有可用视频；归档完成后会出现在这里。';
  }
  if (isActiveMatterixStatus(detail.status)) return '协议结束后生成完整视频。';
  if (isTerminalMatterixStatus(detail.status) && !['COMPLETED', 'SUCCEEDED'].includes(detail.status.toUpperCase())) {
    return '协议未完整结束，未生成完整视频；请从下载页查看实际已执行任务的产物。';
  }
  return '完整协议视频尚未归档；请从下载页查看已有任务产物。';
}

export function matterixSnapshotGroups(detail: MatterixExecutionDetail): MatterixSnapshotGroup[] {
  if (detail.scope === 'protocol') {
    const submitted = nestedRecord(detail.snapshot, 'submitted', 'parameters') ?? {};
    const effective = nestedRecord(detail.snapshot, 'effective', 'parameters') ?? {};
    const names = [
      ...matterixProtocolTaskResults(detail).map((task) => task.name),
      ...new Set([...Object.keys(submitted), ...Object.keys(effective)]),
    ];
    return [...new Set(names)].map((taskName) => ({
      taskName,
      submitted: isRecord(submitted[taskName]) ? submitted[taskName] : {},
      effective: isRecord(effective[taskName]) ? effective[taskName] : {},
    }));
  }
  return [
    {
      taskName: detail.task_name,
      submitted: detail.snapshot.submitted,
      effective: detail.snapshot.effective,
    },
  ];
}

export function matterixSnapshotMetadata(detail: MatterixExecutionDetail): MatterixSnapshotValues | null {
  if (detail.scope !== 'protocol') return null;
  const submitted = nestedRecord(detail.snapshot, 'submitted', 'meta');
  const effective = nestedRecord(detail.snapshot, 'effective', 'meta');
  return submitted && effective ? { submitted, effective } : null;
}

function nestedString(value: unknown, field: string): string | null {
  if (!isRecord(value)) return null;
  if (typeof value[field] === 'string') return value[field] as string;
  for (const item of Object.values(value)) {
    const found = isRecord(item) ? nestedString(item, field) : null;
    if (found) return found;
  }
  return null;
}

export function matterixProtocolTaskResults(detail: MatterixExecutionDetail): MatterixProtocolTaskResult[] {
  if (detail.scope !== 'protocol' || !isRecord(detail.result) || !Array.isArray(detail.result.tasks)) {
    return [];
  }
  return detail.result.tasks.filter(isProtocolTaskResult);
}

export function matterixProtocolTaskRows(detail: MatterixExecutionDetail): MatterixProtocolTaskRow[] {
  if (detail.scope !== 'protocol') return [];
  const stages = isRecord(detail.acceptance?.stages) ? detail.acceptance.stages : {};
  const tasks = matterixProtocolTaskResults(detail);
  const names = [
    ...tasks.map((task) => task.name),
    ...Object.keys(stages).filter((name) => !tasks.some((task) => task.name === name)),
  ];

  return names.map((taskName) => {
    const task = tasks.find((item) => item.name === taskName);
    const stage = stages[taskName] ?? null;
    const accepted = stageAcceptance(stage);
    const taskType = stage?.task_type ?? task?.type ?? '';
    const submittedParameters = nestedRecord(detail.snapshot, 'submitted', 'parameters');
    const submitted = isRecord(submittedParameters?.[taskName]) ? submittedParameters[taskName] : undefined;
    const capabilityId = nestedString(submitted, 'capability_id');
    return {
      taskName,
      taskType,
      label: taskType
        ? matterixTaskLabel(taskType, {
            ...(isRecord(submitted) ? submitted : {}),
            ...(capabilityId ? { capability_id: capabilityId } : {}),
          })
        : taskName,
      status: stage?.status ?? task?.status ?? 'UNKNOWN',
      acceptance: accepted,
      passed: typeof accepted?.passed === 'boolean' ? accepted.passed : null,
    };
  });
}

export function existingMatterixBoolean(
  meta: Record<string, unknown> | null | undefined,
  field: 'record_video' | 'hide_shell'
): boolean | null {
  const value = nestedRecord(meta, 'matterix')?.[field];
  return typeof value === 'boolean' ? value : null;
}

export function mergeMatterixRecordingMeta(
  meta: Record<string, unknown> | null,
  recordVideo: boolean | null,
  hideShell: boolean | null
): Record<string, unknown> | null {
  if (recordVideo === null && hideShell === null) return meta;
  const existingMatterix = isRecord(meta?.matterix) ? meta.matterix : {};
  const matterix = { ...existingMatterix };
  if (recordVideo !== null) matterix.record_video = recordVideo;
  if (hideShell !== null) matterix.hide_shell = hideShell;
  return { ...(meta ?? {}), matterix };
}

export function flattenKeyValues(value: unknown, prefix = ''): KeyValueRow[] {
  if (!isRecord(value)) return [];
  const rows: KeyValueRow[] = [];
  for (const [key, item] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (isRecord(item) && Object.keys(item).length > 0) {
      rows.push(...flattenKeyValues(item, path));
    } else {
      rows.push({ key: path, value: item });
    }
  }
  return rows;
}

export function matterixAcceptanceChecks(value: unknown, stage = ''): JsonRecord[] {
  if (!isRecord(value)) return [];
  const acceptance = stageAcceptance(value);
  if (!acceptance) return [];
  const rawChecks = Array.isArray(acceptance.checks)
    ? acceptance.checks
    : Array.isArray(acceptance.acceptance_checks)
      ? acceptance.acceptance_checks
      : [];
  const checks: JsonRecord[] = rawChecks
    .filter(isRecord)
    .map((check) => ({ ...check, name: `${stage}${check.name ?? '检查'}` }));
  if (isRecord(acceptance.stages)) {
    for (const [name, item] of Object.entries(acceptance.stages)) {
      checks.push(...matterixAcceptanceChecks(item, `${stage}${name} · `));
    }
  }
  return checks;
}

export function matterixAcceptanceRows(value: unknown, stage = ''): KeyValueRow[] {
  if (!isRecord(value)) return [];
  const rows: KeyValueRow[] = [];
  for (const [key, item] of Object.entries(value)) {
    if (key === 'checks' || key === 'acceptance_checks') continue;
    if (key === 'acceptance' && isRecord(item)) {
      const nestedStage = stage ? `${stage}.acceptance` : 'acceptance';
      rows.push(...matterixAcceptanceRows(item, nestedStage));
      continue;
    }
    if (key === 'stages' && isRecord(item)) {
      for (const [name, acceptance] of Object.entries(item)) {
        rows.push(...matterixAcceptanceRows(acceptance, stage ? `${stage}.${name}` : name));
      }
      continue;
    }
    const rowKey = stage ? `${stage}.${key}` : key;
    if (item === null || ['string', 'number', 'boolean'].includes(typeof item)) {
      rows.push({ key: rowKey, value: item });
    } else if (
      key === 'reasons' &&
      Array.isArray(item) &&
      item.length <= 20 &&
      item.every((entry) => ['string', 'number', 'boolean'].includes(typeof entry))
    ) {
      rows.push({ key: rowKey, value: item });
    }
  }
  return rows;
}

export function formatMatterixScientific(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude < 0.001 || magnitude >= 1_000_000)) {
    return value.toExponential(5).replace(/\.?0+e/, 'e');
  }
  return Number(value.toPrecision(8)).toString();
}

export function formatMatterixCompactValue(value: unknown, unit = ''): string {
  const suffix = unit ? ` ${unit}` : '';
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return `${formatMatterixScientific(value)}${suffix}`;
  if (typeof value === 'string') {
    const normalized = value.toLowerCase();
    if (normalized === 'completed' || normalized === 'succeeded') return '已完成';
    if (normalized === 'running') return '运行中';
    if (normalized === 'failed') return '失败';
    if (normalized === 'cancelled') return '已取消';
    if (normalized === 'timed_out') return '已超时';
    if (normalized === 'interrupted') return '已中断';
    if (normalized === 'verified') return '已验证';
    if (normalized === 'mock') return 'Mock';
    if (normalized === 'simulation') return '仿真';
    if (normalized === 'none') return '无';
    if (normalized === 'synthetic_model') return '合成模型（synthetic_model）';
    if (normalized === 'mock_material_balance') return 'Mock 物料平衡（mock_material_balance）';
    if (normalized === 'isaac_semantic_ledger') return 'Isaac 语义账本（isaac_semantic_ledger）';
    if (normalized === 'measurement_invalid') return '测量无效（measurement_invalid）';
    return value.length > 300 ? `${value.slice(0, 300)}…` : value;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return '无';
    if (value.length <= 10 && value.every((item) => ['string', 'number', 'boolean'].includes(typeof item))) {
      return value.map((item) => formatMatterixCompactValue(item)).join(' · ') + suffix;
    }
    return `数组（${value.length} 项，完整内容见原始数据）`;
  }
  if (isRecord(value)) return `对象（${Object.keys(value).length} 项，完整内容见原始数据）`;
  return String(value);
}

function summarySection(
  key: string,
  title: string,
  record: JsonRecord | null,
  fields: Array<{ key: string; unit?: string }>
): MatterixSummarySection | null {
  if (!record) return null;
  const rows = fields.flatMap(({ key: field, unit = '' }) => {
    if (!(field in record)) return [];
    return [
      {
        key: field,
        label: matterixFieldLabel(field),
        value: formatMatterixCompactValue(record[field], unit),
      },
    ];
  });
  return rows.length > 0 ? { key, title, rows } : null;
}

export function matterixResultSummary(result: Record<string, unknown> | null): MatterixResultSummary {
  if (!result) return { sections: [], syntheticModel: false };
  const top = summarySection('summary', '执行摘要', result, [
    { key: 'status' },
    { key: 'source' },
    { key: 'scene' },
    { key: 'scene_id' },
    { key: 'capability' },
    { key: 'capability_id' },
    { key: 'elapsed_sim_s', unit: 's' },
    { key: 'elapsed_wall_s', unit: 's' },
    { key: 'sim_time_s', unit: 's' },
    { key: 'wall_time_s', unit: 's' },
    { key: 'duration_s', unit: 's' },
    { key: 'steps' },
    { key: 'step_count' },
    { key: 'sample_count' },
    { key: 'config_version' },
    { key: 'passed' },
    { key: 'request_id' },
    { key: 'error' },
  ]);
  const sample = nestedRecord(result, 'sample') ?? (result.sample_id && result.stock_ul ? result : null);
  const observation =
    nestedRecord(result, 'observation') ??
    (result.source === 'synthetic_model' && (result.value_mm !== undefined || result.replicates !== undefined)
      ? result
      : null);
  const quality = nestedRecord(result, 'quality') ?? (result.eligible !== undefined ? result : null);
  const score =
    result.target_mm !== undefined || result.measured_mm !== undefined || result.loss !== undefined ? result : null;
  const sections = [
    top,
    summarySection('sample', '制备样品', sample, [
      { key: 'sample_id' },
      { key: 'backend' },
      { key: 'requested_stock_ul', unit: 'µL' },
      { key: 'stock_ul', unit: 'µL' },
      { key: 'diluent_ul', unit: 'µL' },
      { key: 'stock_concentration_mm', unit: 'mM' },
      { key: 'composition_source' },
      { key: 'hardware_calibrated' },
    ]),
    summarySection('observation', '合成观测', observation, [
      { key: 'source' },
      { key: 'backend' },
      { key: 'replicates', unit: 'mM' },
      { key: 'value_mm', unit: 'mM' },
      { key: 'uncertainty_mm', unit: 'mM' },
      { key: 'blank_mm', unit: 'mM' },
      { key: 'reference_mm', unit: 'mM' },
      { key: 'seed' },
      { key: 'model_version' },
    ]),
    summarySection('quality', '测量质量', quality, [
      { key: 'eligible' },
      { key: 'reasons' },
      { key: 'failure_kind' },
      { key: 'policy_version' },
    ]),
    summarySection('score', '目标评分', score, [
      { key: 'target_mm', unit: 'mM' },
      { key: 'measured_mm', unit: 'mM' },
      { key: 'uncertainty_mm', unit: 'mM' },
      { key: 'loss', unit: 'mM²' },
      { key: 'source' },
      { key: 'observation_id' },
    ]),
  ].filter((section): section is MatterixSummarySection => section !== null);
  const syntheticModel =
    result.source === 'synthetic_model' || nestedRecord(result, 'observation')?.source === 'synthetic_model';
  return { sections, syntheticModel };
}

export function formatMatterixValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(6)));
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

export function boundedJson(value: unknown, maxChars = 65536): string {
  const text = JSON.stringify(value, null, 2);
  if (text === undefined) return '';
  return text.length > maxChars ? `${text.slice(0, maxChars)}\n…（预览已截断）` : text;
}

export async function readBoundedResponseText(
  response: Response,
  maxBytes = 65536
): Promise<{ text: string; truncated: boolean }> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('响应正文为空');

  const decoder = new TextDecoder();
  let text = '';
  let bytesRead = 0;
  let truncated = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const remaining = maxBytes - bytesRead;
    if (value.byteLength >= remaining) {
      text += decoder.decode(value.subarray(0, remaining), { stream: true });
      bytesRead = maxBytes;
      truncated = true;
      await reader.cancel();
      break;
    }
    text += decoder.decode(value, { stream: true });
    bytesRead += value.byteLength;
  }
  text += decoder.decode();

  const contentRange = response.headers.get('content-range');
  const rangeMatch = contentRange?.match(/^bytes\s+\d+-\d+\/(\d+)$/i);
  if (rangeMatch && Number(rangeMatch[1]) > maxBytes) truncated = true;
  return { text, truncated };
}

export function artifactDownloadUrl(key: string, inline = false): string {
  const path = key.split('/').map(encodeURIComponent).join('/');
  return `/api/files/download/${path}${inline ? '?inline=1' : ''}`;
}

export function matterixChartTimeLabel(detail: MatterixExecutionDetail): string {
  return detail.scope === 'protocol' ? '协议时间（s）' : '仿真时间（s）';
}

function sampleTime(sample: MatterixSample, scope: MatterixExecutionDetail['scope']): number | null {
  if (scope === 'protocol') return finiteNumber(sample.protocol_time_s);
  return finiteNumber(sample.sim_time_s);
}

const LIQUID_NOISE_ML = 1e-6;
const JOINT_LINEAR_NOISE_MM = 1e-3;
const JOINT_ANGULAR_NOISE_DEG = 1e-3;

function hasMeaningfulChange(points: Array<{ x: number; y: number }>, minSpread: number): boolean {
  if (points.length < 2) return false;
  let min = points[0].y;
  let max = points[0].y;
  for (const point of points.slice(1)) {
    min = Math.min(min, point.y);
    max = Math.max(max, point.y);
  }
  return max - min > minSpread;
}

function seriesContext(sample: MatterixSample): string {
  return [sample.scene_id, sample.session_id ?? sample.task_name].map((value) => String(value ?? '')).join('\u0000');
}

function seriesIdentity(sample: MatterixSample, entity: string): string {
  return `${seriesContext(sample)}\u0000${entity}`;
}

function shortSeriesId(value: string): string {
  return value.length <= 10 ? value : `${value.slice(0, 3)}…${value.slice(-4)}`;
}

function seriesLabel(sample: MatterixSample, entity: string, includeContext: boolean): string {
  if (!includeContext) return entity;
  const context = [
    sample.scene_id ? `场景 ${shortSeriesId(String(sample.scene_id))}` : '',
    sample.session_id
      ? `会话 ${shortSeriesId(String(sample.session_id))}`
      : sample.task_name
        ? `任务 ${shortSeriesId(String(sample.task_name))}`
        : '',
  ]
    .filter(Boolean)
    .join(' · ');
  const parts = [entity, context].filter(Boolean);
  return parts.join(' · ');
}

interface NumberSeries extends MatterixSeries {
  values: number[];
}

function volumeGroups(
  samples: MatterixSample[],
  scope: MatterixExecutionDetail['scope'],
  includeContext: boolean
): MatterixChartGroup[] {
  const discovered = new Map<string, NumberSeries>();
  for (const sample of samples) {
    const x = sampleTime(sample, scope);
    if (x === null) continue;
    const values =
      nestedRecord(sample, 'semantics', 'liquid_volumes_ml') ?? nestedRecord(sample, 'station', 'volume_ml') ?? {};
    for (const [entity, rawValue] of Object.entries(values)) {
      const value = finiteNumber(rawValue);
      if (value === null) continue;
      const key = seriesIdentity(sample, entity);
      const series = discovered.get(key) ?? {
        key,
        label: seriesLabel(sample, matterixAssetLabel(entity), includeContext),
        points: [],
        values: [],
      };
      series.points.push({ x, y: value });
      series.values.push(value);
      discovered.set(key, series);
    }
  }

  const series = [...discovered.values()]
    .filter((item) => hasMeaningfulChange(item.points, LIQUID_NOISE_ML))
    .map((item) => ({
      ...item,
      spread: Math.max(...item.values) - Math.min(...item.values),
      magnitude: item.values.reduce((total, value) => total + Math.abs(value), 0),
    }))
    .sort((left, right) => right.spread - left.spread || right.magnitude - left.magnitude);
  if (series.length === 0) return [];

  return [
    {
      key: 'liquid-absolute',
      title: '容器绝对液量',
      unit: 'mL',
      series: series.map(({ key, label, points }) => ({ key, label, points })),
    },
    {
      key: 'liquid-delta',
      title: '相对各序列首个实际样本的液量变化',
      unit: 'µL',
      series: series.map(({ key, label, points }) => {
        const baseline = points[0]?.y;
        return {
          key,
          label,
          points: points.map((point) => ({ x: point.x, y: (point.y - baseline) * 1000 })),
        };
      }),
    },
  ];
}

function normalizeUnit(unit: unknown): string | null {
  if (typeof unit !== 'string') return null;
  return unit.trim().toLowerCase();
}

function convertJointValue(value: number, unit: string): { value: number; unit: string } | null {
  switch (unit) {
    case 'm':
      return { value: value * 1000, unit: 'mm' };
    case 'mm':
      return { value, unit: 'mm' };
    case 'cm':
      return { value: value * 10, unit: 'mm' };
    case 'rad':
      return { value: (value * 180) / Math.PI, unit: '°' };
    case 'deg':
    case 'degree':
    case 'degrees':
    case '°':
      return { value, unit: '°' };
    default:
      return null;
  }
}

interface UnitValue {
  name: string;
  value: number;
  unit: string;
}

function explicitJointValues(sample: JsonRecord): UnitValue[] {
  const raw = nestedRecord(sample, 'semantics', 'jointsSI') ?? nestedRecord(sample, 'jointsSI');
  if (!raw) return [];

  const names = Array.isArray(raw.names) ? raw.names : null;
  const values = Array.isArray(raw.values) ? raw.values : null;
  const units = Array.isArray(raw.units) ? raw.units : null;
  if (names && values) {
    return names.flatMap((name, index) => {
      const value = finiteNumber(values[index]);
      const unit = normalizeUnit(units?.[index]);
      return value === null || unit === null ? [] : [{ name: String(name), value, unit }];
    });
  }

  const entries = isRecord(raw.values) ? raw.values : isRecord(raw.positions) ? raw.positions : raw;
  const unitMap = isRecord(raw.units)
    ? raw.units
    : (nestedRecord(sample, 'semantics', 'units', 'joints') ?? nestedRecord(sample, 'units', 'joints') ?? {});
  const result: UnitValue[] = [];
  for (const [name, item] of Object.entries(entries)) {
    if (name === 'names' || name === 'values' || name === 'positions' || name === 'units') continue;
    if (isRecord(item)) {
      const value = finiteNumber(item.value);
      const unit = normalizeUnit(item.unit ?? unitMap[name]);
      if (value !== null && unit !== null) result.push({ name, value, unit });
    } else if (Array.isArray(item)) {
      const value = finiteNumber(item[0]);
      const unit = normalizeUnit(item[1] ?? unitMap[name]);
      if (value !== null && unit !== null) result.push({ name, value, unit });
    } else {
      const value = finiteNumber(item);
      const unit = normalizeUnit(unitMap[name]);
      if (value !== null && unit !== null) result.push({ name, value, unit });
    }
  }
  return result;
}

function jointGroups(
  samples: MatterixSample[],
  includeFranka: boolean,
  scope: MatterixExecutionDetail['scope'],
  includeContext: boolean
): MatterixChartGroup[] {
  const grouped = new Map<string, Map<string, MatterixSeries>>();
  const fingerJoints = new Set(['panda_finger_joint1', 'panda_finger_joint2']);
  const armJoints = new Set([
    'panda_joint1',
    'panda_joint2',
    'panda_joint3',
    'panda_joint4',
    'panda_joint5',
    'panda_joint6',
    'panda_joint7',
  ]);

  for (const sample of samples) {
    const x = sampleTime(sample, scope);
    if (x === null) continue;
    let joints = explicitJointValues(sample);
    if (joints.length === 0 && includeFranka) {
      const robot = nestedRecord(sample, 'robot');
      const names = Array.isArray(robot?.joint_names) ? robot.joint_names : [];
      const positions = Array.isArray(robot?.joint_position) ? robot.joint_position : [];
      const units = nestedRecord(sample, 'units');
      const armUnit = normalizeUnit(units?.arm_joint_position);
      const gripperUnit = normalizeUnit(units?.gripper_joint_position);
      joints = names.flatMap((name, jointIndex) => {
        const jointName = String(name);
        const value = finiteNumber(positions[jointIndex]);
        const unit = fingerJoints.has(jointName) ? gripperUnit : armJoints.has(jointName) ? armUnit : null;
        return value === null || unit === null ? [] : [{ name: jointName, value, unit }];
      });
    }

    for (const joint of joints) {
      const converted = convertJointValue(joint.value, joint.unit);
      if (!converted) continue;
      if (!grouped.has(converted.unit)) grouped.set(converted.unit, new Map());
      const seriesMap = grouped.get(converted.unit)!;
      const key = seriesIdentity(sample, `${joint.name}\u0000${converted.unit}`);
      const series = seriesMap.get(key) ?? {
        key,
        label: seriesLabel(sample, joint.name, includeContext),
        points: [],
      };
      series.points.push({ x, y: converted.value });
      seriesMap.set(key, series);
    }
  }

  return [...grouped.entries()].map(([unit, seriesMap]) => {
    const minSpread = unit === 'mm' ? JOINT_LINEAR_NOISE_MM : JOINT_ANGULAR_NOISE_DEG;
    return {
      key: unit === 'mm' ? 'joint-linear' : 'joint-angular',
      title: unit === 'mm' ? '机构线性关节' : '机构旋转关节',
      unit,
      series: [...seriesMap.values()].filter((series) => hasMeaningfulChange(series.points, minSpread)),
    };
  });
}

function heightGroup(
  samples: MatterixSample[],
  scope: MatterixExecutionDetail['scope'],
  includeContext: boolean
): MatterixChartGroup | null {
  const grouped = new Map<string, MatterixSeries>();
  for (const sample of samples) {
    const x = sampleTime(sample, scope);
    if (x === null || normalizeUnit(nestedRecord(sample, 'units')?.position) !== 'm') continue;
    const beakerPosition = Array.isArray(nestedRecord(sample, 'beaker')?.position_w)
      ? (nestedRecord(sample, 'beaker')!.position_w as unknown[])
      : [];
    const eePosition = Array.isArray(nestedRecord(sample, 'robot')?.ee_position_w)
      ? (nestedRecord(sample, 'robot')!.ee_position_w as unknown[])
      : [];
    const values = [
      { entity: 'beaker-height', label: '烧杯高度', value: finiteNumber(beakerPosition[2]) },
      { entity: 'ee-height', label: '末端执行器高度', value: finiteNumber(eePosition[2]) },
    ];
    for (const item of values) {
      if (item.value === null) continue;
      const key = seriesIdentity(sample, item.entity);
      const series = grouped.get(key) ?? {
        key,
        label: seriesLabel(sample, item.label, includeContext),
        points: [],
      };
      series.points.push({ x, y: item.value * 1000 });
      grouped.set(key, series);
    }
  }
  const series = [...grouped.values()].filter((item) => hasMeaningfulChange(item.points, JOINT_LINEAR_NOISE_MM));
  return series.length > 0 ? { key: 'height', title: '烧杯与末端执行器高度', unit: 'mm', series } : null;
}

export function extractMatterixChartGroups(detail: MatterixExecutionDetail): MatterixChartGroup[] {
  if (detail.source === 'mock') return [];
  const samples = detail.samples.filter((sample) => isRecord(sample) && sampleTime(sample, detail.scope) !== null);
  const hasFranka = samples.some((sample) => isRecord(sample.beaker) && isRecord(sample.robot));
  const includeContext = new Set(samples.map(seriesContext)).size > 1;
  const groups = volumeGroups(samples, detail.scope, includeContext);
  const heights = heightGroup(samples, detail.scope, includeContext);
  if (heights) groups.push(heights);
  groups.push(...jointGroups(samples, hasFranka, detail.scope, includeContext));
  return groups.filter((group) => group.series.some((series) => series.points.length > 0));
}
