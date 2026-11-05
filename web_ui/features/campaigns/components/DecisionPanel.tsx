'use client';

import * as React from 'react';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Compass, RefreshCw, Sparkles } from 'lucide-react';
import { Badge, getStatusBadgeVariant } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import { statusLabel } from '@/lib/labels';
import { useUser } from '@/contexts/UserContext';
import { getCampaignDecision, reviewCampaignDecision } from '../api/decision';
import type { DecisionSnapshot, DecisionReview } from '../types/decision';

const METHOD_LABELS: Record<string, string> = {
  ai: 'AI 选参',
  bayesian: '贝叶斯选参',
  initial_design: '初始探索',
  initial_design_fallback: 'AI 不可用，初始探索接替',
  bayesian_fallback: 'AI 不可用，优化器接替',
  optimizer_fallback: 'AI 不可用，优化器接替',
  provided: '指定条件',
  optimizer: '优化器选参',
  unknown: '未记录选择依据',
};
const SOURCE_LABELS: Record<string, string> = {
  synthetic_model: '合成测量',
  simulation: '仿真执行',
  mock: 'Mock 执行',
  unknown: '来源未记录',
};
const QUALITY_LABELS: Record<string, string> = {
  blank_out_of_range: '空白读数超出范围',
  reference_out_of_range: '参考样读数超出范围',
  response_out_of_range: '测量读数超出范围',
  replicates_disagree: '重复测量差异过大',
};
const number = (value: number) => value.toLocaleString('zh-CN', { maximumSignificantDigits: 6 });
const source = (value: string) => SOURCE_LABELS[value] ?? value;
const METRIC_LABELS: Record<string, string> = {
  'prepare.stock_volume_ul': '母液量（µL）',
  'score.loss': '浓度误差平方（mM²）',
};
const metric = (key: string) => METRIC_LABELS[key] ?? key;

export function DecisionPanel({ campaignName, refreshKey }: { campaignName: string; refreshKey: string }) {
  const { canSubmit } = useUser();
  const [snapshot, setSnapshot] = React.useState<DecisionSnapshot | null>(null);
  const [error, setError] = React.useState('');
  const [refresh, setRefresh] = React.useState(0);
  const [question, setQuestion] = React.useState('请分析实验进展、数据质量、停止原因，并提出下一步可验证的建议。');
  const [review, setReview] = React.useState<DecisionReview | null>(null);
  const [reviewError, setReviewError] = React.useState('');
  const [reviewing, setReviewing] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    getCampaignDecision(campaignName)
      .then((result) => {
        if (cancelled) return;
        setError(result.error ?? '');
        if (result.data) setSnapshot(result.data);
      })
      .catch(() => {
        if (!cancelled) setError('无法获取实验决策数据，请重试。');
      });
    return () => {
      cancelled = true;
    };
  }, [campaignName, refreshKey, refresh]);

  const requestReview = async () => {
    setReviewing(true);
    setReviewError('');
    try {
      const result = await reviewCampaignDecision(campaignName, question.trim());
      if (result.data) {
        setReview(result.data);
        setRefresh((value) => value + 1);
      } else setReviewError(result.error ?? 'AI 分析失败，请重试。');
    } catch {
      setReviewError('AI 分析请求失败，请重试。');
    } finally {
      setReviewing(false);
    }
  };

  const rounds = snapshot?.rounds ?? [];
  const passed = rounds.filter((row) => row.quality === 'passed').length;
  const rejected = rounds.filter((row) => row.quality === 'rejected').length;
  const groups = new Set(
    rounds
      .filter((row) => row.backend !== 'unknown' && row.data_source !== 'unknown')
      .map((row) => `${row.backend}/${row.data_source}/${row.model_version}`)
  );

  return (
    <section
      className="rounded-lg border border-gray-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900"
      aria-labelledby="decision-heading"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 id="decision-heading" className="flex items-center gap-2 text-lg font-semibold">
            <Compass className="h-5 w-5" />
            实验决策
          </h2>
          <p className="mt-1 text-sm text-gray-500">把实验条件、测量与质控放在一起，核对每轮实验依据。</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setRefresh((value) => value + 1)} aria-label="刷新实验决策">
          <RefreshCw className="mr-1 h-4 w-4" />
          刷新
        </Button>
      </div>
      {error && (
        <p role="alert" className="mt-3 text-sm text-red-600">
          {error}
        </p>
      )}
      {!snapshot && !error && (
        <p className="mt-4 text-sm text-gray-500" role="status">
          正在读取实验记录…
        </p>
      )}
      {snapshot && (
        <>
          <div className="my-4 flex flex-wrap gap-2">
            <Badge variant="info">已记录 {snapshot.total_runs} 轮</Badge>
            <Badge variant="success">本页质控通过 {passed} 轮</Badge>
            <Badge variant={rejected ? 'warning' : 'muted'}>本页质控未通过 {rejected} 轮</Badge>
            <Badge variant="muted">预算 {snapshot.max_runs || '未限定'} 轮</Badge>
          </div>
          <p className="text-sm">
            目标：
            {snapshot.objectives.length
              ? snapshot.objectives
                  .map(
                    (item) =>
                      `${item.direction === 'min' ? '最小化' : item.direction === 'max' ? '最大化' : '方向未记录'} ${metric(item.key)}`
                  )
                  .join('；')
              : '尚未记录优化目标'}
          </p>
          {snapshot.stop_reason && (
            <p className="mt-2 text-sm">
              停止说明：{snapshot.stop_reason}
              {snapshot.status === 'COMPLETED' && ' 完成状态不代表科学目标已达到。'}
            </p>
          )}
          {groups.size > 1 && (
            <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
              存在不同执行或测量来源，最优值按来源与模型版本分别计算。
            </p>
          )}
          <div className="my-4 grid gap-3 md:grid-cols-2">
            {snapshot.best.map((best) => (
              <div
                key={`${best.objective}/${best.backend}/${best.data_source}/${best.model_version}`}
                className="rounded-md bg-slate-50 p-3 dark:bg-slate-800"
              >
                <p className="text-xs text-gray-500">
                  本页质控通过记录中的最优 · {source(best.backend)} · {source(best.data_source)}
                </p>
                <p className="mt-1 font-medium">
                  {metric(best.objective)} = {number(best.value)}
                </p>
                <Link
                  className="text-xs text-blue-600 hover:underline"
                  href={`/protocol-runs/${encodeURIComponent(best.run_name)}`}
                >
                  {best.run_name}
                </Link>
              </div>
            ))}
          </div>
          {rounds.length > 0 ? (
            <div className="mt-4 max-h-[28rem] overflow-auto">
              <table className="w-full text-left text-sm">
                <caption className="mb-2 text-left text-xs text-gray-500">
                  最近 {rounds.length} 轮，最多显示 {snapshot.window} 轮。缺少质控记录的结果不会列入此处的最优值。
                </caption>
                <thead className="sticky top-0 border-b bg-white dark:border-slate-700 dark:bg-slate-900">
                  <tr>
                    {['实验轮次', '实验条件', '测量与来源', '质量检查', '目标值', '选择依据'].map((label) => (
                      <th key={label} className="px-2 py-2 font-medium">
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...rounds].reverse().map((row) => (
                    <tr key={row.run_name} className="border-b align-top dark:border-slate-800">
                      <td className="max-w-48 px-2 py-3">
                        <Link
                          className="break-all text-blue-600 hover:underline"
                          href={`/protocol-runs/${encodeURIComponent(row.run_name)}`}
                        >
                          {row.run_name}
                        </Link>
                        <div className="mt-1">
                          <Badge variant={getStatusBadgeVariant(row.status)}>{statusLabel(row.status)}</Badge>
                        </div>
                      </td>
                      <td className="px-2 py-3">
                        {Object.entries(row.inputs).map(([key, value]) => (
                          <p key={key} title={key}>
                            {metric(key)}: {number(value)}
                          </p>
                        ))}
                      </td>
                      <td className="px-2 py-3">
                        <p>
                          {row.measurement.value_mm !== undefined ? (
                            <>
                              {number(row.measurement.value_mm)}
                              {row.measurement.uncertainty_mm !== undefined &&
                                ` ± ${number(row.measurement.uncertainty_mm)}`}{' '}
                              {row.measurement.unit}
                            </>
                          ) : (
                            '未记录测量值'
                          )}
                        </p>
                        {row.measurement.target_mm !== undefined && (
                          <p className="text-xs text-gray-500">目标 {number(row.measurement.target_mm)} mM</p>
                        )}
                        <p className="mt-1 text-xs text-gray-500">
                          {source(row.backend)} · {source(row.data_source)}
                        </p>
                        {row.model_version && <p className="text-xs text-gray-500">{row.model_version}</p>}
                        {row.sample_id && <p className="mt-1 break-all text-xs text-gray-500">样品：{row.sample_id}</p>}
                        {row.files
                          .filter((file) => file.name === 'observation.json')
                          .map((file) => (
                            <a
                              key={file.task_name}
                              className="mt-1 block text-xs text-blue-600 hover:underline"
                              href={`/api/files/download/${[row.run_name, file.task_name, file.name].map(encodeURIComponent).join('/')}`}
                            >
                              原始观测
                            </a>
                          ))}
                      </td>
                      <td className="px-2 py-3">
                        <Badge
                          variant={
                            row.quality === 'passed' ? 'success' : row.quality === 'rejected' ? 'error' : 'muted'
                          }
                        >
                          {row.quality === 'passed' ? '通过' : row.quality === 'rejected' ? '未通过' : '未记录'}
                        </Badge>
                        {row.quality_reasons.map((reason) => (
                          <p key={reason} className="mt-1 text-xs">
                            {QUALITY_LABELS[reason] ?? reason}
                          </p>
                        ))}
                      </td>
                      <td className="px-2 py-3">
                        {Object.entries(row.objectives).map(([key, value]) => (
                          <p key={key} title={key}>
                            {metric(key)}: {number(value)}
                          </p>
                        ))}
                      </td>
                      <td className="min-w-40 max-w-72 px-2 py-3">
                        <details>
                          <summary className="cursor-pointer">
                            {METHOD_LABELS[row.selection.method] ?? row.selection.method}
                          </summary>
                          <p className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-xs">
                            {row.selection.reason}
                          </p>
                        </details>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="my-4 text-sm text-gray-500">尚无实验轮次。实验开始后将在这里显示条件和数据。</p>
          )}
          <div className="mt-5 border-t pt-4 dark:border-slate-700">
            <h3 className="flex items-center gap-2 font-medium">
              <Sparkles className="h-4 w-4" />
              AI 实验分析
            </h3>
            <p className="my-2 text-xs text-gray-500">
              按需发送本页最近 {snapshot.window} 轮的条件、测量和质控信息到配置的 AI
              服务。分析只提供建议，不自动修改或执行实验。
            </p>
            {!snapshot.ai.configured && (
              <p className="mb-2 text-sm text-amber-700 dark:text-amber-300">
                AI 尚未配置。请在服务器填写服务 URL、API Key 和模型名称。
              </p>
            )}
            <label htmlFor="decision-question" className="text-sm">
              本次想分析的问题
            </label>
            <Textarea
              id="decision-question"
              value={question}
              maxLength={2000}
              onChange={(event) => setQuestion(event.target.value)}
              className="mt-1 min-h-20"
            />
            <Button
              size="sm"
              className="mt-2"
              onClick={requestReview}
              disabled={!canSubmit || !snapshot.ai.configured || reviewing || !question.trim()}
            >
              {reviewing ? '正在分析…' : '生成 AI 分析'}
            </Button>
            {reviewError && (
              <p role="alert" className="mt-2 text-sm text-red-600">
                {reviewError}
              </p>
            )}
            {review && (
              <div className="mt-4 rounded-md bg-slate-50 p-4 dark:bg-slate-800">
                <p className="mb-3 text-xs text-gray-500">
                  {review.model} · 数据时间 {new Date(review.snapshot_at).toLocaleString('zh-CN')}
                </p>
                {review.snapshot_id !== snapshot.snapshot_id && (
                  <p className="mb-3 text-sm text-amber-700 dark:text-amber-300">
                    这份分析与当前显示的数据版本不同，请刷新或重新生成。
                  </p>
                )}
                <div className="space-y-2 text-sm [&_li]:ml-5 [&_ul]:list-disc [&_ol]:list-decimal [&_p]:my-2">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} disallowedElements={['img']}>
                    {review.text}
                  </ReactMarkdown>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
