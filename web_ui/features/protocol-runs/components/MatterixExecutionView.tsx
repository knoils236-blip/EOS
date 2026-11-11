'use client';

import * as React from 'react';
import dynamic from 'next/dynamic';
import {
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  type ChartData,
  type ChartOptions,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { AlertTriangle, CheckCircle2, Download, FileJson, LoaderCircle, RefreshCw, Video, XCircle } from 'lucide-react';
import { matterixFieldLabel, statusLabel } from '@/lib/labels';
import type { MatterixArtifact, MatterixExecutionDetail } from '../types/matterixExecution';
import {
  artifactDownloadUrl,
  boundedJson,
  extractMatterixChartGroups,
  flattenKeyValues,
  formatMatterixCompactValue,
  formatMatterixValue,
  matterixAcceptanceChecks,
  matterixAcceptanceRows,
  matterixChartTimeLabel,
  matterixExecutionPassed,
  matterixProtocolTaskRows,
  matterixProtocolTaskResults,
  matterixRecording,
  matterixRecordingStatusLabel,
  matterixResultSummary,
  matterixSnapshotGroups,
  matterixSnapshotMetadata,
  matterixVideoNotice,
  readBoundedResponseText,
  type MatterixChartGroup,
} from '../utils/matterixExecution';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip);

// three.js needs the browser, and its bundle is only worth loading when the tab is opened
const StationTwinView = dynamic(() => import('./StationTwinView'), { ssr: false });

const TABS = ['观测曲线', '数字孪生', '参数快照', '验收详情', '视频', '下载与原始数据'] as const;
type Tab = (typeof TABS)[number];

const SERIES_COLORS = [
  '#2563eb',
  '#dc2626',
  '#16a34a',
  '#9333ea',
  '#ea580c',
  '#0891b2',
  '#ca8a04',
  '#db2777',
  '#4f46e5',
  '#059669',
  '#7c3aed',
  '#475569',
];

interface MatterixExecutionViewProps {
  detail: MatterixExecutionDetail;
  refreshing: boolean;
  error: string | null;
  onRetry: () => void;
  onTaskSelect?: (taskName: string) => void;
}

function formatBytes(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function SummaryTable({ rows }: { rows: Array<{ key: string; label: string; value: string }> }) {
  if (rows.length === 0) return <p className="text-sm text-gray-500 dark:text-gray-400">暂无记录。</p>;
  return (
    <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-slate-700">
      <table className="min-w-full divide-y divide-gray-200 dark:divide-slate-700 text-sm">
        <tbody className="divide-y divide-gray-100 dark:divide-slate-800">
          {rows.map((row) => (
            <tr key={row.key}>
              <th className="w-2/5 bg-gray-50 dark:bg-slate-800/60 px-3 py-2 text-left text-xs font-medium text-gray-700 dark:text-gray-200">
                <span className="block" title={row.key}>
                  {row.label}
                </span>
                {row.label !== row.key && (
                  <span
                    className="mt-0.5 block truncate font-mono text-[10px] font-normal text-gray-400 dark:text-gray-500"
                    title={row.key}
                  >
                    {row.key}
                  </span>
                )}
              </th>
              <td className="px-3 py-2 text-gray-800 dark:text-gray-200 break-words">{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChartGroup({ group, timeLabel }: { group: MatterixChartGroup; timeLabel: string }) {
  const [hiddenSeries, setHiddenSeries] = React.useState<Set<string>>(() => new Set());
  const data: ChartData<'line'> = {
    datasets: group.series.map((series, index) => ({
      label: series.label,
      data: series.points,
      hidden: hiddenSeries.has(series.key),
      parsing: false,
      borderColor: SERIES_COLORS[index % SERIES_COLORS.length],
      backgroundColor: SERIES_COLORS[index % SERIES_COLORS.length],
      pointRadius: 0,
      pointHoverRadius: 4,
      borderWidth: 2,
      tension: 0,
    })),
  };
  const options: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'nearest', axis: 'x', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          title: (items) => `${timeLabel.replace('（s）', '')} ${Number(items[0]?.parsed.x ?? 0).toFixed(3)} s`,
          label: (context) => `${context.dataset.label}: ${Number(context.parsed.y).toFixed(3)} ${group.unit}`,
        },
      },
    },
    scales: {
      x: {
        type: 'linear',
        title: { display: true, text: timeLabel },
        ticks: { maxTicksLimit: 8 },
      },
      y: {
        title: { display: true, text: group.unit },
        ticks: { maxTicksLimit: 7 },
      },
    },
  };

  return (
    <section className="rounded-lg border border-gray-200 dark:border-slate-700 p-3">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h4 className="text-sm font-semibold text-gray-900 dark:text-white">{group.title}</h4>
        <span className="text-xs text-gray-500 dark:text-gray-400">{group.unit}</span>
      </div>
      <div className="mb-2 max-h-28 overflow-y-auto rounded-md border border-gray-200 bg-gray-50 p-2 dark:border-slate-700 dark:bg-slate-900/50">
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {group.series.map((series, index) => {
            const color = SERIES_COLORS[index % SERIES_COLORS.length];
            return (
              <label
                key={series.key}
                className="flex max-w-full items-start gap-1.5 text-xs text-gray-700 dark:text-gray-200"
              >
                <input
                  type="checkbox"
                  checked={!hiddenSeries.has(series.key)}
                  onChange={() =>
                    setHiddenSeries((current) => {
                      const next = new Set(current);
                      if (next.has(series.key)) next.delete(series.key);
                      else next.add(series.key);
                      return next;
                    })
                  }
                  className="mt-0.5 h-3.5 w-3.5 shrink-0"
                />
                <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: color }} />
                <span className="break-words">{series.label}</span>
              </label>
            );
          })}
        </div>
      </div>
      <div className="h-72">
        <Line data={data} options={options} />
      </div>
    </section>
  );
}

function CurvesTab({
  detail,
  onTaskSelect,
}: {
  detail: MatterixExecutionDetail;
  onTaskSelect?: (taskName: string) => void;
}) {
  const groups = React.useMemo(() => extractMatterixChartGroups(detail), [detail]);
  const protocolTasks = React.useMemo(() => matterixProtocolTaskRows(detail), [detail]);
  if (detail.source === 'mock') {
    return (
      <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 text-sm text-amber-800 dark:text-amber-200">
        Mock 仅提供物料平衡和合成测量记录，不包含机械臂、工作站液量或关节遥测曲线。
      </div>
    );
  }
  if (groups.length === 0) {
    return (
      <p className="rounded-lg border border-gray-200 dark:border-slate-700 p-4 text-sm text-gray-500 dark:text-gray-400">
        {detail.samples.length === 0
          ? '后端未返回可绘制的实际观测样本。这里不补造曲线。'
          : '暂无变化关键参数。仅显示同一场景/会话内超出数值噪声的关节、液面/液量及执行高度系列。'}
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {detail.scope === 'protocol' && protocolTasks.length > 0 && (
        <section className="rounded-lg border border-gray-200 p-3 dark:border-slate-700">
          <h4 className="mb-2 text-sm font-semibold text-gray-900 dark:text-white">任务阶段</h4>
          <div className="flex flex-wrap gap-2">
            {protocolTasks.map((task) => (
              <button
                key={task.taskName}
                type="button"
                onClick={() => onTaskSelect?.(task.taskName)}
                disabled={!onTaskSelect}
                className="rounded-md border border-gray-200 bg-gray-50 px-2 py-1 text-left text-xs text-gray-700 enabled:hover:border-blue-300 enabled:hover:text-blue-700 dark:border-slate-700 dark:bg-slate-800 dark:text-gray-200 dark:enabled:hover:border-blue-700 dark:enabled:hover:text-blue-300"
              >
                {task.label}
                <span className="ml-1 text-gray-400">· {statusLabel(task.status)}</span>
              </button>
            ))}
          </div>
        </section>
      )}
      <p className="text-xs text-gray-500 dark:text-gray-400">
        {detail.samples.length.toLocaleString()} 个原始样本；同一场景/会话独立计算变化与液量基线。
      </p>
      {groups.map((group) => (
        <ChartGroup key={group.key} group={group} timeLabel={matterixChartTimeLabel(detail)} />
      ))}
    </div>
  );
}

function ParameterComparison({
  submitted: submittedValues,
  effective: effectiveValues,
}: {
  submitted: Record<string, unknown>;
  effective: Record<string, unknown>;
}) {
  const submitted = flattenKeyValues(submittedValues);
  const effective = flattenKeyValues(effectiveValues);
  const keys = [...new Set([...submitted.map((row) => row.key), ...effective.map((row) => row.key)])];
  const submittedMap = new Map(submitted.map((row) => [row.key, row.value]));
  const effectiveMap = new Map(effective.map((row) => [row.key, row.value]));

  if (keys.length === 0) return <p className="text-sm text-gray-500 dark:text-gray-400">未记录提交参数。</p>;
  return (
    <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-slate-700">
      <table className="min-w-full divide-y divide-gray-200 dark:divide-slate-700 text-sm">
        <thead className="bg-gray-50 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">参数</th>
            <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">提交值</th>
            <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">生效值</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-slate-800">
          {keys.map((key) => (
            <tr key={key}>
              <th className="px-3 py-2 text-left font-mono text-xs font-medium text-gray-600 dark:text-gray-300">
                {key}
              </th>
              <td className="px-3 py-2 text-gray-800 dark:text-gray-200">
                {formatMatterixValue(submittedMap.get(key))}
              </td>
              <td className="px-3 py-2 text-gray-800 dark:text-gray-200">
                {formatMatterixValue(effectiveMap.get(key))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ParametersTab({ detail }: { detail: MatterixExecutionDetail }) {
  const groups = React.useMemo(() => matterixSnapshotGroups(detail), [detail]);
  const metadata = React.useMemo(() => matterixSnapshotMetadata(detail), [detail]);
  const taskNames = new Map(matterixProtocolTaskRows(detail).map((task) => [task.taskName, task.label]));

  if (groups.length === 0 && !metadata) {
    return <p className="text-sm text-gray-500 dark:text-gray-400">未记录提交参数。</p>;
  }
  return (
    <div className="space-y-5">
      {metadata && (
        <section className="space-y-2">
          <h4 className="text-sm font-semibold text-gray-900 dark:text-white">协议元数据</h4>
          <ParameterComparison submitted={metadata.submitted} effective={metadata.effective} />
        </section>
      )}
      {groups.map((group) => (
        <section key={group.taskName} className="space-y-2">
          {detail.scope === 'protocol' && (
            <div>
              <h4 className="text-sm font-semibold text-gray-900 dark:text-white">
                {taskNames.get(group.taskName) ?? group.taskName}
              </h4>
              <p className="font-mono text-[11px] text-gray-500 dark:text-gray-400">{group.taskName}</p>
            </div>
          )}
          <ParameterComparison submitted={group.submitted} effective={group.effective} />
        </section>
      ))}
    </div>
  );
}

function AcceptanceTab({
  detail,
  onTaskSelect,
}: {
  detail: MatterixExecutionDetail;
  onTaskSelect?: (taskName: string) => void;
}) {
  const acceptance = detail.acceptance;
  const checks = matterixAcceptanceChecks(acceptance);
  const passed = matterixExecutionPassed(detail);
  const failed =
    acceptance?.passed === false ||
    ['CANCELLED', 'FAILED', 'INTERRUPTED', 'REJECTED', 'TIMED_OUT'].includes(detail.status.toUpperCase());
  const protocolTasks = React.useMemo(() => matterixProtocolTaskRows(detail), [detail]);
  const remainingRows = matterixAcceptanceRows(acceptance).map((row) => {
    const parts = row.key.split('.');
    const field = parts.at(-1) ?? row.key;
    const stage = parts.length > 1 ? `${matterixFieldLabel(parts[0])} · ` : '';
    return {
      key: row.key,
      label: `${stage}${matterixFieldLabel(field)}`,
      value: formatMatterixCompactValue(row.value),
    };
  });
  const resultSummary = matterixResultSummary(detail.result);

  const checkLabel = (name: string) => {
    const parts = name.split(' · ');
    return parts.length > 1
      ? `${matterixFieldLabel(parts[0])} · ${matterixFieldLabel(parts.at(-1) ?? name)}`
      : matterixFieldLabel(name);
  };

  return (
    <div className="space-y-4">
      {acceptance || protocolTasks.length > 0 ? (
        <div
          className={`flex items-center gap-2 rounded-md border p-3 text-sm ${
            passed === true
              ? 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/30 text-green-800 dark:text-green-200'
              : failed
                ? 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 text-red-800 dark:text-red-200'
                : 'border-gray-200 dark:border-slate-700 bg-gray-50 dark:bg-slate-800/60 text-gray-700 dark:text-gray-200'
          }`}
        >
          {passed === true ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : failed ? (
            <XCircle className="h-4 w-4" />
          ) : (
            <AlertTriangle className="h-4 w-4" />
          )}
          <strong>
            {passed === true
              ? detail.scope === 'protocol'
                ? '协议验收通过'
                : '验收通过'
              : failed
                ? detail.scope === 'protocol'
                  ? '协议验收未通过'
                  : '验收未通过'
                : detail.scope === 'protocol'
                  ? '协议未完成或验收记录不完整，不能判定通过'
                  : '验收记录未提供通过结论'}
          </strong>
          {checks.length > 0 && (
            <span>
              · {checks.filter((check) => check.passed === true).length}/{checks.length} 项检查通过
            </span>
          )}
        </div>
      ) : (
        <p className="rounded-md border border-gray-200 p-3 text-sm text-gray-500 dark:border-slate-700 dark:text-gray-400">
          {detail.scope === 'protocol' ? '本协议未提供验收记录；缺失记录不能视为通过。' : '本任务未提供设备验收记录。'}
        </p>
      )}

      {protocolTasks.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-sm font-semibold text-gray-900 dark:text-white">协议任务结果</h4>
          <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-slate-700">
            <table className="min-w-full divide-y divide-gray-200 text-sm dark:divide-slate-700">
              <thead className="bg-gray-50 dark:bg-slate-800">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">任务</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">状态</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-300">验收</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-slate-800">
                {protocolTasks.map((task) => {
                  const completed = task.status.toUpperCase() === 'COMPLETED';
                  const taskPassed = completed && task.passed === true;
                  return (
                    <tr key={task.taskName}>
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => onTaskSelect?.(task.taskName)}
                          disabled={!onTaskSelect}
                          className="block text-left font-medium text-gray-900 enabled:hover:text-blue-600 enabled:hover:underline dark:text-white dark:enabled:hover:text-blue-400"
                        >
                          {task.label}
                        </button>
                        <span className="font-mono text-[10px] text-gray-400 dark:text-gray-500">{task.taskName}</span>
                      </td>
                      <td className="px-3 py-2 text-gray-700 dark:text-gray-200">{statusLabel(task.status)}</td>
                      <td
                        className={`px-3 py-2 ${
                          taskPassed
                            ? 'text-green-600 dark:text-green-400'
                            : task.passed === false
                              ? 'text-red-600 dark:text-red-400'
                              : 'text-amber-600 dark:text-amber-400'
                        }`}
                      >
                        {taskPassed
                          ? '通过'
                          : !completed
                            ? '未完成或未执行'
                            : task.passed === false
                              ? '未通过'
                              : '验收记录缺失'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {checks.length > 0 && (
        <div className="space-y-2">
          {checks.map((check, index) => (
            <div
              key={`${String(check.name ?? 'check')}-${index}`}
              className="grid grid-cols-[1.2fr_1fr_1fr_auto] gap-2 rounded-md border border-gray-200 dark:border-slate-700 px-3 py-2 text-xs"
            >
              <span className="font-medium text-gray-900 dark:text-white">
                <span className="block">{checkLabel(String(check.name ?? `检查 ${index + 1}`))}</span>
                {checkLabel(String(check.name ?? '')) !== String(check.name ?? '') && (
                  <span className="mt-0.5 block truncate font-mono text-[10px] font-normal text-gray-400 dark:text-gray-500">
                    {String(check.name)}
                  </span>
                )}
              </span>
              <span className="text-gray-600 dark:text-gray-300">
                实际：{formatMatterixCompactValue(check.actual, String(check.unit ?? ''))}
              </span>
              <span className="text-gray-600 dark:text-gray-300">
                期望：{formatMatterixCompactValue(check.expected, String(check.unit ?? ''))}
              </span>
              <span
                className={
                  check.passed === true
                    ? 'text-green-600 dark:text-green-400'
                    : check.passed === false
                      ? 'text-red-600 dark:text-red-400'
                      : 'text-amber-600 dark:text-amber-400'
                }
              >
                {check.passed === true ? '通过' : check.passed === false ? '未通过' : '未记录'}
              </span>
            </div>
          ))}
        </div>
      )}

      {remainingRows.length > 0 && <SummaryTable rows={remainingRows} />}
      {detail.scope !== 'protocol' && resultSummary.sections.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
            执行摘要
          </h4>
          {resultSummary.syntheticModel && (
            <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
              浓度来自合成模型，并非仪器测量。
            </div>
          )}
          <div className="space-y-4">
            {resultSummary.sections.map((section) => (
              <section key={section.key}>
                <h5 className="mb-2 text-xs font-semibold text-gray-700 dark:text-gray-200">{section.title}</h5>
                <SummaryTable rows={section.rows} />
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function VideoTab({ detail }: { detail: MatterixExecutionDetail }) {
  if (detail.videos.length === 0) {
    return <p className="text-sm text-gray-500 dark:text-gray-400">{matterixVideoNotice(detail)}</p>;
  }
  return (
    <div className="space-y-4">
      {detail.videos.map((video) => (
        <section key={video.key} className="space-y-2 rounded-lg border border-gray-200 dark:border-slate-700 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-white">
              <Video className="h-4 w-4" />
              {video.name}
            </div>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {[
                video.width && `${video.width}×${video.height}`,
                video.fps && `${video.fps} fps`,
                formatBytes(video.bytes),
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </div>
          <video
            className="max-h-[32rem] w-full rounded-md bg-black"
            controls
            preload="metadata"
            src={artifactDownloadUrl(video.key, true)}
          />
          <a
            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline dark:text-blue-400"
            href={artifactDownloadUrl(video.key)}
          >
            <Download className="h-3.5 w-3.5" />
            下载视频
          </a>
        </section>
      ))}
    </div>
  );
}

function RawFilePreview({ artifact }: { artifact: MatterixArtifact }) {
  const [text, setText] = React.useState<string | null>(null);
  const [truncated, setTruncated] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    setText(null);
    setTruncated(false);
    setError(null);
    fetch(artifactDownloadUrl(artifact.key, true), { headers: { Range: 'bytes=0-65535' } })
      .then(async (response) => {
        if (!response.ok) throw new Error(`预览失败（HTTP ${response.status}）`);
        const value = await readBoundedResponseText(response);
        if (!cancelled) {
          setText(value.text);
          setTruncated(value.truncated);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '预览失败');
      });
    return () => {
      cancelled = true;
    };
  }, [artifact.key]);

  if (error) return <p className="text-sm text-red-600 dark:text-red-400">{error}</p>;
  if (text === null) {
    return (
      <p className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
        <LoaderCircle className="h-4 w-4 animate-spin" />
        正在读取前 64 KiB…
      </p>
    );
  }
  return (
    <div>
      <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
        最多显示前 64 KiB{truncated ? '（预览已截断）' : ''}；完整内容请下载原文件。
      </p>
      <pre className="max-h-96 overflow-auto rounded-md border border-gray-200 bg-gray-50 p-3 text-xs dark:border-slate-700 dark:bg-slate-950 dark:text-gray-300">
        <code>{text}</code>
      </pre>
    </div>
  );
}

function DownloadsTab({ detail }: { detail: MatterixExecutionDetail }) {
  const [preview, setPreview] = React.useState<MatterixArtifact | null>(null);
  const previewable = (artifact: MatterixArtifact) =>
    /\.(json|jsonl)$/i.test(artifact.name) || ['json', 'jsonl'].includes(artifact.kind.toLowerCase());
  const zipFiles = detail.files.filter(
    (file) => file.kind.toLowerCase() === 'zip' || file.name.toLowerCase().endsWith('.zip')
  );
  const samplesPreview = detail.samples.slice(-20);

  return (
    <div className="space-y-6">
      <section>
        <div className="mb-2 flex items-center justify-between gap-2">
          <h4 className="text-sm font-semibold text-gray-900 dark:text-white">归档与文件</h4>
          {detail.scope === 'task' && zipFiles.length > 0 && (
            <a
              className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50 dark:border-slate-600 dark:text-gray-200 dark:hover:bg-slate-800"
              href={artifactDownloadUrl(zipFiles[0].key)}
            >
              <Download className="h-3.5 w-3.5" />
              下载完整 ZIP
            </a>
          )}
        </div>
        {detail.files.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">暂无文件记录。</p>
        ) : (
          <ul className="divide-y divide-gray-100 rounded-md border border-gray-200 dark:divide-slate-800 dark:border-slate-700">
            {detail.files.map((file) => (
              <li
                key={`${file.task_name ?? ''}:${file.key}`}
                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm text-gray-900 dark:text-white">
                    {file.task_name && (
                      <span className="mr-2 rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-600 dark:bg-slate-800 dark:text-gray-300">
                        {file.task_name}
                      </span>
                    )}
                    {file.name}
                  </div>
                  <div className="truncate font-mono text-[11px] text-gray-500 dark:text-gray-400">{file.key}</div>
                </div>
                <div className="flex items-center gap-3 text-xs">
                  {previewable(file) && (
                    <button
                      className="text-gray-600 hover:underline dark:text-gray-300"
                      onClick={() => setPreview(file)}
                    >
                      有界预览
                    </button>
                  )}
                  <a className="text-blue-600 hover:underline dark:text-blue-400" href={artifactDownloadUrl(file.key)}>
                    下载
                  </a>
                </div>
              </li>
            ))}
          </ul>
        )}
        {preview && (
          <div className="mt-3 rounded-md border border-gray-200 p-3 dark:border-slate-700">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-white">
                <FileJson className="h-4 w-4" />
                {preview.name}
              </span>
              <button
                className="text-xs text-gray-500 hover:underline dark:text-gray-400"
                onClick={() => setPreview(null)}
              >
                收起
              </button>
            </div>
            <RawFilePreview artifact={preview} />
          </div>
        )}
      </section>

      <section>
        <h4 className="mb-2 text-sm font-semibold text-gray-900 dark:text-white">原始快照</h4>
        <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
          显示最近 {samplesPreview.length} 条，最多 {detail.samples.length.toLocaleString()} 条参与曲线。
        </p>
        {samplesPreview.length === 0 && !detail.latest_sample ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">暂无原始样本。</p>
        ) : (
          <pre className="max-h-96 overflow-auto rounded-md border border-gray-200 bg-gray-50 p-3 text-xs dark:border-slate-700 dark:bg-slate-950 dark:text-gray-300">
            <code>{boundedJson(samplesPreview.length > 0 ? samplesPreview : detail.latest_sample)}</code>
          </pre>
        )}
      </section>
      <section>
        <h4 className="mb-2 text-sm font-semibold text-gray-900 dark:text-white">原始执行结果</h4>
        {detail.result ? (
          <pre className="max-h-96 overflow-auto rounded-md border border-gray-200 bg-gray-50 p-3 text-xs dark:border-slate-700 dark:bg-slate-950 dark:text-gray-300">
            <code>{boundedJson(detail.result)}</code>
          </pre>
        ) : (
          <p className="text-sm text-gray-500 dark:text-gray-400">暂无执行结果。</p>
        )}
      </section>
    </div>
  );
}

export function MatterixExecutionView({
  detail,
  refreshing,
  error,
  onRetry,
  onTaskSelect,
}: MatterixExecutionViewProps) {
  const [activeTab, setActiveTab] = React.useState<Tab>('观测曲线');
  const sourceLabel =
    detail.source === 'simulation'
      ? 'Isaac Sim 仿真'
      : detail.source === 'mock'
        ? 'Mock（物料平衡 / 合成测量）'
        : '无执行源';
  const recording = matterixRecording(detail);
  const protocolTaskCount = matterixProtocolTaskResults(detail).length;
  const latestTime =
    detail.scope === 'protocol' ? detail.latest_sample?.protocol_time_s : detail.latest_sample?.sim_time_s;

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium text-gray-900 dark:text-white">{statusLabel(detail.status)}</span>
          <span className="text-gray-400">·</span>
          <span className="text-gray-600 dark:text-gray-300">{sourceLabel}</span>
          {detail.scope === 'protocol' && protocolTaskCount > 0 && (
            <>
              <span className="text-gray-400">·</span>
              <span className="text-gray-600 dark:text-gray-300">{protocolTaskCount} 个任务</span>
            </>
          )}
          {detail.scope === 'protocol' && (
            <>
              <span className="text-gray-400">·</span>
              <span className="text-gray-600 dark:text-gray-300">{matterixRecordingStatusLabel(recording)}</span>
            </>
          )}
          {refreshing && (
            <span className="ml-auto flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              刷新中
            </span>
          )}
        </div>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          {detail.samples.length.toLocaleString()} 个样本
          {typeof latestTime === 'number'
            ? ` · 最新${detail.scope === 'protocol' ? '协议' : '仿真'}时间 ${latestTime.toFixed(3)} s`
            : ''}
          {recording?.error ? ` · ${recording.error}` : ''}
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex shrink-0 items-center gap-1 text-xs font-medium hover:underline"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            立即重试
          </button>
        </div>
      )}
      {detail.warnings.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          <div className="mb-1 flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4" />
            后端警告
          </div>
          <ul className="list-inside list-disc space-y-1">
            {detail.warnings.map((warning, index) => (
              <li key={`${warning}-${index}`}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-1 border-b border-gray-200 pb-2 dark:border-slate-700" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            onClick={() => setActiveTab(tab)}
            className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
              activeTab === tab
                ? 'bg-blue-600 text-white'
                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-slate-800'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {activeTab === '观测曲线' && <CurvesTab detail={detail} onTaskSelect={onTaskSelect} />}
        {activeTab === '数字孪生' && <StationTwinView detail={detail} />}
        {activeTab === '参数快照' && <ParametersTab detail={detail} />}
        {activeTab === '验收详情' && <AcceptanceTab detail={detail} onTaskSelect={onTaskSelect} />}
        {activeTab === '视频' && <VideoTab detail={detail} />}
        {activeTab === '下载与原始数据' && <DownloadsTab detail={detail} />}
      </div>
    </div>
  );
}
