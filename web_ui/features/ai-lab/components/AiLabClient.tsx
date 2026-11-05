'use client';

import { useState, useTransition } from 'react';
import { decideApproval, type ApprovalRequest, type NotebookEntry } from '../api/aiLab';

const KIND_LABELS: Record<string, string> = {
  hypothesis: '假设',
  design: '设计',
  observation: '观察',
  interpretation: '解读',
  decision: '决策',
  note: '备注',
};

const STATUS_LABELS: Record<ApprovalRequest['status'], string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已拒绝',
};

function PendingRequest({ request }: { request: ApprovalRequest }) {
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const decide = (decision: 'approve' | 'reject') =>
    startTransition(async () => {
      const result = await decideApproval(request.id, decision, note);
      setError(result.error ?? null);
    });

  return (
    <li className="rounded-xl border border-amber-300 dark:border-amber-800 bg-white dark:bg-slate-800 p-4 space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium text-gray-900 dark:text-white">
          #{request.id} {request.kind === 'campaign' ? '实验计划' : '协议运行'} {request.name}
        </span>
        <span className="text-xs text-gray-500">
          {request.requested_by} · {new Date(request.created_at).toLocaleString()}
        </span>
      </div>
      <p className="text-sm text-gray-700 dark:text-gray-300">真实实验室：{request.labs.join('、')}</p>
      {request.reason && (
        <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">理由：{request.reason}</p>
      )}
      <details>
        <summary className="text-xs text-gray-500 cursor-pointer">提交内容</summary>
        <pre className="text-xs overflow-x-auto mt-1">{JSON.stringify(request.payload, null, 2)}</pre>
      </details>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="审批意见（可选，AI 可以读到）"
        aria-label={`审批意见 #${request.id}`}
        className="w-full rounded border border-gray-300 dark:border-slate-600 bg-transparent p-2 text-sm"
        rows={2}
      />
      <div className="flex gap-2">
        <button
          onClick={() => decide('approve')}
          disabled={pending}
          className="px-3 py-1.5 rounded bg-teal-600 text-white text-sm hover:bg-teal-700 disabled:opacity-50"
        >
          批准并提交
        </button>
        <button
          onClick={() => decide('reject')}
          disabled={pending}
          className="px-3 py-1.5 rounded border border-gray-300 dark:border-slate-600 text-sm hover:bg-gray-50 dark:hover:bg-slate-700 disabled:opacity-50"
        >
          拒绝
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </li>
  );
}

export function AiLabClient({ approvals, notebook }: { approvals: ApprovalRequest[]; notebook: NotebookEntry[] }) {
  const pending = approvals.filter((a) => a.status === 'pending');
  const decided = approvals.filter((a) => a.status !== 'pending');

  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <section aria-labelledby="approvals-heading" className="space-y-4">
        <h2 id="approvals-heading" className="text-lg font-semibold text-gray-900 dark:text-white">
          待审批提交（{pending.length}）
        </h2>
        {pending.length === 0 ? (
          <p className="text-sm text-gray-500">没有等待审批的提交。仿真实验室上的提交会直接运行。</p>
        ) : (
          <ul className="space-y-3">
            {pending.map((request) => (
              <PendingRequest key={request.id} request={request} />
            ))}
          </ul>
        )}
        {decided.length > 0 && (
          <details>
            <summary className="text-sm text-gray-600 dark:text-gray-400 cursor-pointer">
              已处理（{decided.length}）
            </summary>
            <ul className="mt-2 space-y-1 text-sm text-gray-700 dark:text-gray-300">
              {decided.map((request) => (
                <li key={request.id}>
                  #{request.id} {request.name} · {STATUS_LABELS[request.status]}（{request.decided_by}）
                  {request.note ? ` · ${request.note}` : ''}
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <section aria-labelledby="notebook-heading" className="space-y-4">
        <h2 id="notebook-heading" className="text-lg font-semibold text-gray-900 dark:text-white">
          实验记录本
        </h2>
        {notebook.length === 0 ? (
          <p className="text-sm text-gray-500">
            还没有记录。AI 通过 MCP 的 add_notebook_entry 写入假设、设计理由和决策。
          </p>
        ) : (
          <ol className="space-y-3">
            {[...notebook].reverse().map((entry) => (
              <li key={entry.id} className="rounded-xl border border-gray-200 dark:border-slate-700 p-3">
                <div className="flex flex-wrap gap-2 text-xs text-gray-500 mb-1">
                  <span className="font-medium text-teal-700 dark:text-teal-300">
                    {KIND_LABELS[entry.kind] ?? entry.kind}
                  </span>
                  {entry.campaign_name && <span>实验计划 {entry.campaign_name}</span>}
                  {entry.protocol_run_name && <span>运行 {entry.protocol_run_name}</span>}
                  <span>
                    {entry.author} · {new Date(entry.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{entry.content}</p>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
