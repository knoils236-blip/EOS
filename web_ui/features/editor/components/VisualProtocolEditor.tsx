'use client';

import { useEffect, useState, useCallback } from 'react';
import { EditorSubmitButton } from './EditorSubmitButton';
import { Save, Code, AlertTriangle, RefreshCw, Download, Upload } from 'lucide-react';
import { ProtocolEditor } from '@/features/protocol-editor/components/ProtocolEditor';
import { useEditorStore } from '@/lib/stores/editorStore';
import { serializeCurrentProtocol } from '@/lib/utils/protocolSerializer';
import { loadEntity, unloadEntity, reloadEntity } from '@/features/editor/api/reload';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import { getLoadedStatus } from '@/features/editor/api/loaded-status';
import { DiskChangedBanner } from './DiskChangedBanner';

interface VisualProtocolEditorProps {
  hasJinja: boolean;
  onSave: () => void | Promise<void>;
  onReload?: () => void;
  onSwitchToCode: () => void;
}

export function VisualProtocolEditor({ hasJinja, onSave, onReload, onSwitchToCode }: VisualProtocolEditorProps) {
  const selectedPackage = useEditorStore((s) => s.selectedPackage);
  const selectedEntityName = useEditorStore((s) => s.selectedEntityName);
  const isDirty = useEditorStore((s) => s.isDirty);
  const updateYaml = useEditorStore((s) => s.updateYaml);
  const updateLayout = useEditorStore((s) => s.updateLayout);

  const tasks = useEditorStore((s) => s.tasks);
  const taskTemplates = useEditorStore((s) => s.taskTemplates);
  const protocolType = useEditorStore((s) => s.protocolType);
  const protocolDesc = useEditorStore((s) => s.protocolDesc);
  const labs = useEditorStore((s) => s.labs);

  const { isConnected } = useOrchestratorConnected();
  const [isReloading, setIsReloading] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isUnloading, setIsUnloading] = useState(false);
  const [isLoaded, setIsLoaded] = useState(false);

  // Fetch loaded status when entity changes
  useEffect(() => {
    if (!selectedEntityName) {
      setIsLoaded(false);
      return;
    }

    async function checkLoadedStatus() {
      try {
        const status = await getLoadedStatus();
        setIsLoaded(status.protocols.includes(selectedEntityName!));
      } catch {
        // ignore
      }
    }

    checkLoadedStatus();
  }, [selectedEntityName]);

  // Handle load
  const handleLoad = useCallback(async () => {
    if (!selectedPackage || !selectedEntityName) return;
    setIsLoading(true);
    try {
      const result = await loadEntity('protocols', selectedEntityName);
      if (result.success) {
        setIsLoaded(true);
        onReload?.();
      } else {
        alert(result.error || '加载协议失败');
      }
    } catch {
      alert('加载协议失败');
    } finally {
      setIsLoading(false);
    }
  }, [selectedPackage, selectedEntityName, onReload]);

  // Handle unload
  const handleUnload = useCallback(async () => {
    if (!selectedPackage || !selectedEntityName) return;
    setIsUnloading(true);
    try {
      const result = await unloadEntity('protocols', selectedEntityName);
      if (result.success) {
        setIsLoaded(false);
        onReload?.();
      } else {
        alert(result.error || '卸载协议失败');
      }
    } catch {
      alert('卸载协议失败');
    } finally {
      setIsUnloading(false);
    }
  }, [selectedPackage, selectedEntityName, onReload]);

  // Handle reload
  const handleReload = useCallback(async () => {
    if (!selectedPackage || !selectedEntityName) return;
    setIsReloading(true);
    try {
      const result = await reloadEntity('protocols', selectedEntityName);
      if (result.success) onReload?.();
      else alert(result.error || '重新加载协议失败');
    } catch {
      alert('重新加载协议失败');
    } finally {
      setIsReloading(false);
    }
  }, [selectedPackage, selectedEntityName, onReload]);

  // -----------------------------------------------------------------------
  // Serialize effect: protocol state → yaml/layout (ONE direction only)
  // No effect watches yaml→protocol, so no loop is possible.
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (tasks.length === 0 && !protocolType) return;

    const { yaml, layoutJson } = serializeCurrentProtocol();

    const state = useEditorStore.getState();
    if (yaml !== state.yaml) updateYaml(yaml);
    if (layoutJson !== state.layout) updateLayout(layoutJson);
  }, [tasks, taskTemplates, protocolType, protocolDesc, labs, updateYaml, updateLayout]);

  // Show warning if Jinja detected
  if (hasJinja) {
    return (
      <div className="h-full flex flex-col items-center justify-center bg-white dark:bg-gray-900 p-8">
        <AlertTriangle className="w-16 h-16 text-yellow-500 mb-4" />
        <h3 className="text-xl font-semibold mb-2">检测到 Jinja 模板</h3>
        <p className="text-gray-600 dark:text-gray-400 text-center mb-4">
          此协议包含 Jinja2 模板语法。为防止模板损坏，已禁用可视化编辑。
        </p>
        <button
          onClick={onSwitchToCode}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 dark:bg-yellow-500 text-white dark:text-slate-900 rounded hover:bg-blue-700 dark:hover:bg-yellow-600"
        >
          <Code className="w-4 h-4" />
          切换到代码编辑器
        </button>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-white dark:bg-gray-900">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-gray-900 dark:text-white">协议： {selectedEntityName}</span>
          <div className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-500">
            <span>{selectedPackage}</span>
            <span>/</span>
            <span>协议</span>
            <span>/</span>
            <span>{selectedEntityName}</span>
          </div>
          {isDirty && <span className="text-xs text-orange-600 dark:text-orange-400">● 未保存</span>}
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={onSwitchToCode}
            className="flex items-center gap-2 px-3 py-1.5 text-sm rounded hover:bg-gray-100 dark:hover:bg-gray-800"
            title="切换到代码编辑器"
          >
            <Code className="w-4 h-4" />
            代码
          </button>

          {!isLoaded && (
            <button
              onClick={handleLoad}
              disabled={isLoading || !isConnected}
              className="flex items-center gap-2 px-3 py-1.5 text-sm rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
              title="加载协议"
            >
              <Download className={`w-4 h-4 ${isLoading ? 'animate-pulse' : ''}`} />
              加载
            </button>
          )}

          {isLoaded && (
            <button
              onClick={handleUnload}
              disabled={isUnloading || !isConnected}
              className="flex items-center gap-2 px-3 py-1.5 text-sm rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
              title="卸载协议"
            >
              <Upload className={`w-4 h-4 ${isUnloading ? 'animate-pulse' : ''}`} />
              卸载
            </button>
          )}

          {isLoaded && (
            <button
              onClick={handleReload}
              disabled={isReloading || !isConnected}
              className="flex items-center gap-2 px-3 py-1.5 text-sm rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
              title="从磁盘重新加载协议"
            >
              <RefreshCw className={`w-4 h-4 ${isReloading ? 'animate-spin' : ''}`} />
              重新加载
            </button>
          )}

          <EditorSubmitButton isLoaded={isLoaded} />

          <button
            onClick={() => onSave()}
            disabled={!isDirty}
            className="flex items-center gap-2 px-3 py-1.5 text-sm bg-blue-600 dark:bg-yellow-500 text-white dark:text-slate-900 rounded hover:bg-blue-700 dark:hover:bg-yellow-600 disabled:opacity-50 disabled:cursor-not-allowed"
            title="保存（Cmd/Ctrl+S）"
          >
            <Save className="w-4 h-4" />
            保存
          </button>
        </div>
      </div>

      <DiskChangedBanner onReload={onReload} />

      {/* Visual Editor */}
      <div className="flex-1 overflow-hidden">
        <ProtocolEditor />
      </div>
    </div>
  );
}
