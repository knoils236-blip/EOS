'use client';

import * as React from 'react';
import { ChevronDown, ChevronRight, SlidersHorizontal } from 'lucide-react';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Textarea } from '@/components/ui/Textarea';
import { Button } from '@/components/ui/Button';
import {
  DomainEditor,
  RawInput,
  selectClass,
  parseDomainInputs,
  parseDomainOutputs,
  parseDomainConstraints,
  type DomainValue,
} from './DomainEditor';
import { updateOptimizerParams } from '../api/optimizer';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import type { OptimizerDefaults, OptimizerInfo } from '@/lib/types/api';
import { OptimizerParamFields } from './OptimizerParamFields';
import { Combobox, type ComboboxOption } from '@/components/ui/Combobox';
import { DescriptionTooltip } from '@/components/ui/DescriptionTooltip';

// ============================================================================
// AI Model Options
// ============================================================================

// OpenAI-compatible and Ollama model names are entered by the user.
const AI_MODEL_OPTIONS: ComboboxOption[] = [
  { value: 'claude-agent-sdk:fable', label: 'Claude Fable', group: 'Claude Agent SDK' },
  { value: 'claude-agent-sdk:opus', label: 'Claude Opus', group: 'Claude Agent SDK' },
  { value: 'claude-agent-sdk:sonnet', label: 'Claude Sonnet', group: 'Claude Agent SDK' },
];

const CLAUDE_AGENT_SDK_PREFIX = 'claude-agent-sdk:';
const OLLAMA_PREFIX = 'ollama:';
const OPENAI_PREFIX = 'openai:';
const SUPPORTED_MODEL_PREFIXES = [CLAUDE_AGENT_SDK_PREFIX, OLLAMA_PREFIX, OPENAI_PREFIX];

const isSupportedModel = (model: string) => SUPPORTED_MODEL_PREFIXES.some((p) => model.startsWith(p));

const EFFORT_LABELS = { low: '低', medium: '中', high: '高', xhigh: '很高', max: '最高' };
const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
type EffortLevel = (typeof EFFORT_LEVELS)[number];
const DEFAULT_EFFORT: EffortLevel = 'high';

// ============================================================================
// Types
// ============================================================================

interface RuntimeModeProps {
  mode: 'runtime';
  campaignName: string;
  optimizerInfo: OptimizerInfo;
  isRunning: boolean;
  onRefresh?: () => void;
}

interface SubmissionModeProps {
  mode: 'submission';
  defaults: OptimizerDefaults;
  isResume: boolean;
  overrides: Record<string, unknown>;
  onChange: (overrides: Record<string, unknown>) => void;
  /** For resume mode, the persisted domain from campaign.meta.beacon.optimizer_config */
  persistedDomain?: DomainValue | null;
}

interface EditorModeProps {
  mode: 'editor';
  defaults: OptimizerDefaults;
  onSave: (values: Record<string, unknown>, domain: DomainValue) => void | Promise<void>;
}

type OptimizerPanelProps = RuntimeModeProps | SubmissionModeProps | EditorModeProps;

/** Display a JSON value as a pretty-printed string, or empty string for null/undefined. */
function jsonDisplay(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

/** Parse a JSON string, returning null for empty/invalid input. Keeps raw string if not valid JSON. */
function jsonParse(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed; // Keep raw string — user may still be typing
  }
}

/**
 * Textarea for editing JSON values. Uses local state during editing to avoid
 * cursor jumps from round-tripping through jsonDisplay/jsonParse on every keystroke.
 * Syncs to the parent on blur.
 */
export function JsonTextarea({
  value,
  onChange,
  ...props
}: { value: unknown; onChange: (parsed: unknown) => void } & Omit<
  React.ComponentProps<typeof Textarea>,
  'value' | 'onChange'
>) {
  const [localText, setLocalText] = React.useState(() => jsonDisplay(value));
  const focusedRef = React.useRef(false);

  // Sync from parent when not focused (e.g. external reset)
  React.useEffect(() => {
    if (!focusedRef.current) setLocalText(jsonDisplay(value));
  }, [value]);

  return (
    <Textarea
      {...props}
      value={localText}
      onChange={(e) => setLocalText(e.target.value)}
      onFocus={() => {
        focusedRef.current = true;
      }}
      onBlur={() => {
        onChange(jsonParse(localText));
        focusedRef.current = false;
      }}
    />
  );
}

// ============================================================================
// Strategy Mix Slider (shared)
// ============================================================================

export function StrategyMixSlider({
  pAi,
  onChange,
  disabled,
}: {
  /** AI probability 0–1 (left = Bayesian, right = AI) */
  pAi: number;
  onChange: (pAi: number) => void;
  disabled?: boolean;
}) {
  const pAiPct = Math.round(pAi * 100);
  const pBayesianPct = 100 - pAiPct;

  return (
    <div className="space-y-2 px-2">
      {/* Labels sit above the track so it can span the full panel width */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-red-600">贝叶斯 {pBayesianPct}%</span>
        <span className="text-sm font-semibold text-purple-600">AI {pAiPct}%</span>
      </div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={pAi}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        disabled={disabled}
        className="strategy-slider block h-[18px] w-full cursor-pointer disabled:cursor-not-allowed"
      />
    </div>
  );
}

// ============================================================================
// Runtime Mode Component
// ============================================================================

function RuntimePanel({ campaignName, optimizerInfo, isRunning, onRefresh }: Omit<RuntimeModeProps, 'mode'>) {
  const { isConnected } = useOrchestratorConnected();
  const { runtime_params, is_beacon, param_schema } = optimizerInfo;

  const [pBayesian, setPBayesian] = React.useState(runtime_params.p_bayesian);
  const [historySize, setHistorySize] = React.useState(runtime_params.ai_history_size);
  const [additionalContext, setAdditionalContext] = React.useState(runtime_params.ai_additional_context ?? '');
  const [customParams, setCustomParams] = React.useState<Record<string, unknown>>({});
  const [isUpdating, setIsUpdating] = React.useState(false);
  const [saveError, setSaveError] = React.useState('');

  // Sync with props when they update
  React.useEffect(() => {
    setPBayesian(runtime_params.p_bayesian);
    setHistorySize(runtime_params.ai_history_size);
    setAdditionalContext(runtime_params.ai_additional_context ?? '');
    setCustomParams({});
  }, [runtime_params]);

  const dirtyCustomParams = React.useMemo(() => {
    return Object.fromEntries(Object.entries(customParams).filter(([k, v]) => v !== runtime_params[k]));
  }, [customParams, runtime_params]);

  const hasDirtyParams =
    Object.keys(dirtyCustomParams).length > 0 ||
    (is_beacon &&
      (pBayesian !== runtime_params.p_bayesian ||
        historySize !== runtime_params.ai_history_size ||
        additionalContext !== (runtime_params.ai_additional_context ?? '')));

  const handleSaveParams = async () => {
    const params: Record<string, unknown> = { ...dirtyCustomParams };
    if (is_beacon) {
      if (pBayesian !== runtime_params.p_bayesian) params.p_bayesian = pBayesian;
      if (historySize !== runtime_params.ai_history_size) params.ai_history_size = historySize;
      if (additionalContext !== (runtime_params.ai_additional_context ?? ''))
        params.ai_additional_context = additionalContext || undefined;
    }
    if (Object.keys(params).length === 0) return;
    setIsUpdating(true);
    setSaveError('');
    try {
      const result = await updateOptimizerParams(campaignName, params);
      if (result.success) onRefresh?.();
      else setSaveError(result.error ?? '保存优化器设置失败');
    } catch {
      setSaveError('无法保存优化器设置，请重试。');
    } finally {
      setIsUpdating(false);
    }
  };

  const disabled = !isRunning || isUpdating || !isConnected;

  return (
    <div className="space-y-4">
      {saveError && (
        <p role="alert" className="text-sm text-red-600">
          {saveError}
        </p>
      )}
      {is_beacon && (
        <>
          {/* Strategy Mix Slider */}
          <StrategyMixSlider pAi={1 - pBayesian} onChange={(pAi) => setPBayesian(1 - pAi)} disabled={disabled} />

          {/* AI Settings */}
          <div className="space-y-3">
            <Label className="text-sm font-semibold text-purple-600">
              AI
              <DescriptionTooltip description="Beacon 的 AI 设置：根据协议、运行历史和您的建议进行推理，提出参数方案。" />
            </Label>
            <div className="grid grid-cols-2 items-end gap-4">
              <div className="space-y-1">
                <Label className="text-xs">
                  历史记录数量
                  <DescriptionTooltip description="提供给 AI 作为上下文的历史协议运行数量。数量越多，可供推理的信息越多，但消耗的 token 也越多。" />
                </Label>
                <Input
                  type="number"
                  min={1}
                  value={historySize}
                  onChange={(e) => setHistorySize(parseInt(e.target.value) || 1)}
                  disabled={disabled}
                  className="text-xs h-8"
                />
              </div>
            </div>

            <div className="space-y-1">
              <Label className="text-xs">
                补充上下文
                <DescriptionTooltip description="每次生成建议时传给 AI 的补充说明，例如领域知识或无法通过优化域表达的约束。" />
              </Label>
              <Textarea
                value={additionalContext}
                onChange={(e) => setAdditionalContext(e.target.value)}
                placeholder="输入 AI 的补充上下文…"
                disabled={disabled}
                className="text-xs min-h-[60px]"
              />
            </div>
          </div>
        </>
      )}

      {param_schema.some((f) => f.runtime) && (
        <div className="space-y-3">
          <Label className="text-sm font-semibold">
            优化器参数
            <DescriptionTooltip description="该优化器通过 eos_param_schema() 声明的参数。" />
          </Label>
          <OptimizerParamFields
            schema={param_schema}
            values={{ ...runtime_params, ...customParams }}
            onChange={(key, value) => setCustomParams((prev) => ({ ...prev, [key]: value }))}
            disabled={disabled}
            runtimeOnly
          />
        </div>
      )}

      {/* Save Button */}
      {isRunning && (
        <Button
          variant="default"
          size="sm"
          onClick={handleSaveParams}
          disabled={!hasDirtyParams || isUpdating || !isConnected}
          className="w-full"
        >
          {isUpdating ? '正在保存…' : '保存更改'}
        </Button>
      )}
    </div>
  );
}

// ============================================================================
// Submission Mode Component
// ============================================================================

function SubmissionPanel({
  defaults,
  isResume,
  overrides,
  onChange,
  persistedDomain,
  onSave,
  defaultShowDomain = true,
}: Omit<SubmissionModeProps, 'mode'> & {
  onSave?: (values: Record<string, unknown>, domain: DomainValue) => void | Promise<void>;
  defaultShowDomain?: boolean;
}) {
  const params = defaults.params;

  const get = <T,>(key: string, fallback: T): T => {
    return key in overrides ? (overrides[key] as T) : fallback;
  };

  const set = (key: string, value: unknown) => {
    onChange({ ...overrides, [key]: value });
  };

  // Derive model + effort state for the Claude Agent SDK slider
  const currentModel = get<string>('ai_model', params.ai_model);
  const isClaudeAgentSdk = typeof currentModel === 'string' && currentModel.startsWith(CLAUDE_AGENT_SDK_PREFIX);
  const rawModelSettings = get<Record<string, unknown> | string | null>('ai_model_settings', params.ai_model_settings);
  const modelSettingsObj =
    rawModelSettings && typeof rawModelSettings === 'object' && !Array.isArray(rawModelSettings)
      ? (rawModelSettings as Record<string, unknown>)
      : null;
  const currentEffort = ((modelSettingsObj?.effort as EffortLevel | undefined) ?? DEFAULT_EFFORT) as EffortLevel;
  const setEffort = (level: EffortLevel) => {
    set('ai_model_settings', { ...(modelSettingsObj ?? {}), effort: level });
  };

  // Effort is only meaningful for Claude Agent SDK; drop it when switching to any other model.
  React.useEffect(() => {
    if (isClaudeAgentSdk || !modelSettingsObj || !('effort' in modelSettingsObj)) return;
    const { effort: _effort, ...rest } = modelSettingsObj;
    void _effort;
    set('ai_model_settings', Object.keys(rest).length > 0 ? rest : null);
  }, [isClaudeAgentSdk]); // eslint-disable-line react-hooks/exhaustive-deps

  // Domain state (Tier 3)
  const [domain, setDomain] = React.useState<DomainValue>(() => {
    if (isResume && persistedDomain) {
      return persistedDomain;
    }
    // Parse defaults into DomainValue
    return {
      inputs: (overrides.inputs as DomainValue['inputs']) ?? parseDomainInputs(defaults.inputs),
      outputs: (overrides.outputs as DomainValue['outputs']) ?? parseDomainOutputs(defaults.outputs),
      constraints:
        (overrides.constraints as DomainValue['constraints']) ?? parseDomainConstraints(defaults.constraints),
    };
  });

  const handleDomainChange = (newDomain: DomainValue) => {
    setDomain(newDomain);
    onChange({
      ...overrides,
      inputs: newDomain.inputs,
      outputs: newDomain.outputs,
      constraints: newDomain.constraints,
    });
  };

  const [showDomain, setShowDomain] = React.useState(defaultShowDomain);
  const [justSaved, setJustSaved] = React.useState(false);
  const [isSaving, setIsSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState<string | null>(null);

  return (
    <div className="space-y-4">
      {defaults.is_beacon && (
        <>
          {/* Strategy Mix Slider */}
          <StrategyMixSlider
            pAi={1 - get<number>('p_bayesian', params.p_bayesian)}
            onChange={(pAi) => set('p_bayesian', 1 - pAi)}
          />

          {/* Bayesian Settings */}
          <div className="space-y-3">
            <Label className="text-sm font-semibold text-red-600">
              贝叶斯
              <DescriptionTooltip description="Beacon 的贝叶斯设置：根据历史结果拟合代理模型，并通过优化采集函数选择参数。" />
            </Label>
            <div className="grid grid-cols-2 items-end gap-3">
              <div className="space-y-1">
                <Label className="text-xs">
                  初始样本数
                  <DescriptionTooltip description="代理模型接管前，通过初始采样方法生成的协议运行数量。数值越大，前期探索越充分。" />
                </Label>
                <Input
                  type="number"
                  min={0}
                  value={get<number>('num_initial_samples', params.num_initial_samples)}
                  onChange={(e) => set('num_initial_samples', parseInt(e.target.value) || 0)}
                  disabled={isResume}
                  className="text-xs h-8"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">
                  采样方法
                  <DescriptionTooltip description="初始样本的生成方式。Sobol 和拉丁超立方在优化域中均匀布点，均匀随机采样则随机取点。" />
                </Label>
                <select
                  value={get<string>('initial_sampling_method', params.initial_sampling_method)}
                  onChange={(e) => set('initial_sampling_method', e.target.value)}
                  className={selectClass}
                >
                  <option value="SOBOL">Sobol</option>
                  <option value="UNIFORM">均匀随机</option>
                  <option value="LHS">拉丁超立方</option>
                </select>
              </div>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">
                采集函数
                <DescriptionTooltip description="BoFire 采集函数决定下一次尝试的点，以包含 type 字段的 JSON 对象指定，例如 qLogNEI，用于权衡探索与利用。" />
              </Label>
              <JsonTextarea
                value={get<Record<string, unknown> | null>('acquisition_function', params.acquisition_function)}
                onChange={(v) => set('acquisition_function', v)}
                placeholder='{"type": "qLogNEI"}'
                className="text-xs min-h-[48px] font-mono"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">
                代理模型定义
                <DescriptionTooltip description="以 JSON 指定 BoFire 代理模型。留空时由 BoFire 为每个输出选择模型，通常为高斯过程。" />
              </Label>
              <JsonTextarea
                value={get<Record<string, unknown> | null>('surrogate_specs', params.surrogate_specs)}
                onChange={(v) => set('surrogate_specs', v)}
                placeholder="留空以自动选择代理模型"
                className="text-xs min-h-[48px] font-mono"
              />
            </div>
          </div>

          {/* AI Settings */}
          <div className="space-y-3">
            <Label className="text-sm font-semibold text-purple-600">
              AI
              <DescriptionTooltip description="Beacon 的 AI 设置：根据协议、运行历史和您的建议进行推理，提出参数方案。" />
            </Label>
            <div className="grid grid-cols-2 items-end gap-3">
              <div className="space-y-1">
                <Label className="text-xs">
                  历史记录数量
                  <DescriptionTooltip description="提供给 AI 作为上下文的历史协议运行数量。数量越多，可供推理的信息越多，但消耗的 token 也越多。" />
                </Label>
                <Input
                  type="number"
                  min={1}
                  value={get<number>('ai_history_size', params.ai_history_size)}
                  onChange={(e) => set('ai_history_size', parseInt(e.target.value) || 1)}
                  className="text-xs h-8"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">
                  重试次数
                  <DescriptionTooltip description="AI 建议超出优化域边界或违反约束时，重新请求建议的次数。" />
                </Label>
                <Input
                  type="number"
                  min={0}
                  value={get<number>('ai_retries', params.ai_retries)}
                  onChange={(e) => set('ai_retries', parseInt(e.target.value) || 0)}
                  className="text-xs h-8"
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">
                模型
                <DescriptionTooltip description="OpenAI 兼容服务使用 openai:模型名称；URL 和 API Key 在服务器配置。也支持 Claude Agent SDK 与 Ollama。" />
              </Label>
              <Combobox
                options={AI_MODEL_OPTIONS}
                value={currentModel}
                onChange={(v) => set('ai_model', v)}
                placeholder="选择模型…"
                searchPlaceholder="输入 openai:模型名称 或 ollama:模型名称…"
                allowCustomValue
                customValueHint="自定义模型"
                className="text-xs h-10"
              />
              {!isSupportedModel(currentModel) && (
                <p className="text-[11px] text-red-600 dark:text-red-400">
                  模型需以 {OPENAI_PREFIX}、{CLAUDE_AGENT_SDK_PREFIX} 或 {OLLAMA_PREFIX} 开头。
                </p>
              )}
            </div>
            {isClaudeAgentSdk && (
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <Label className="text-xs">
                    推理强度
                    <DescriptionTooltip description="Claude Agent SDK 的推理强度。较高强度有助于改善建议，但耗时更长。" />
                  </Label>
                  <span className="text-xs font-medium capitalize text-purple-600">{EFFORT_LABELS[currentEffort]}</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={EFFORT_LEVELS.length - 1}
                  step={1}
                  value={Math.max(0, EFFORT_LEVELS.indexOf(currentEffort))}
                  onChange={(e) => setEffort(EFFORT_LEVELS[parseInt(e.target.value)])}
                  className="w-full h-2 rounded-lg appearance-none cursor-pointer bg-gray-200 dark:bg-slate-600 accent-purple-600"
                />
                <div className="flex justify-between text-[10px] text-gray-500 dark:text-gray-400 px-0.5">
                  {EFFORT_LEVELS.map((l) => (
                    <span key={l} className="capitalize">
                      {l}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="space-y-1">
              <Label className="text-xs">
                模型设置
                <DescriptionTooltip description="以 JSON 对象指定额外模型设置，例如将 temperature 设为 0.3。" />
              </Label>
              <JsonTextarea
                // Effort is owned by the slider — hide it here and re-merge on write so typing can't override it.
                value={(() => {
                  if (!modelSettingsObj) return modelSettingsObj;
                  const { effort: _effort, ...rest } = modelSettingsObj;
                  void _effort;
                  return Object.keys(rest).length > 0 ? rest : null;
                })()}
                onChange={(v) => {
                  const next =
                    v && typeof v === 'object' && !Array.isArray(v) ? { ...(v as Record<string, unknown>) } : {};
                  if (modelSettingsObj && 'effort' in modelSettingsObj) {
                    next.effort = modelSettingsObj.effort;
                  }
                  set('ai_model_settings', Object.keys(next).length > 0 ? next : null);
                }}
                placeholder="{}"
                className="text-xs min-h-[48px] font-mono"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">
                附加参数
                <DescriptionTooltip description="提供给 AI 作为上下文但不纳入优化目标的额外任务输出，使用 TASK.PARAMETER 格式。" />
              </Label>
              <RawInput
                displayValue={(
                  get<string[] | null>('ai_additional_parameters', params.ai_additional_parameters) ?? []
                ).join(', ')}
                onRawChange={(text) => {
                  const val = text.trim();
                  set(
                    'ai_additional_parameters',
                    val
                      ? val
                          .split(',')
                          .map((s) => s.trim())
                          .filter(Boolean)
                      : null
                  );
                }}
                placeholder="例如 TaskName.param1, TaskName.param2"
                className="text-xs h-8"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">
                补充上下文
                <DescriptionTooltip description="每次生成建议时传给 AI 的补充说明，例如领域知识或无法通过优化域表达的约束。" />
              </Label>
              <Textarea
                value={get<string | null>('ai_additional_context', params.ai_additional_context) ?? ''}
                onChange={(e) => set('ai_additional_context', e.target.value || null)}
                placeholder="输入 AI 的补充上下文…"
                className="text-xs min-h-[60px]"
              />
            </div>
          </div>
        </>
      )}

      {defaults.param_schema.length > 0 && (
        <div className="space-y-3">
          <Label className="text-sm font-semibold">
            优化器参数
            <DescriptionTooltip description="该优化器通过 eos_param_schema() 声明的参数。" />
          </Label>
          <OptimizerParamFields
            schema={defaults.param_schema}
            values={{ ...params, ...overrides }}
            onChange={(key, value) => set(key, value)}
          />
        </div>
      )}

      {/* Tier 3: Domain Definition */}
      <div className="border-t border-gray-200 dark:border-slate-700 pt-3">
        <button
          type="button"
          onClick={() => setShowDomain(!showDomain)}
          className="flex items-center gap-1 text-sm font-medium text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white"
        >
          {showDomain ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          优化域定义
          {isResume && (
            <span className="ml-2 text-xs text-amber-600 dark:text-amber-400 font-normal">（恢复运行时只读）</span>
          )}
        </button>
        {showDomain && (
          <div className="mt-3">
            {isResume && (
              <p className="text-xs text-amber-600 dark:text-amber-400 mb-3">
                恢复运行时不能修改优化域，因为已有协议运行历史依赖该定义。
              </p>
            )}
            <DomainEditor value={domain} onChange={handleDomainChange} readOnly={isResume} />
          </div>
        )}
      </div>

      {/* Save Button (editor mode only) */}
      {onSave && (
        <div className="border-t border-gray-200 dark:border-slate-700 pt-3">
          {saveError && (
            <p role="alert" className="mb-2 text-sm text-red-600 dark:text-red-400">
              保存失败：{saveError}
            </p>
          )}
          <Button
            variant={justSaved ? 'default' : 'primary'}
            size="sm"
            disabled={isSaving}
            onClick={async () => {
              setIsSaving(true);
              setSaveError(null);
              setJustSaved(false);
              try {
                await onSave(overrides, domain);
                setJustSaved(true);
                setTimeout(() => setJustSaved(false), 2000);
              } catch (error) {
                setSaveError(error instanceof Error ? error.message : '请重试。');
              } finally {
                setIsSaving(false);
              }
            }}
            className="w-full"
          >
            {isSaving ? '正在保存…' : justSaved ? '已保存！' : '保存'}
          </Button>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function OptimizerPanel(props: OptimizerPanelProps) {
  const [expanded, setExpanded] = React.useState(props.mode === 'editor');

  // Editor mode: manage internal overrides state
  const [editorOverrides, setEditorOverrides] = React.useState<Record<string, unknown>>(() => {
    return props.mode === 'editor' ? { ...props.defaults.params } : {};
  });

  // If in editor mode, render without collapsible header
  if (props.mode === 'editor') {
    return (
      <SubmissionPanel
        defaults={props.defaults}
        isResume={false}
        overrides={editorOverrides}
        onChange={setEditorOverrides}
        onSave={(values, domain) => props.onSave(values, domain)}
      />
    );
  }

  const optimizerType = props.mode === 'runtime' ? props.optimizerInfo.optimizer_type : props.defaults.optimizer_type;
  const optimizerLabel = optimizerType === 'BeaconOptimizer' ? 'Beacon 优化器' : optimizerType;

  return (
    <div className="bg-white dark:bg-slate-900 rounded-lg border border-gray-200 dark:border-slate-700 p-4">
      <button type="button" onClick={() => setExpanded(!expanded)} className="w-full flex items-center gap-2 text-left">
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-gray-500" />
        ) : (
          <ChevronRight className="h-4 w-4 text-gray-500" />
        )}
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <SlidersHorizontal className="h-5 w-5" />
            {optimizerLabel}
          </h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {props.mode === 'runtime' ? '实时调整优化器参数' : '配置此实验计划的优化器参数'}
          </p>
        </div>
      </button>
      {expanded && (
        <div className="mt-4">
          {props.mode === 'runtime' ? (
            <RuntimePanel
              campaignName={props.campaignName}
              optimizerInfo={props.optimizerInfo}
              isRunning={props.isRunning}
              onRefresh={props.onRefresh}
            />
          ) : (
            <SubmissionPanel
              defaults={props.defaults}
              isResume={props.isResume}
              overrides={props.overrides}
              onChange={props.onChange}
              persistedDomain={props.persistedDomain}
              defaultShowDomain={false}
            />
          )}
        </div>
      )}
    </div>
  );
}
