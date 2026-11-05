'use client';

import { useState } from 'react';
import Link from 'next/link';
import { ExternalLink, Play } from 'lucide-react';
import { useEditorStore } from '@/lib/stores/editorStore';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import { SubmitTaskDialog } from '@/features/tasks/components/SubmitTaskDialog';
import { SubmitProtocolRunDialog } from '@/features/protocol-runs/components/SubmitProtocolRunDialog';
import { generateCloneNameForEntity } from '@/lib/utils/naming.server';
import { getSubmitContext, type SubmitContext } from '@/features/editor/api/submit-context';

const BTN =
  'flex items-center gap-2 px-3 py-1.5 text-sm rounded border border-green-600 text-green-700 dark:border-green-500 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/20 disabled:opacity-50 disabled:cursor-not-allowed';

interface EditorSubmitButtonProps {
  /** Whether the selected protocol is loaded in the orchestrator; tasks are always submittable. */
  isLoaded: boolean;
}

/**
 * Submits the task or protocol open in the editor as an on-demand task or a protocol run.
 * Submissions go through the same dialogs as the Tasks and Protocol Runs pages, so they show up there too.
 */
export function EditorSubmitButton({ isLoaded }: EditorSubmitButtonProps) {
  const { isConnected } = useOrchestratorConnected();
  const selectedPackage = useEditorStore((s) => s.selectedPackage);
  const selectedEntityType = useEditorStore((s) => s.selectedEntityType);
  const selectedEntityName = useEditorStore((s) => s.selectedEntityName);
  const isDirty = useEditorStore((s) => s.isDirty);

  const [context, setContext] = useState<SubmitContext | null>(null);
  const [isOpening, setIsOpening] = useState(false);
  const [lastSubmitted, setLastSubmitted] = useState<{ href: string; name: string } | null>(null);

  if (selectedEntityType !== 'tasks' && selectedEntityType !== 'protocols') return null;
  const isTask = selectedEntityType === 'tasks';
  const canSubmit = isConnected && (isTask || isLoaded);

  const handleOpen = async () => {
    if (!selectedPackage || !selectedEntityName) return;
    if (isDirty && !confirm('有未保存的修改。提交使用的是已保存并加载到编排器中的版本，是否继续？')) return;

    setIsOpening(true);
    try {
      const result = await getSubmitContext(selectedEntityType, selectedPackage, selectedEntityName);
      if (!result.type) {
        alert(`编排器中找不到这个${isTask ? '任务' : '协议'}的定义，请先保存并刷新软件包。`);
        return;
      }
      setContext(result);
    } finally {
      setIsOpening(false);
    }
  };

  const handleClose = (open: boolean) => {
    if (!open) setContext(null);
  };

  const handleSuccess = (name: string) => {
    setLastSubmitted({ name, href: isTask ? '/tasks' : `/protocol-runs/${encodeURIComponent(name)}` });
  };

  return (
    <>
      {lastSubmitted && (
        <Link
          href={lastSubmitted.href}
          className="flex items-center gap-1 text-xs text-blue-600 dark:text-yellow-400 hover:underline"
          title={isTask ? '在任务页面查看' : '查看协议运行详情'}
        >
          查看 {lastSubmitted.name}
          <ExternalLink className="w-3 h-3" />
        </Link>
      )}

      <button
        onClick={handleOpen}
        disabled={!canSubmit || isOpening}
        className={BTN}
        title={
          !isConnected
            ? '编排器未连接'
            : !canSubmit
              ? '请先加载协议'
              : isTask
                ? '将此任务作为按需任务提交'
                : '用此协议提交一次协议运行'
        }
      >
        <Play className={`w-4 h-4 ${isOpening ? 'animate-pulse' : ''}`} />
        {isTask ? '提交任务' : '提交协议运行'}
      </button>

      {context && isTask && (
        <SubmitTaskDialog
          open
          onOpenChange={handleClose}
          onSuccess={handleSuccess}
          taskSpecs={context.taskSpecs}
          labSpecs={context.labSpecs}
          initialType={context.type ?? undefined}
          generateCloneName={(name) => generateCloneNameForEntity('tasks', name)}
        />
      )}

      {context && !isTask && (
        <SubmitProtocolRunDialog
          open
          onOpenChange={handleClose}
          onSuccess={handleSuccess}
          protocolSpecs={context.protocolSpecs}
          taskSpecs={context.taskSpecs}
          labSpecs={context.labSpecs}
          initialType={context.type ?? undefined}
          generateCloneName={(name) => generateCloneNameForEntity('protocolRuns', name)}
        />
      )}
    </>
  );
}
