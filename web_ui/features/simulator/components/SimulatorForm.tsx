'use client';

import { useState } from 'react';
import { Plus, Trash2, Play, Loader2, Download, Upload } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Combobox } from '@/components/ui/Combobox';
import { DescriptionTooltip } from '@/components/ui/DescriptionTooltip';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { SimConfig, ProtocolRunConfig } from '../types';

interface Props {
  protocolTypes: string[];
  loading: boolean;
  onRun: (config: SimConfig) => void;
  onLoadConfig: (config: SimConfig) => void;
  config: SimConfig;
  setConfig: (config: SimConfig) => void;
}

export function SimulatorForm({ protocolTypes, loading, onRun, onLoadConfig, config, setConfig }: Props) {
  const [error, setError] = useState<string | null>(null);

  const updateProtocol = (index: number, updates: Partial<ProtocolRunConfig>) => {
    const protocols = config.protocols.map((p, i) => (i === index ? { ...p, ...updates } : p));
    setConfig({ ...config, protocols });
  };

  const addProtocol = () => {
    setConfig({
      ...config,
      protocols: [
        ...config.protocols,
        { type: protocolTypes[0] || '', iterations: 10, max_concurrent: 0, priority: 1 },
      ],
    });
  };

  const removeProtocol = (index: number) => {
    setConfig({ ...config, protocols: config.protocols.filter((_, i) => i !== index) });
  };

  const handleSubmit = () => {
    if (config.protocols.length === 0) {
      setError('请至少添加一个协议。');
      return;
    }
    if (config.protocols.some((p) => !p.type)) {
      setError('必须为所有协议选择类型。');
      return;
    }
    setError(null);
    onRun(config);
  };

  const downloadConfig = () => {
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'sim_config.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  const uploadConfig = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const parsed = JSON.parse(text) as SimConfig;
        onLoadConfig(parsed);
      } catch {
        setError('配置文件无效。');
      }
    };
    input.click();
  };

  return (
    <div className="bg-white dark:bg-slate-900 rounded-lg border border-gray-200 dark:border-slate-700 p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-white">配置</h2>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={uploadConfig} title="加载配置">
            <Upload className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="sm" onClick={downloadConfig} title="保存配置">
            <Download className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-md text-red-600 dark:text-red-400 text-sm">
          {error}
        </div>
      )}

      <div className="space-y-4">
        {/* Protocols */}
        <div>
          <Label className="mb-2 block">协议</Label>
          {/* Column headers */}
          <div className="flex items-center gap-2 px-3 mb-1">
            <div className="flex-1 min-w-0">
              <span className="text-xs text-gray-500 dark:text-gray-400">类型</span>
              <DescriptionTooltip description="要仿真的协议。" />
            </div>
            <div className="w-24">
              <span className="text-xs text-gray-500 dark:text-gray-400">迭代次数</span>
              <DescriptionTooltip description="为此协议类型创建的协议运行数量。" />
            </div>
            <div className="w-32">
              <span className="text-xs text-gray-500 dark:text-gray-400">最大并发数</span>
              <DescriptionTooltip description="此协议类型允许的最大并发运行数，0 表示不限。" />
            </div>
            <div className="w-20">
              <span className="text-xs text-gray-500 dark:text-gray-400">优先级</span>
              <DescriptionTooltip description="调度优先级，数值越大越优先。" />
            </div>
            <div className="w-8" />
          </div>
          <div className="space-y-2">
            {config.protocols.map((protocol, i) => (
              <div
                key={i}
                className="flex items-center gap-2 p-3 bg-gray-50 dark:bg-slate-800/50 rounded-md border border-gray-200 dark:border-slate-700"
              >
                <div className="flex-1 min-w-0">
                  <Combobox
                    options={protocolTypes.map((t) => ({ value: t, label: t }))}
                    value={protocol.type}
                    onChange={(v) => updateProtocol(i, { type: v })}
                    placeholder="协议类型"
                    searchPlaceholder="搜索协议…"
                    className="h-8 text-sm"
                  />
                </div>
                <div className="w-24">
                  <Input
                    type="number"
                    min={1}
                    value={protocol.iterations}
                    onChange={(e) => updateProtocol(i, { iterations: parseInt(e.target.value) || 1 })}
                    className="h-8 text-sm"
                  />
                </div>
                <div className="w-32">
                  <Input
                    type="number"
                    min={0}
                    value={protocol.max_concurrent}
                    onChange={(e) => updateProtocol(i, { max_concurrent: parseInt(e.target.value) || 0 })}
                    className="h-8 text-sm"
                  />
                </div>
                <div className="w-20">
                  <Input
                    type="number"
                    min={0}
                    value={protocol.priority}
                    onChange={(e) => updateProtocol(i, { priority: parseInt(e.target.value) || 1 })}
                    className="h-8 text-sm"
                  />
                </div>
                <Button variant="ghost" size="sm" onClick={() => removeProtocol(i)} className="h-8 w-8 p-0">
                  <Trash2 className="h-4 w-4 text-gray-400" />
                </Button>
              </div>
            ))}
          </div>
          <Button variant="ghost" size="sm" onClick={addProtocol} className="mt-2">
            <Plus className="h-4 w-4 mr-1" />
            添加协议
          </Button>
        </div>

        {/* Settings row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <Label htmlFor="scheduler" className="mb-1 block">
              调度器
              <DescriptionTooltip description="选择调度算法。贪心算法在资源可用时立即调度任务；启发式算法还会规划设备竞争时的分配顺序；CP-SAT 使用约束规划寻找最优调度。" />
            </Label>
            <Select value={config.scheduler} onValueChange={(v) => setConfig({ ...config, scheduler: v })}>
              <SelectTrigger id="scheduler" className="h-9 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="greedy">贪心</SelectItem>
                <SelectItem value="heuristic">启发式</SelectItem>
                <SelectItem value="cpsat">CP-SAT</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="jitter" className="mb-1 block">
              时长抖动
              <DescriptionTooltip description="任务耗时的随机波动幅度，例如 10% 表示最多波动 ±10%。任务耗时始终不低于 1 秒。" />
            </Label>
            <div className="relative">
              <Input
                id="jitter"
                type="number"
                min={0}
                step={1}
                value={Math.round(config.jitter * 100)}
                onChange={(e) => setConfig({ ...config, jitter: (parseFloat(e.target.value) || 0) / 100 })}
                className="h-9 text-sm pr-8"
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-sm text-gray-400 dark:text-gray-500 pointer-events-none">
                %
              </span>
            </div>
          </div>
          <div>
            <Label htmlFor="seed" className="mb-1 block">
              随机种子
              <DescriptionTooltip description="用于复现仿真结果的随机种子，留空时随机生成。" />
            </Label>
            <Input
              id="seed"
              type="number"
              value={config.seed ?? ''}
              onChange={(e) => setConfig({ ...config, seed: e.target.value ? parseInt(e.target.value) : null })}
              className="h-9 text-sm"
              placeholder="可选"
            />
          </div>
          <div className="flex items-end">
            <Button variant="primary" onClick={handleSubmit} disabled={loading} className="w-full h-9">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {loading ? '正在运行…' : '运行'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
