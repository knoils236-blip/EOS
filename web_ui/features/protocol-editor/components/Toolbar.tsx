'use client';

import { useState } from 'react';
import {
  FlaskConical,
  X,
  LayoutDashboard,
  Camera,
  CheckCircle,
  AlertCircle,
  Loader2,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { useEditorStore } from '@/lib/stores/editorStore';
import type { LabSpec } from '@/lib/api/specs';
import { OptimizerButton } from './OptimizerButton';
import { AIDraftDialog } from './AIDraftDialog';

interface ToolbarProps {
  labSpecs: Record<string, LabSpec>;
  onAutoLayout: () => void;
  onExportImage: () => void;
  onValidate: () => void;
}

function LabsSelector({ labSpecs }: { labSpecs: Record<string, LabSpec> }) {
  const labs = useEditorStore((state) => state.labs);
  const setLabs = useEditorStore((state) => state.setLabs);
  const [isOpen, setIsOpen] = useState(false);

  const toggleLab = (labName: string) => {
    if (labs.includes(labName)) {
      setLabs(labs.filter((l) => l !== labName));
    } else {
      setLabs([...labs, labName]);
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1 rounded-md transition-colors text-sm font-medium bg-white dark:bg-slate-800 text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-slate-600 hover:bg-gray-50 dark:hover:bg-slate-700"
      >
        <FlaskConical className="w-4 h-4" />
        <span>实验室（{labs.length})</span>
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setIsOpen(false)} />
          <div className="absolute left-0 top-full mt-2 w-80 bg-white dark:bg-slate-800 border border-gray-200 dark:border-slate-600 rounded-md shadow-lg z-20">
            <div className="p-3 border-b border-gray-200 dark:border-slate-600 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">选择实验室</h3>
              <button
                onClick={() => setIsOpen(false)}
                className="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-2 max-h-64 overflow-y-auto">
              {Object.entries(labSpecs).length === 0 ? (
                <div className="text-sm text-gray-500 dark:text-gray-400 p-2 text-center">暂无可用实验室</div>
              ) : (
                Object.entries(labSpecs).map(([labName, spec]) => (
                  <label
                    key={labName}
                    className="flex items-start gap-3 p-2 hover:bg-gray-50 dark:hover:bg-slate-700 rounded cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={labs.includes(labName)}
                      onChange={() => toggleLab(labName)}
                      className="mt-0.5 rounded border-gray-300 dark:border-slate-600"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{labName}</div>
                      {spec.desc && <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{spec.desc}</div>}
                    </div>
                  </label>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function ValidateButton({ onValidate }: { onValidate: () => void }) {
  const isValid = useEditorStore((state) => state.isValid);
  const isValidating = useEditorStore((state) => state.isValidating);
  const validationErrors = useEditorStore((state) => state.validationErrors);
  const isValidationPanelOpen = useEditorStore((state) => state.isValidationPanelOpen);
  const setIsValidationPanelOpen = useEditorStore((state) => state.setIsValidationPanelOpen);

  // Status icon shown next to the button
  const StatusIcon = () => {
    if (isValidating) return <Loader2 className="w-4 h-4 animate-spin text-gray-400" />;
    if (isValid === true) return <CheckCircle className="w-4 h-4 text-green-600 dark:text-green-400" />;
    if (isValid === false) {
      const count = validationErrors.length;
      return (
        <button
          onClick={() => setIsValidationPanelOpen(!isValidationPanelOpen)}
          className="flex items-center gap-1 text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 transition-colors"
          title={`${count} 个错误，点击${isValidationPanelOpen ? '隐藏' : '显示'}`}
        >
          <AlertCircle className="w-4 h-4" />
          <span className="text-xs font-medium">{count}</span>
        </button>
      );
    }
    return null;
  };

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={onValidate}
        disabled={isValidating}
        className="flex items-center gap-2 px-3 py-1 rounded-md transition-colors text-sm font-medium bg-white dark:bg-slate-800 text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-slate-600 hover:bg-gray-50 dark:hover:bg-slate-700 active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
        title="通过编排器验证协议"
      >
        <ShieldCheck className="w-4 h-4" />
        <span>验证</span>
      </button>
      <StatusIcon />
    </div>
  );
}

export function Toolbar({ labSpecs, onAutoLayout, onExportImage, onValidate }: ToolbarProps) {
  const isOptimizerPanelOpen = useEditorStore((state) => state.isOptimizerPanelOpen);
  const setIsOptimizerPanelOpen = useEditorStore((state) => state.setIsOptimizerPanelOpen);
  const [isDraftOpen, setIsDraftOpen] = useState(false);
  return (
    <div className="bg-white dark:bg-slate-900 border-b border-gray-200 dark:border-slate-700 px-4 py-1.5 flex items-center gap-4 shadow-sm">
      <LabsSelector labSpecs={labSpecs} />
      <ValidateButton onValidate={onValidate} />

      <div className="ml-auto flex items-center gap-4">
        <button
          onClick={() => setIsDraftOpen(true)}
          className="flex items-center gap-2 px-3 py-1 rounded-md transition-colors text-sm font-medium bg-white dark:bg-slate-800 text-purple-700 dark:text-purple-300 border border-purple-300 dark:border-purple-700 hover:bg-purple-50 dark:hover:bg-purple-900/20 active:scale-95"
          title="描述实验目标，由 AI 生成协议草稿"
        >
          <Sparkles className="w-4 h-4" />
          <span>AI 草稿</span>
        </button>
        {isDraftOpen && <AIDraftDialog isOpen onClose={() => setIsDraftOpen(false)} labSpecs={labSpecs} />}

        {/* Auto Layout Button */}
        <button
          onClick={onAutoLayout}
          className="flex items-center gap-2 px-3 py-1 rounded-md transition-colors text-sm font-medium bg-white dark:bg-slate-800 text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-slate-600 hover:bg-gray-50 dark:hover:bg-slate-700 active:scale-95"
          title="从左到右自动排列节点"
        >
          <LayoutDashboard className="w-4 h-4" />
          <span>自动布局</span>
        </button>

        {/* Export Image Button */}
        <button
          onClick={onExportImage}
          className="flex items-center gap-2 px-3 py-1 rounded-md transition-colors text-sm font-medium bg-white dark:bg-slate-800 text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-slate-600 hover:bg-gray-50 dark:hover:bg-slate-700 active:scale-95"
          title="将画布导出为图片"
        >
          <Camera className="w-4 h-4" />
          <span>导出图片</span>
        </button>

        <OptimizerButton onClick={() => setIsOptimizerPanelOpen(!isOptimizerPanelOpen)} isOpen={isOptimizerPanelOpen} />
      </div>
    </div>
  );
}
