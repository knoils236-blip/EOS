'use client';

import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { AlertCircle, CheckCircle, Loader2, Sparkles, X } from 'lucide-react';
import { useEditorStore } from '@/lib/stores/editorStore';
import { draftProtocol, type ProtocolDraft } from '@/features/editor/api/draft';
import { extractHoldsFromRawTasks, parseYaml } from '@/lib/utils/editor-utils';
import { autoLayoutTasks } from '@/lib/utils/autoLayout';
import type { LabSpec } from '@/lib/api/specs';
import type { TaskNode } from '@/lib/types/protocol';

interface AIDraftDialogProps {
  isOpen: boolean;
  onClose: () => void;
  labSpecs: Record<string, LabSpec>;
}

/** Describe an experiment in words; an AI model drafts the protocol, which the scientist reviews before applying. */
export function AIDraftDialog({ isOpen, onClose, labSpecs }: AIDraftDialogProps) {
  const protocolType = useEditorStore((s) => s.protocolType);
  const currentLabs = useEditorStore((s) => s.labs);
  const taskCount = useEditorStore((s) => s.tasks.length);
  const loadProtocol = useEditorStore((s) => s.loadProtocol);

  const [goal, setGoal] = useState('');
  const [labs, setLabs] = useState<string[]>(() => (currentLabs.length ? currentLabs : Object.keys(labSpecs)));
  const [isDrafting, setIsDrafting] = useState(false);
  const [draft, setDraft] = useState<ProtocolDraft | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggleLab = (lab: string) =>
    setLabs((prev) => (prev.includes(lab) ? prev.filter((l) => l !== lab) : [...prev, lab]));

  const handleDraft = async () => {
    setIsDrafting(true);
    setError(null);
    setDraft(null);
    try {
      const result = await draftProtocol(goal, labs, protocolType || 'draft_protocol');
      setDraft(result.data ?? null);
      setError(result.error ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '生成草稿失败');
    } finally {
      setIsDrafting(false);
    }
  };

  const handleApply = () => {
    if (!draft) return;
    const { data, error: parseError } = parseYaml(draft.yaml);
    if (parseError || !data) {
      setError(`草稿不是有效的 YAML：${parseError}`);
      return;
    }
    if (taskCount > 0 && !confirm('草稿会替换当前画布上的任务（可撤销，未保存前不会写入文件）。继续吗？')) return;

    const parsed = data as { desc?: string; labs?: string[]; tasks?: Record<string, unknown>[] };
    const tasks = extractHoldsFromRawTasks(parsed.tasks ?? []) as unknown as TaskNode[];
    const layout = autoLayoutTasks(tasks);
    loadProtocol({
      type: protocolType, // keep the type that matches the protocol's folder
      desc: parsed.desc ?? '',
      labs: parsed.labs ?? labs,
      tasks: tasks.map((task) => ({ ...task, position: layout[task.name] ?? { x: 100, y: 100 } })),
    });
    onClose();
  };

  return (
    <Dialog.Root open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 z-50" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white dark:bg-slate-900 rounded-lg shadow-xl w-[720px] max-w-[95vw] max-h-[85vh] z-50 flex flex-col">
          <div className="flex items-center justify-between p-5 border-b border-gray-200 dark:border-slate-700">
            <Dialog.Title className="flex items-center gap-2 text-lg font-semibold text-gray-900 dark:text-white">
              <Sparkles className="w-5 h-5 text-purple-500" />
              AI 生成协议草稿
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
                <X className="w-5 h-5" />
              </button>
            </Dialog.Close>
          </div>

          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            <div>
              <label htmlFor="draft-goal" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                实验目标
              </label>
              <textarea
                id="draft-goal"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                rows={4}
                placeholder="例如：把起始数依次乘以两个因子，再用分析仪评估结果与 1024 的差距；起始数和两个因子交给优化器选择。"
                className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-slate-600 rounded-md bg-white dark:bg-slate-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-yellow-500"
              />
            </div>

            <div>
              <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">可用实验室</div>
              <div className="flex flex-wrap gap-3">
                {Object.keys(labSpecs).map((lab) => (
                  <label key={lab} className="flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300">
                    <input type="checkbox" checked={labs.includes(lab)} onChange={() => toggleLab(lab)} />
                    {lab}
                  </label>
                ))}
              </div>
            </div>

            {error && (
              <div className="flex items-start gap-2 p-3 text-sm rounded bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300">
                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                {error}
              </div>
            )}

            {draft && (
              <div className="space-y-3">
                <div
                  className={`flex items-center gap-2 text-sm font-medium ${draft.valid ? 'text-green-700 dark:text-green-400' : 'text-orange-700 dark:text-orange-400'}`}
                >
                  {draft.valid ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                  {draft.valid
                    ? `草稿已通过编排器校验（生成 ${draft.attempts} 次）`
                    : `草稿仍有 ${draft.errors.length} 个校验错误，应用后请在画布上修正`}
                </div>
                {!draft.valid && (
                  <ul className="text-xs text-orange-700 dark:text-orange-300 list-disc pl-5 space-y-0.5">
                    {draft.errors.map((e, i) => (
                      <li key={i}>{e.message}</li>
                    ))}
                  </ul>
                )}
                {draft.notes && (
                  <div className="p-3 text-sm rounded bg-purple-50 dark:bg-purple-900/20 text-gray-800 dark:text-gray-200 whitespace-pre-wrap">
                    {draft.notes}
                  </div>
                )}
                <pre className="p-3 text-xs rounded bg-gray-50 dark:bg-slate-800 text-gray-800 dark:text-gray-200 overflow-x-auto max-h-64">
                  {draft.yaml}
                </pre>
              </div>
            )}
          </div>

          <div className="flex items-center justify-end gap-2 p-4 border-t border-gray-200 dark:border-slate-700">
            <span className="mr-auto text-xs text-gray-500 dark:text-gray-400">
              草稿由 AI 生成，请逐项核对后再保存和运行。
            </span>
            <button
              onClick={handleDraft}
              disabled={!goal.trim() || labs.length === 0 || isDrafting}
              className="flex items-center gap-2 px-3 py-1.5 text-sm rounded border border-purple-500 text-purple-700 dark:text-purple-300 hover:bg-purple-50 dark:hover:bg-purple-900/20 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isDrafting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              {isDrafting ? '生成中…' : draft ? '重新生成' : '生成草稿'}
            </button>
            <button
              onClick={handleApply}
              disabled={!draft || isDrafting}
              className="px-3 py-1.5 text-sm rounded bg-blue-600 dark:bg-yellow-500 text-white dark:text-slate-900 hover:bg-blue-700 dark:hover:bg-yellow-600 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              应用到画布
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
