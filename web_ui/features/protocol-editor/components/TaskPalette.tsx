'use client';

import { memo, useMemo, useState } from 'react';
import { GripVertical, PanelLeftClose, PanelLeftOpen, Plus, Search, RefreshCw, Settings2 } from 'lucide-react';
import type { BoundPreset, EditorScene } from '../lib/scenePresets';

export const TASK_DRAG_TYPE = 'application/x-eos-task-type';
export const CAPABILITY_DRAG_TYPE = 'application/x-eos-scene-capability';
// Atomic Matterix skills are for debugging and custom sequences, so they start folded
const COLLAPSED_GROUPS = new Set(['原子技能']);

interface TaskPaletteProps {
  scenes: EditorScene[];
  presets: BoundPreset[];
  selectedLabs: string[];
  usage: Map<string, number>;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onSelectScene: (labName: string) => void;
  onAdd: (preset: BoundPreset) => void;
  onAdvanced: () => void;
}

const TaskPaletteComponent = ({
  scenes,
  presets,
  selectedLabs,
  usage,
  loading,
  error,
  onRefresh,
  onSelectScene,
  onAdd,
  onAdvanced,
}: TaskPaletteProps) => {
  const [isOpen, setIsOpen] = useState(true);
  const [query, setQuery] = useState('');
  const [activeScene, setActiveScene] = useState('');
  const sceneKey = (scene: EditorScene) => `${scene.lab_name}/${scene.device_name}`;
  const scene =
    scenes.find((s) => sceneKey(s) === activeScene && selectedLabs.includes(s.lab_name)) ??
    scenes.find((s) => selectedLabs.includes(s.lab_name));
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = presets.filter(
      (preset) =>
        preset.scene === scene && (!q || `${preset.label} ${preset.id} ${preset.description}`.toLowerCase().includes(q))
    );
    return ['实验边界', '场景操作', '分析与反馈', '原子技能']
      .map((group) => [group, matches.filter((p) => p.group === group)] as const)
      .filter(([, items]) => items.length > 0);
  }, [presets, scene, query]);

  if (!isOpen)
    return (
      <div className="w-9 shrink-0 border-r border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 pt-2 text-center">
        <button
          onClick={() => setIsOpen(true)}
          className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-slate-800"
          aria-label="展开场景能力面板"
        >
          <PanelLeftOpen className="w-4 h-4" />
        </button>
      </div>
    );

  return (
    <aside
      className="w-72 shrink-0 border-r border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 flex flex-col min-h-0"
      aria-label="场景能力"
    >
      <div className="flex items-center justify-between px-3 pt-3 pb-2">
        <span className="text-sm font-semibold text-gray-900 dark:text-white">场景能力</span>
        <div className="flex gap-1">
          <button
            onClick={onRefresh}
            disabled={loading}
            className="p-1 rounded hover:bg-gray-100 dark:hover:bg-slate-800 disabled:opacity-50"
            aria-label="刷新场景能力"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => setIsOpen(false)}
            className="p-1 rounded hover:bg-gray-100 dark:hover:bg-slate-800"
            aria-label="收起场景能力面板"
          >
            <PanelLeftClose className="w-4 h-4" />
          </button>
        </div>
      </div>
      <div className="px-3 pb-3 space-y-2">
        <label className="block text-xs text-gray-500 dark:text-gray-400" htmlFor="editor-scene">
          实验场景
        </label>
        <select
          id="editor-scene"
          value={scene ? sceneKey(scene) : ''}
          disabled={loading}
          onChange={(e) => {
            const next = scenes.find((s) => sceneKey(s) === e.target.value);
            if (next) {
              setActiveScene(e.target.value);
              onSelectScene(next.lab_name);
            }
          }}
          className="w-full rounded border border-gray-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-2 text-sm"
        >
          <option value="" disabled>
            请选择实验场景
          </option>
          {scenes.map((s) => (
            <option key={sceneKey(s)} value={sceneKey(s)}>
              {s.label} · {s.lab_name}
            </option>
          ))}
        </select>
        {scene && (
          <p className="text-xs text-gray-500 break-all">
            {scene.device_name} · {scene.loaded ? '实验室已加载' : '仅设计；执行前需加载实验室'}
          </p>
        )}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="搜索场景能力"
            placeholder="搜索开门、移液、取放…"
            className="w-full pl-8 pr-2 py-1.5 text-sm border border-gray-300 dark:border-slate-600 rounded bg-white dark:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {error && (
          <p role="alert" className="text-xs text-red-600 py-3">
            {error}
          </p>
        )}
        {loading && (
          <p role="status" className="text-xs text-gray-500 py-3">
            正在读取已登记能力…
          </p>
        )}
        {!loading && !error && groups.length === 0 && (
          <p className="text-xs text-gray-500 py-3">
            {scene ? '没有匹配的能力。' : '选择实验场景后显示对应能力。未登记能力的设备可通过高级任务配置。'}
          </p>
        )}
        {groups.map(([group, items]) => (
          <details key={group} open={!COLLAPSED_GROUPS.has(group) || query.trim() !== ''} className="mb-4">
            <summary className="flex justify-between text-xs font-medium text-gray-500 dark:text-gray-400 mb-2 cursor-pointer">
              {group}
              <span>{items.length}</span>
            </summary>
            <div className="space-y-2">
              {items.map((preset) => (
                <div
                  key={preset.key}
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData(CAPABILITY_DRAG_TYPE, preset.key);
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  className="group flex items-start gap-2 p-3 rounded-xl border border-teal-200 dark:border-teal-900 bg-white dark:bg-slate-800 hover:border-teal-500 cursor-grab active:cursor-grabbing"
                  title={preset.description}
                >
                  <GripVertical className="w-3.5 h-3.5 mt-0.5 shrink-0 text-gray-400" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-gray-900 dark:text-white leading-relaxed break-words">
                      {preset.label}
                    </div>
                    <div className="text-[11px] text-gray-500 dark:text-gray-400 mt-1 break-all font-mono">
                      {preset.id}
                    </div>
                    {(usage.get(preset.key) ?? 0) > 0 && (
                      <span className="text-[11px] text-teal-700 dark:text-teal-300">
                        已添加 {usage.get(preset.key)} 次
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => onAdd(preset)}
                    className="p-1 rounded text-teal-700 dark:text-teal-300 hover:bg-teal-50 dark:hover:bg-slate-700 focus-visible:ring-2 focus-visible:ring-teal-500"
                    aria-label={`添加${preset.label}`}
                  >
                    <Plus className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          </details>
        ))}
      </div>
      <div className="px-3 py-3 border-t border-gray-200 dark:border-slate-700 space-y-2">
        <p className="text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
          拖入能力或点击 +，自动填写设备与默认参数。
          {scene?.scene_id === 'pipetting_realtime'
            ? '移液站操作按添加顺序连接；请从“实验初始化”开始，以对应的结束检查收尾。'
            : scene?.scene_id === 'franka_beaker'
              ? '先抓取烧杯，再组合升降，回到支撑面后松开。下降不会恢复累计上升预算。'
              : '按实验目的连接制备、测量与分析步骤。'}
        </p>
        <button
          onClick={onAdvanced}
          className="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300 hover:underline"
        >
          <Settings2 className="w-3.5 h-3.5" />
          高级任务…
        </button>
      </div>
    </aside>
  );
};

TaskPaletteComponent.displayName = 'TaskPalette';
export const TaskPalette = memo(TaskPaletteComponent);
