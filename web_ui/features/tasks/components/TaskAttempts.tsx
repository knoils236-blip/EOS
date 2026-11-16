'use client';

import * as React from 'react';
import { getTaskAttempts } from '../api/tasks';
import type { TaskAttemptRow } from '@/lib/db/queries';

const OUTCOME_LABELS: Record<string, string> = {
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  timed_out: '超时',
  cancelled: '已取消',
  interrupted: '重启中断',
};

function confirmation(value: boolean | null, yes: string, no: string): string {
  if (value === null) return '设备未回报';
  return value ? yes : no;
}

/** Each real launch of the task, so failed, cancelled and interrupted attempts stay visible after a retry or resume. */
export function TaskAttempts({ protocolRunName, taskName }: { protocolRunName: string | null; taskName: string }) {
  const [attempts, setAttempts] = React.useState<TaskAttemptRow[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let current = true;
    getTaskAttempts(protocolRunName, taskName)
      .then((rows) => current && setAttempts(rows))
      .catch((e: unknown) => current && setError(e instanceof Error ? e.message : '加载执行尝试失败'));
    return () => {
      current = false;
    };
  }, [protocolRunName, taskName]);

  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!attempts?.length) return null;

  return (
    <div>
      <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">执行尝试</div>
      <ol className="space-y-2">
        {attempts.map((a) => (
          <li key={a.id} className="rounded border border-gray-200 dark:border-slate-700 p-2 text-sm">
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-medium text-gray-900 dark:text-gray-100">
                第 {a.attemptNumber} 次 · {OUTCOME_LABELS[a.outcome] ?? a.outcome}
              </span>
              <span className="text-xs text-gray-500">{new Date(a.startedAt).toLocaleString()}</span>
            </div>
            {a.needsManualRecovery && (
              <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                需要人工恢复：此任务可能有不可逆动作，系统不会自动重做。
              </p>
            )}
            <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
              {confirmation(a.stopConfirmed, '已确认停止', '未确认停止')} ·{' '}
              {confirmation(a.deviceAvailable, '设备可用', '设备不可用')}
              {a.externalRequestId && ` · 请求 ${a.externalRequestId}`}
            </p>
            {a.error && <p className="mt-1 text-xs text-red-600 break-words">{a.error}</p>}
          </li>
        ))}
      </ol>
    </div>
  );
}
