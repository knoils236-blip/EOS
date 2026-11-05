'use client';

import * as React from 'react';
import { AlertTriangle, LoaderCircle, RefreshCw } from 'lucide-react';
import { getMatterixProtocolExecutionDetail } from '../api/protocolRunDetails';
import type { MatterixProtocolExecutionDetail } from '../types/matterixExecution';
import {
  isActiveMatterixStatus,
  isTerminalMatterixStatus,
  mergeMatterixSamples,
  shouldPollMatterixExecution,
} from '../utils/matterixExecution';
import { MatterixExecutionView } from './MatterixExecutionView';

interface ProtocolMatterixExecutionViewProps {
  protocolRunName: string;
  protocolRunStatus: string;
  onTaskSelect: (taskName: string) => void;
}

export function ProtocolMatterixExecutionView({
  protocolRunName,
  protocolRunStatus,
  onTaskSelect,
}: ProtocolMatterixExecutionViewProps) {
  const [detail, setDetail] = React.useState<MatterixProtocolExecutionDetail | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [refreshing, setRefreshing] = React.useState(false);
  const retryRef = React.useRef<(() => void) | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    let loading = false;
    let continuePolling = isActiveMatterixStatus(protocolRunStatus);
    let timer: ReturnType<typeof setTimeout> | undefined;
    let archiveRefreshed = false;
    setDetail(null);
    setError(null);
    setRefreshing(false);

    const load = async () => {
      if (cancelled || loading) return;
      loading = true;
      setRefreshing(true);
      try {
        const next = await getMatterixProtocolExecutionDetail(protocolRunName);
        if (cancelled) return;
        setDetail((current) =>
          current ? { ...next, samples: mergeMatterixSamples(current.samples, next.samples) } : next
        );
        setError(null);
        continuePolling = shouldPollMatterixExecution(next);
        if (continuePolling) {
          timer = setTimeout(load, 1000);
        } else if (next.source && isTerminalMatterixStatus(next.status) && !archiveRefreshed) {
          archiveRefreshed = true;
          timer = setTimeout(load, 700);
        }
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : '获取 Matterix 协议执行详情失败');
        if (continuePolling) timer = setTimeout(load, 1000);
      } finally {
        loading = false;
        if (!cancelled) setRefreshing(false);
      }
    };

    retryRef.current = () => {
      if (timer) clearTimeout(timer);
      timer = undefined;
      void load();
    };
    void load();
    return () => {
      cancelled = true;
      retryRef.current = null;
      if (timer) clearTimeout(timer);
    };
  }, [protocolRunName, protocolRunStatus]);

  return (
    <div className="h-full overflow-y-auto bg-white p-4 dark:bg-slate-900 md:p-6">
      <div className="mx-auto max-w-6xl space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">协议执行结果</h2>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            汇总整个协议运行的任务参数、协议时间轴、验收、完整视频与归档文件。
          </p>
        </div>
        {detail ? (
          <MatterixExecutionView
            detail={detail}
            refreshing={refreshing}
            error={error}
            onRetry={() => retryRef.current?.()}
            onTaskSelect={onTaskSelect}
          />
        ) : error ? (
          <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button
              type="button"
              onClick={() => retryRef.current?.()}
              className="inline-flex items-center gap-1 text-xs font-medium hover:underline"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              立即重试
            </button>
          </div>
        ) : (
          <p className="flex items-center gap-2 rounded-md border border-gray-200 p-4 text-sm text-gray-500 dark:border-slate-700 dark:text-gray-400">
            <LoaderCircle className="h-4 w-4 animate-spin" />
            正在读取整个协议的 Matterix 执行结果…
          </p>
        )}
      </div>
    </div>
  );
}
