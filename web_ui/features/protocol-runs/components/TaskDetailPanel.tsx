'use client';

import * as React from 'react';
import { matterixTaskLabel, statusLabel } from '@/lib/labels';

import { X } from 'lucide-react';
import { Badge, getStatusBadgeVariant } from '@/components/ui/Badge';
import { DetailField, TimelineSection, ConditionalJsonSection } from './shared';
import { TaskOutputFiles } from '@/features/files/components/TaskOutputFiles';
import { SECTION_DIVIDER } from '../styles';
import { ErrorBox } from '@/components/ui/ErrorBox';
import type { Task } from '@/lib/types/api';
import type { MatterixExecutionDetail } from '../types/matterixExecution';
import {
  isActiveMatterixStatus,
  isMatterixRichTask,
  isTerminalMatterixStatus,
  mergeMatterixSamples,
} from '../utils/matterixExecution';
import { getMatterixExecutionDetail } from '../api/protocolRunDetails';
import { MatterixExecutionView } from './MatterixExecutionView';

interface TaskDetailPanelProps {
  task: Task | null;
  onClose: () => void;
}

export function TaskDetailPanel({ task, onClose }: TaskDetailPanelProps) {
  const isMatterixTask = React.useMemo(() => (task ? isMatterixRichTask(task) : false), [task]);
  const [matterixDetail, setMatterixDetail] = React.useState<MatterixExecutionDetail | null>(null);
  const [matterixError, setMatterixError] = React.useState<string | null>(null);
  const [isRefreshingMatterix, setIsRefreshingMatterix] = React.useState(false);
  const matterixRetryRef = React.useRef<(() => void) | null>(null);

  React.useEffect(() => {
    matterixRetryRef.current = null;
    setMatterixDetail(null);
    setMatterixError(null);
    setIsRefreshingMatterix(false);
    if (!task?.protocol_run_name || !isMatterixTask) return;

    let cancelled = false;
    let loading = false;
    let active = isActiveMatterixStatus(task.status);
    let timer: ReturnType<typeof setTimeout> | undefined;
    let archiveRefreshed = false;

    const load = async () => {
      if (cancelled || loading) return;
      loading = true;
      setIsRefreshingMatterix(true);
      try {
        const next = await getMatterixExecutionDetail(task.protocol_run_name!, task.name);
        if (cancelled) return;
        setMatterixDetail((current) =>
          current
            ? { ...next, samples: mergeMatterixSamples(current.samples, next.samples) }
            : next
        );
        setMatterixError(null);
        active = isActiveMatterixStatus(next.status);

        if (active) {
          timer = setTimeout(load, 1000);
        } else if (next.source && isTerminalMatterixStatus(next.status) && !archiveRefreshed) {
          archiveRefreshed = true;
          timer = setTimeout(load, 700);
        }
      } catch (error) {
        if (cancelled) return;
        setMatterixError(error instanceof Error ? error.message : '获取 Matterix 执行详情失败');
        if (active) timer = setTimeout(load, 1000);
      } finally {
        loading = false;
        if (!cancelled) setIsRefreshingMatterix(false);
      }
    };

    matterixRetryRef.current = () => {
      if (timer) clearTimeout(timer);
      timer = undefined;
      void load();
    };
    void load();
    return () => {
      cancelled = true;
      matterixRetryRef.current = null;
      if (timer) clearTimeout(timer);
    };
  }, [isMatterixTask, task?.name, task?.protocol_run_name, task?.status]);

  if (!task) return null;

  const timelineEntries = [
    { label: '创建时间', timestamp: task.created_at ?? null },
    { label: '开始时间', timestamp: task.start_time ?? null },
    { label: '结束时间', timestamp: task.end_time ?? null },
  ];
  const displayName = isMatterixTask
    ? matterixTaskLabel(task.type, task.input_parameters ?? {})
    : task.name;

  return (
    <div
      className={`border-l border-gray-200 bg-white dark:border-slate-700 dark:bg-slate-900 overflow-y-auto ${
        isMatterixTask ? 'w-full md:w-[min(56rem,68vw)] md:min-w-[32rem]' : 'w-96'
      }`}
    >
      <div className="sticky top-0 bg-white dark:bg-slate-900 border-b border-gray-200 dark:border-slate-700 p-4 z-10">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">{displayName}</h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {isMatterixTask ? task.name : task.type}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-sm opacity-70 ring-offset-white dark:ring-offset-slate-900 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2 text-gray-900 dark:text-gray-400"
          >
            <X className="h-4 w-4" />
            <span className="sr-only">关闭</span>
          </button>
        </div>
      </div>

      <div className="p-4 space-y-6">
        <div className="space-y-3">
          <DetailField
            label="状态"
            value={<Badge variant={getStatusBadgeVariant(task.status)}>{statusLabel(task.status)}</Badge>}
          />
          {task.status === 'FAILED' && task.error_message && <ErrorBox error={task.error_message} />}
          <DetailField label="优先级" value={task.priority} />
          {task.protocol_run_name && <DetailField label="协议运行" value={task.protocol_run_name} />}
        </div>

        <div className={SECTION_DIVIDER}>
          <div className="text-sm font-medium text-gray-900 dark:text-white mb-2">时间线</div>
          <TimelineSection entries={timelineEntries} />
        </div>

        {isMatterixTask && matterixDetail ? (
          <MatterixExecutionView
            detail={matterixDetail}
            refreshing={isRefreshingMatterix}
            error={matterixError}
            onRetry={() => matterixRetryRef.current?.()}
          />
        ) : (
          <>
            {isMatterixTask && (
              <div className="text-sm text-gray-500 dark:text-gray-400">
                <span>{isRefreshingMatterix ? '正在读取 Matterix 执行快照…' : matterixError}</span>
                {matterixError && !isRefreshingMatterix && (
                  <button
                    type="button"
                    onClick={() => matterixRetryRef.current?.()}
                    className="ml-2 font-medium text-blue-600 hover:underline dark:text-blue-400"
                  >
                    立即重试
                  </button>
                )}
              </div>
            )}
            <ConditionalJsonSection data={task.input_parameters} label="输入参数" />
            <ConditionalJsonSection data={task.input_resources} label="输入资源" />
            <ConditionalJsonSection data={task.devices} label="设备" />
            <ConditionalJsonSection data={task.output_parameters} label="输出参数" />
            <ConditionalJsonSection data={task.output_resources} label="输出资源" />
            {task.output_file_names && task.output_file_names.length > 0 && (
              <div className={SECTION_DIVIDER}>
                <TaskOutputFiles
                  fileNames={task.output_file_names}
                  protocolRunName={task.protocol_run_name ?? null}
                  taskName={task.name}
                />
              </div>
            )}
            <ConditionalJsonSection data={task.meta} label="元数据" />
          </>
        )}
      </div>
    </div>
  );
}
