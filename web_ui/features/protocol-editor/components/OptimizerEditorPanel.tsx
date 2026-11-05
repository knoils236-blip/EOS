'use client';

import { useEffect, useState } from 'react';
import { X, AlertTriangle, Loader2, RefreshCw, Settings2 } from 'lucide-react';
import { useEditorStore } from '@/lib/stores/editorStore';
import { OptimizerPanel } from '@/features/campaigns/components/OptimizerPanel';
import type { DomainValue } from '@/features/campaigns/components/DomainEditor';
import { getOptimizerDefaultsResult } from '@/features/campaigns/api/optimizer';
import { generateOptimizerPython, buildOptimizerConfig } from '../utils/optimizerCodegen';
import {
  assertOptimizerDomainSaveable,
  createBayesianOptimizerDraft,
  isOptimizerCodeWritable,
  loadOptimizerEditorState,
  mergeOptimizerSaveValues,
  type OptimizerEditorState,
} from '../utils/optimizerEditorState';
import { serializeCurrentProtocol } from '@/lib/utils/protocolSerializer';

/**
 * Save the Python (optimizer.py) file to disk along with the current YAML and layout.
 * Uses current content (not originals) to avoid overwriting unsaved YAML changes.
 */
async function savePythonOnly(code: string) {
  const store = useEditorStore.getState();
  const { selectedPackage, selectedEntityType, selectedEntityName, isSaving, diskMtime } = store;

  if (!selectedPackage || !selectedEntityType || !selectedEntityName) {
    throw new Error('无法保存优化器：未选择协议文件。');
  }
  if (isSaving) throw new Error('已有保存操作正在进行，请稍后重试。');

  useEditorStore.getState().setIsSaving(true);
  try {
    let yamlToWrite = store.yaml;
    let layoutToWrite = store.layout;

    if (store.editorMode === 'visual') {
      const serialized = serializeCurrentProtocol();
      yamlToWrite = serialized.yaml;
      layoutToWrite = serialized.layoutJson;
      useEditorStore.getState().updateYaml(yamlToWrite);
      useEditorStore.getState().updateLayout(layoutToWrite);
    }

    const body: Record<string, unknown> = { yaml: yamlToWrite, python: code };

    if (selectedEntityType === 'protocols' && layoutToWrite) {
      body.json = layoutToWrite;
    }

    if (diskMtime != null) {
      body.expectedMtime = diskMtime;
    }

    const response = await fetch(
      `/api/filesystem/packages/${encodeURIComponent(selectedPackage)}/${selectedEntityType}/${selectedEntityName}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }
    );

    const data = response.ok ? await response.json() : await response.json().catch(() => null);
    const currentStore = useEditorStore.getState();
    const isSameSelection =
      currentStore.selectedPackage === selectedPackage &&
      currentStore.selectedEntityType === selectedEntityType &&
      currentStore.selectedEntityName === selectedEntityName;

    if (response.status === 409) {
      if (isSameSelection) currentStore.setConflictMtime(data?.mtime ?? null);
      throw new Error('optimizer.py 已在磁盘上更新，请先处理冲突后重试。');
    }

    if (!response.ok) {
      const detail =
        typeof data?.error === 'string'
          ? data.error
          : typeof data?.message === 'string'
            ? data.message
            : '';
      throw new Error(`保存优化器文件失败（HTTP ${response.status}）${detail ? `：${detail}` : ''}`);
    }

    if (!isSameSelection) return;
    currentStore.updatePython(code);
    currentStore.markSaved(data.mtime);
  } finally {
    useEditorStore.getState().setIsSaving(false);
  }
}

export function OptimizerEditorPanel() {
  const protocolType = useEditorStore((state) => state.protocolType);
  const setIsOptimizerPanelOpen = useEditorStore((state) => state.setIsOptimizerPanelOpen);
  const pythonContent = useEditorStore((state) => state.python);

  const [loadState, setLoadState] = useState<OptimizerEditorState>({ kind: 'loading' });
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let active = true;
    setLoadState({ kind: 'loading' });
    void loadOptimizerEditorState(pythonContent, protocolType, getOptimizerDefaultsResult).then((next) => {
      if (active) setLoadState(next);
    });

    return () => {
      active = false;
    };
  }, [protocolType, pythonContent, reloadToken]);

  const handleSave = async (values: Record<string, unknown>, domain: DomainValue) => {
    if (!isOptimizerCodeWritable(pythonContent)) {
      throw new Error('检测到自定义 optimizer.py，已保持只读，不会覆盖自定义优化器代码。');
    }
    assertOptimizerDomainSaveable(domain);
    if (loadState.kind !== 'ready') throw new Error('优化器配置尚未就绪，请重试。');

    const mergedValues = mergeOptimizerSaveValues(loadState.defaults, values);
    const config = buildOptimizerConfig(mergedValues, domain, loadState.defaults.param_schema, pythonContent);
    const code = generateOptimizerPython(config);
    await savePythonOnly(code);
  };

  return (
    <div className="h-full flex flex-col bg-white dark:bg-slate-900">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-slate-700">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-white">优化器</h2>
        <button
          onClick={() => setIsOptimizerPanelOpen(false)}
          className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {loadState.kind === 'loading' && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            <span className="ml-2 text-sm text-gray-500">正在加载优化器配置…</span>
          </div>
        )}

        {loadState.kind === 'error' && (
          <div
            role="alert"
            className="space-y-3 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md"
          >
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" />
              <p className="text-sm text-red-700 dark:text-red-400">{loadState.message}</p>
            </div>
            <button
              type="button"
              onClick={() => setReloadToken((value) => value + 1)}
              className="inline-flex items-center gap-1 text-sm font-medium text-red-700 hover:text-red-800 dark:text-red-400"
            >
              <RefreshCw className="h-4 w-4" />
              重新加载
            </button>
          </div>
        )}

        {loadState.kind === 'unconfigured' && (
          <div className="space-y-3 rounded-md border border-gray-200 bg-gray-50 p-4 dark:border-slate-700 dark:bg-slate-800/50">
            <div className="flex items-start gap-2">
              <Settings2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-gray-500" />
              <div className="space-y-1">
                <p className="text-sm font-medium text-gray-800 dark:text-gray-200">此协议尚未配置优化器</p>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  保存协议仅保存实验流程，单次执行无需优化器。需要自动选参时，请先设置可调整变量和优化目标。
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() =>
                setLoadState({
                  kind: 'ready',
                  source: 'draft',
                  defaults: createBayesianOptimizerDraft(),
                })
              }
              className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 dark:border-slate-600 dark:bg-slate-900 dark:text-gray-200 dark:hover:bg-slate-700"
            >
              配置优化器
            </button>
          </div>
        )}

        {loadState.kind === 'custom' && (
          <div className="flex items-start gap-2 p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-md">
            <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 flex-shrink-0" />
            <p className="text-sm text-amber-700 dark:text-amber-400">
              此协议使用自定义优化器。请在代码编辑器中修改，以保留现有逻辑。
            </p>
          </div>
        )}

        {loadState.kind === 'ready' && (
          <div className="space-y-3">
            {loadState.source === 'draft' && (
              <p className="text-xs text-gray-500 dark:text-gray-400">
                填写可调整变量和优化目标后，点击保存配置。草稿尚未写入协议。
              </p>
            )}
            <OptimizerPanel
              key={`${loadState.source}:${JSON.stringify(loadState.defaults)}`}
              mode="editor"
              defaults={loadState.defaults}
              onSave={handleSave}
            />
          </div>
        )}
      </div>
    </div>
  );
}
